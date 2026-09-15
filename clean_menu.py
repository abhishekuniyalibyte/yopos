#!/usr/bin/env python3
"""
Build a compact, AI-ready menu from the client's menu.json export.

    python3 clean_menu.py

    menu.json  +  menu_mapping.json   ->   menu_clean.json

menu.json is the client's source of truth and is never modified. menu_clean.json is
generated — re-run this script rather than hand-editing it. Our own editorial decisions
(meal-time tags, step labels, exclusions) live in menu_mapping.json so that what came
from the client stays separable from what we added.

Why this exists
---------------
The raw export is 2.3 MB, mostly audit columns and a map_Item_Category block that repeats
the same category object once per historical edit (406 rows for 137 items, most of them
soft-deleted). Stripped to what a chat assistant actually needs, the whole menu is ~46 KB
/ ~12k tokens, which fits in a system prompt with room to spare. That is why there is no
embedding or retrieval step here: with 137 items, top-k search would only ever show the
model a fraction of the menu and make it answer "what burgers do you have?" wrongly.

Assumptions baked in (documented because the client cannot be asked to confirm them)
-----------------------------------------------------------------------------------
1. web_enable == 1 selects live items; the `status` column is ignored. All 137 items are
   status 0 while web_enable is 1, so filtering on status would yield an empty menu.
2. web_price is the authoritative price.
3. Items priced 0.00 are withheld — see excluded_items in menu_mapping.json.
4. The current category is the last non-deleted map_Item_Category row.
5. No nutrition, calorie, allergen or dietary data is emitted or inferred. item_kcal is
   null for all 137 items and there are no ingredients, so any such value could only be
   guessed from the item name. See the dietary_policy block in the output.
"""

import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "menu.json"
MAPPING = BASE / "menu_mapping.json"
OUTPUT = BASE / "menu_clean.json"

# Emitted into menu_clean.json so the rule travels with the data rather than living only
# in whichever system prompt happens to load it. See README note above.
DIETARY_POLICY = {
    "has_nutrition_data": False,
    "rule": (
        "This menu contains no calorie, protein, carbohydrate, fat, allergen or ingredient "
        "data. Never state or imply such information, and never describe an item as healthy, "
        "light, low-calorie, high-protein, low-carb or diet-friendly. If asked, say the "
        "information is not available and refer the customer to the restaurant."
    ),
    "suggested_reply": (
        "I don't have nutrition or allergen information for our menu, so I can't tell you "
        "which items are highest in protein — I'd recommend checking with the restaurant "
        "directly. I can tell you what's on the menu, though."
    ),
}


def money(value):
    """Prices arrive as int, float or string depending on the row. Normalise to float."""
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def iter_items(raw):
    """Yield every item dict, flattening menu -> sub_category -> item_data."""
    for store in raw.get("all_Store", []):
        for menu in store.get("Category_data", []):
            for sub in menu.get("sub_category_data", []) or []:
                for item in sub.get("item_data") or []:
                    yield sub, item


def resolve_category(item, sub):
    """
    Current category = last map_Item_Category row without a deleted_at.

    That block carries one row per historical re-categorisation, so the live row is the
    surviving one; fall back to the sub-category name if every row is soft-deleted.
    """
    live = [r for r in (item.get("map_Item_Category") or []) if not r.get("deleted_at")]
    if live:
        name = (live[-1].get("category_data") or {}).get("name")
        if name:
            return name
    return sub.get("name")


def build_step_lookup(step_types):
    """Flatten {label: [option titles]} into {option title: label} for O(1) matching."""
    return {
        title.lower(): label
        for label, titles in step_types.items()
        for title in titles
    }


def label_step(options, lookup, default):
    """
    Infer a step's purpose from its option titles.

    Every step in the export is titled "Please Select" / "Choose 1", so the label carries
    no information. The option titles do: there are only 16 distinct ones. A step is
    labelled only when ALL its recognised options agree, so a mixed or unfamiliar step
    degrades to the generic prompt instead of being labelled wrongly.
    """
    labels = {lookup.get((o.get("title") or "").lower()) for o in options}
    labels.discard(None)
    return labels.pop() if len(labels) == 1 else default


def clean():
    for path in (SOURCE, MAPPING):
        if not path.exists():
            sys.exit(f"error: {path.name} not found in {BASE}")

    raw = json.loads(SOURCE.read_text())
    mapping = json.loads(MAPPING.read_text())

    meal_times = mapping.get("meal_times", {})
    default_label = mapping.get("default_step_label", "Please choose one")
    display_fixes = {k.lower(): v for k, v in mapping.get("display_fixes", {}).items()}
    excluded = {str(k) for k in mapping.get("excluded_items", {})}
    step_lookup = build_step_lookup(mapping.get("step_types", {}))

    items = []
    skipped = Counter()
    unmapped_categories = set()
    unmapped_options = set()

    for sub, item in iter_items(raw):
        item_id = item.get("item_id")

        if str(item_id) in excluded:
            skipped["excluded (zero price)"] += 1
            continue
        if item.get("deleted_at"):
            skipped["deleted"] += 1
            continue
        if item.get("web_enable") != 1:
            skipped["not web_enable"] += 1
            continue

        price = money(item.get("web_price"))
        if not price:
            # Safety net: any other zero/missing price we did not know to exclude.
            skipped["zero or missing price"] += 1
            continue

        category = resolve_category(item, sub)
        if category not in meal_times:
            unmapped_categories.add(category)

        entry = {
            "item_id": item_id,
            "name": item.get("name"),
            "category": category,
            "price": price,
            "meal_times": meal_times.get(category, []),
        }

        if item.get("description"):
            entry["description"] = item["description"]

        steps = []
        for step in item.get("item_step") or []:
            options = []
            for opt in step.get("option") or []:
                title = opt.get("title") or ""
                if title.lower() not in step_lookup:
                    unmapped_options.add(title)
                options.append({
                    # Preserved exactly as issued by the backend — this is the cart payload
                    # key, so it must survive any display-text correction.
                    "option_id": opt.get("item_step_option_id"),
                    "name": display_fixes.get(title.lower(), title),
                    "extra_price": money(opt.get("web_price")),
                })

            if not options:
                continue

            steps.append({
                "step_id": step.get("item_step_id"),
                "label": label_step(step.get("option") or [], step_lookup, default_label),
                "required": str(step.get("required")) == "1",
                "max_choices": step.get("max_count"),
                "options": options,
            })

        if steps:
            entry["steps"] = steps
        entry["needs_customisation"] = any(s["required"] for s in steps)

        items.append(entry)

    items.sort(key=lambda e: (e["category"] or "", e["name"] or ""))

    store = raw["all_Store"][0]["store_data"]
    store = store[0] if isinstance(store, list) else store
    settings = store.get("store_setting") or {}

    # Root start_time/end_time are null; real per-day hours live in the nested availability
    # rows, which repeat per edit — dedupe by day.
    hours = {}
    for block in store.get("store_available") or []:
        if block.get("status") != "open":
            continue
        for slot in block.get("availability") or []:
            day = slot.get("day_name")
            if day and day not in hours:
                hours[day] = f"{slot.get('from_time')} - {slot.get('to_time')}"

    output = {
        "store": {
            "name": store.get("store_name"),
            "address": store.get("address"),
            "phone": store.get("phone_number"),
            "postcode": store.get("postal_code"),
            "cook_time": store.get("cook_time"),
            "delivery_radius_miles": store.get("radius"),
            "delivery_fee": money(settings.get("delivery_fees")),
            "minimum_order": money(settings.get("minimum_order_fees")),
            # Prices are GBP: the address is Birmingham UK and every price reads as pounds.
            # country_currency holds a rupee symbol, which we treat as an export artefact.
            "currency": "GBP",
            "opening_hours": hours,
        },
        "dietary_policy": DIETARY_POLICY,
        "categories": sorted({e["category"] for e in items if e["category"]}),
        "item_count": len(items),
        "items": items,
    }

    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    raw_kb = SOURCE.stat().st_size / 1024
    out_kb = OUTPUT.stat().st_size / 1024
    print(f"wrote {OUTPUT.name}: {len(items)} items, {len(output['categories'])} categories")
    print(f"  {raw_kb:,.0f} KB -> {out_kb:,.0f} KB  ({100 - 100 * out_kb / raw_kb:.1f}% smaller)")
    print(f"  ~{len(json.dumps(output)) // 4:,} tokens — fits in a system prompt, no RAG needed")
    print(f"  {sum(1 for e in items if e.get('needs_customisation'))} items need required choices")

    for reason, count in sorted(skipped.items()):
        print(f"  skipped {count}: {reason}")
    if unmapped_categories:
        print(f"  WARNING: no meal_times mapping for: {', '.join(sorted(unmapped_categories))}")
    if unmapped_options:
        print(f"  WARNING: unrecognised option titles: {', '.join(sorted(unmapped_options))}")


if __name__ == "__main__":
    clean()

"""
Tool schemas exposed to the model, and the dispatcher that runs them.

Schemas are in OpenAI function-calling format, which is what Groq's API expects.

The model chooses arguments; this module executes nothing until cart.py has validated
them against the menu. Tool errors are returned to the model as ordinary results rather
than raised, so it can correct itself — usually by asking the customer the question it
skipped — instead of the turn failing.
"""

from __future__ import annotations

from typing import Any

from .cart import Cart, CartError
from .menu import Menu

# Tool results are sent back as input tokens on the *next* hop, and Groq's free tier caps
# input at 7k tokens/minute. Returning 25 full items cost ~5.5k per hop, so two hops in a
# minute could not fit and no amount of retrying helped. Eight trimmed items keeps a hop
# near 1-2k, which leaves room for a real conversation.
SEARCH_LIMIT = 8

# detail="names" drops choices, which are most of a result's weight, so a page can hold
# far more. 30 keeps a page under ~600 tokens on the real menu and still returns the
# largest category (Burgers, 27) whole. 40 did not fit — measured, not guessed.
NAMES_LIMIT = 30

# Pages fetched with offset > 0 within one customer message. One lets "show me more"
# work; more than one is the model paging through a listing on its own.
MAX_EXTRA_PAGES_PER_TURN = 1


def tool_definitions(menu: Menu) -> list[dict[str, Any]]:
    """Schemas are built per-menu so the category enum always matches real data."""
    functions = [
        {
            "name": "search_menu",
            "description": (
                "Search the menu by name, category, meal time or maximum price. "
                'detail="full" (default) returns up to 8 items with their required '
                'choices; detail="names" returns up to 30 with just id, name and price '
                "for listing, or a breakdown by category if more than 30 match. If "
                "next_offset is present there are more matches: tell the customer, and "
                "fetch that page only if they ask. Pass item_id to fetch one exact "
                "item with its choices before add_to_cart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "integer",
                        "description": "Fetch exactly this item, with its choices. Other filters are ignored.",
                    },
                    "detail": {"type": "string", "enum": ["full", "names"], "default": "full"},
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Skip this many matches; use next_offset from the previous result.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Text matched against item name and category, e.g. 'chicken'",
                    },
                    "category": {"type": "string", "enum": menu.categories},
                    "meal_time": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                    "max_price": {"type": "number", "description": "Highest price to include"},
                },
            },
        },
        {
            "name": "add_to_cart",
            "description": (
                "Add an item to the cart. If the item has required choices you must supply "
                "one entry per choice, naming the step and the option exactly as "
                "search_menu gave them — e.g. "
                '{\"step\": \"Sauce choice\", \"option\": \"BBQ Sauce\"}. '
                "Do not guess a choice on the customer's behalf; ask them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "quantity": {"type": "integer", "minimum": 1, "default": 1},
                    "choices": {
                        "type": "array",
                        "description": (
                            "One entry per required choice, using the exact step and "
                            "option names from search_menu's choices_required."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {
                                    "type": "string",
                                    "description": 'Step name, e.g. "Sauce choice"',
                                },
                                "option": {
                                    "type": "string",
                                    "description": 'Chosen option, e.g. "BBQ Sauce"',
                                },
                            },
                            "required": ["step", "option"],
                        },
                    },
                },
                "required": ["item_id"],
            },
        },
        {
            "name": "view_cart",
            "description": "Show the current cart contents and total.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "remove_from_cart",
            "description": "Remove a line from the cart by its line number, as shown by view_cart.",
            "parameters": {
                "type": "object",
                "properties": {"line": {"type": "integer", "minimum": 1}},
                "required": ["line"],
            },
        },
        {
            "name": "clear_cart",
            "description": "Empty the cart completely. Confirm with the customer first.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]

    return [{"type": "function", "function": f} for f in functions]


def _compact(item) -> dict[str, Any]:
    """
    One search hit, trimmed to what the model needs to talk about it and add it.

    Required steps carry their real option NAMES. An earlier version sent only the step
    label to save tokens, and the model — asked "what sauce?" before it ever attempted an
    add — filled the gap by inventing plausible options (ketchup, garlic, lettuce) that
    are not on this menu. The cart still refused them, but the customer had already been
    offered food that does not exist.

    Option ids and prices stay out: they are only needed at add_to_cart, which returns
    them when it refuses an incomplete add. Names alone are cheap and stop the guessing.
    """
    out: dict[str, Any] = {
        "item_id": item.item_id,
        "name": item.name,
        "price": item.price,
    }
    if item.required_steps:
        out["choices_required"] = {
            step.label: [o.name for o in step.options] for step in item.required_steps
        }
    return out


def _name_only(item) -> dict[str, Any]:
    """
    One hit for a listing. No choices: they are only needed once the customer has picked
    an item, and the model fetches them then with search_menu(item_id=...).
    """
    return {"item_id": item.item_id, "name": item.name, "price": item.price}


def run_tool(
    name: str,
    payload: dict[str, Any],
    menu: Menu,
    cart: Cart,
    turn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute one tool call. Never raises: failures come back as {"error": ...}.

    `turn` is per-customer-message state shared across the tool calls it triggers; the
    agent passes a fresh dict each turn so limits like one extra page per reply hold.
    """
    payload = payload or {}
    try:
        if name == "search_menu":
            # An exact lookup: the step between "customer picked one from a list" and
            # "ask them about sauce". A name search could page the chosen item out of view.
            if payload.get("item_id") is not None:
                item = menu.get(int(payload["item_id"]))
                if item is None:
                    return {
                        "error": f"There is no item with id {payload['item_id']}. "
                        "Use search_menu to find a real item_id."
                    }
                return {"count": 1, "items": [_compact(item)]}

            detail = payload.get("detail") or "full"
            if detail not in ("full", "names"):
                return {"error": f'detail must be "full" or "names", not {detail!r}.'}
            limit = NAMES_LIMIT if detail == "names" else SEARCH_LIMIT
            offset = max(int(payload.get("offset") or 0), 0)

            # Each extra page stays in history and is re-sent on every later hop; paging
            # all 135 items in one turn measured ~17k input tokens against a 7k/minute
            # cap. The prompt says to wait for the customer; this makes it a guarantee.
            if offset > 0 and turn is not None:
                if turn.get("pages", 0) >= MAX_EXTRA_PAGES_PER_TURN:
                    return {
                        "error": "Only one more page per reply. Show the customer what "
                        "you have and offer to show more."
                    }
                turn["pages"] = turn.get("pages", 0) + 1

            filters = {
                "query": payload.get("query"),
                "category": payload.get("category"),
                "meal_time": payload.get("meal_time"),
                "max_price": payload.get("max_price"),
            }
            items, total = menu.search(**filters, limit=limit, offset=offset)

            if detail == "names" and total > NAMES_LIMIT:
                # Too many to list in one result. Every category fits (the largest is
                # 27), so break the matches down by category and let the customer pick —
                # decided by match count, not by which filters were passed, because
                # max_price=1000 or query="" match the whole menu just as well as no filter.
                everything, _ = menu.search(**filters, limit=len(menu))
                counts: dict[str, int] = {}
                for item in everything:
                    counts[item.category] = counts.get(item.category, 0) + 1
                return {
                    "count": total,
                    "categories": counts,
                    "note": "Too many to list. Tell the customer how these split by "
                    "category and ask which one to show.",
                }
            shape = _name_only if detail == "names" else _compact
            result: dict[str, Any] = {
                "count": total,
                "offset": offset,
                "items": [shape(i) for i in items],
            }
            if offset + len(items) < total:
                result["next_offset"] = offset + len(items)
                result["note"] = (
                    f"Showing {offset + 1}-{offset + len(items)} of {total} matches. "
                    f"Tell the customer there are more; fetch the next page "
                    f"(offset={offset + len(items)}) only if they ask."
                )
            return result

        if name == "add_to_cart":
            return cart.add(
                item_id=payload.get("item_id"),
                quantity=payload.get("quantity", 1),
                choices=payload.get("choices") or [],
            )

        if name == "view_cart":
            return cart.state()

        if name == "remove_from_cart":
            return cart.remove(payload.get("line"))

        if name == "clear_cart":
            return cart.clear()

        return {"error": f"Unknown tool: {name}"}

    except CartError as exc:
        return exc.as_result()
    except (TypeError, ValueError, KeyError) as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}

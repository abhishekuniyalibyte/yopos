"""
Tests against the real menu_clean.json.

These guard the assumptions the assistant depends on: that the file loads, that no
nutrition data has crept in, and that prompt size stays in the range where inlining the
whole menu is the right call.
"""

from __future__ import annotations

import json

import pytest

from server import config
from server.menu import Menu
from server.prompts import build_system_prompt

pytestmark = pytest.mark.skipif(
    not config.MENU_PATH.exists(), reason="menu_clean.json not generated yet"
)


@pytest.fixture(scope="module")
def menu() -> Menu:
    return Menu.load(config.MENU_PATH)


def test_menu_loads(menu: Menu) -> None:
    assert len(menu) > 100
    assert menu.categories


def test_no_zero_priced_items(menu: Menu) -> None:
    """clean_menu.py drops these; a bot adding a free item to a cart looks broken."""
    assert [i.name for i in menu if i.price <= 0] == []


def test_every_item_has_a_category_with_meal_times(menu: Menu) -> None:
    missing = [i.name for i in menu if not i.meal_times]
    assert missing == [], f"no meal_times for: {missing[:5]}"


def test_option_ids_are_unique_within_a_step(menu: Menu) -> None:
    for item in menu:
        for step in item.steps:
            ids = [o.option_id for o in step.options]
            assert len(ids) == len(set(ids)), f"duplicate option ids on {item.name}"


def test_required_steps_always_offer_options(menu: Menu) -> None:
    """A required step with no options would be unaddable by construction."""
    for item in menu:
        for step in item.required_steps:
            assert step.options, f"{item.name} has an unanswerable required step"


def test_no_nutrition_fields_leak_into_the_menu_payload(menu: Menu) -> None:
    """
    The menu has no nutrition data. If a field ever appears, the strict prompt rule
    silently becomes a lie — so fail loudly here instead.
    """
    blob = json.dumps(menu.prompt_payload()).lower()
    for term in ("kcal", "calorie", "protein", "carb", "allergen", "ingredient"):
        assert term not in blob, f"unexpected nutrition field in menu payload: {term}"


def test_typo_is_fixed_in_display_text(menu: Menu) -> None:
    names = {o.name for i in menu for s in i.steps for o in s.options}
    assert "BBQ Saucce" not in names
    assert "BBQ Sauce" in names


def test_prompt_stays_within_a_tight_per_minute_token_budget(menu: Menu) -> None:
    """
    The system prompt is sent on every call, and Groq limits tokens *per minute* (8k on a
    free key) — a far tighter constraint than the model's 131k context window. Inlining
    all 135 items cost ~20k tokens and failed on the first request. The prompt now carries
    a category index instead, and the model retrieves detail via search_menu.

    ~4 chars per token. If this regresses, the per-minute budget breaks again.
    """
    approx_tokens = len(build_system_prompt(menu)) / 4
    assert approx_tokens < 2_000, f"prompt grew to ~{approx_tokens:,.0f} tokens"


def test_prompt_does_not_inline_the_item_list(menu: Menu) -> None:
    """The item list must stay out of the prompt; search_menu is how the model sees it."""
    prompt = build_system_prompt(menu)
    named = sum(1 for item in menu if item.name in prompt)
    assert named < 5, f"{named} item names leaked into the system prompt"


def test_prompt_lists_every_category(menu: Menu) -> None:
    """The model needs to know what exists in order to route a search."""
    prompt = build_system_prompt(menu)
    for category in menu.categories:
        assert category in prompt, f"category missing from prompt: {category}"


def test_prompt_tells_the_model_to_search_first(menu: Menu) -> None:
    assert "ALWAYS call search_menu" in build_system_prompt(menu)


def test_strict_nutrition_rule_is_the_default(menu: Menu) -> None:
    prompt = build_system_prompt(menu, allow_nutrition_inference=False)
    assert "NUTRITION AND ALLERGENS — STRICT" in prompt
    assert "never infer nutrition" in prompt.lower()


def test_inference_mode_still_refuses_allergen_questions(menu: Menu) -> None:
    prompt = build_system_prompt(menu, allow_nutrition_inference=True)
    assert "never answer an allergen question" in prompt.lower()


def test_prompt_states_the_customisation_count(menu: Menu) -> None:
    assert str(menu.customisable_count) in build_system_prompt(menu)


# --- Paging and listing, against the real menu ---------------------------------------

from server.cart import Cart  # noqa: E402
from server.tools import NAMES_LIMIT, SEARCH_LIMIT, run_tool  # noqa: E402

# Filters that match far more than one names result can hold. The first four match the
# whole menu, which is how a guard keyed on "was a filter passed?" was bypassed.
BROAD = [
    {},
    {"max_price": 1000},
    {"query": ""},
    {"meal_time": "dinner"},
    {"max_price": 5},
    {"query": "chicken"},
]


def _approx_tokens(result: dict) -> float:
    """Same estimate as test_search_results_stay_small: characters / 4."""
    return len(json.dumps(result)) / 4


def _walk(menu: Menu, payload: dict) -> list[int]:
    """Follow next_offset to the end and collect every item_id seen."""
    cart, seen, offset = Cart(menu), [], 0
    while True:
        out = run_tool("search_menu", {**payload, "offset": offset}, menu, cart)
        seen += [i["item_id"] for i in out["items"]]
        if "next_offset" not in out:
            return seen
        assert out["next_offset"] > offset, "paging must always move forward"
        offset = out["next_offset"]


@pytest.mark.parametrize("filters", [{}, {"category": "Burgers"}, {"query": "chicken"}])
def test_full_mode_paging_visits_every_match_exactly_once(menu: Menu, filters: dict) -> None:
    seen = _walk(menu, filters)
    expected, _ = menu.search(**filters, limit=len(menu))
    assert len(seen) == len(set(seen)), "an item appeared on two pages"
    assert set(seen) == {i.item_id for i in expected}, "an item was skipped"


@pytest.mark.parametrize("filters", BROAD)
def test_broad_names_search_returns_a_category_breakdown(menu: Menu, filters: dict) -> None:
    out = run_tool("search_menu", {**filters, "detail": "names"}, menu, Cart(menu))
    assert "items" not in out and "next_offset" not in out
    assert out["count"] > NAMES_LIMIT
    assert sum(out["categories"].values()) == out["count"]
    assert _approx_tokens(out) < 150


@pytest.mark.parametrize("filters", BROAD)
def test_every_category_of_a_broad_search_lists_in_one_call(menu: Menu, filters: dict) -> None:
    """The breakdown is only useful if following it up always fits in one result."""
    cart = Cart(menu)
    breakdown = run_tool("search_menu", {**filters, "detail": "names"}, menu, cart)
    for category, n in breakdown["categories"].items():
        out = run_tool(
            "search_menu", {**filters, "category": category, "detail": "names"}, menu, cart
        )
        assert len(out["items"]) == n and "next_offset" not in out
        assert _approx_tokens(out) < 600


def test_every_burger_comes_back_in_one_names_call(menu: Menu) -> None:
    out = run_tool("search_menu", {"category": "Burgers", "detail": "names"}, menu, Cart(menu))
    assert out["count"] == len(out["items"]) >= 27
    assert "next_offset" not in out


def test_default_search_stays_under_the_token_budget(menu: Menu) -> None:
    out = run_tool("search_menu", {}, menu, Cart(menu))
    assert _approx_tokens(out) < 600


def test_page_notes_do_not_tell_the_model_to_keep_paging(menu: Menu) -> None:
    out = run_tool("search_menu", {}, menu, Cart(menu))
    assert "only if they ask" in out["note"]
    assert "Call again" not in out["note"]


def test_an_item_beyond_the_first_page_can_be_fetched_exactly(menu: Menu) -> None:
    """The case item_id lookup exists for: the chosen item is past the first 8 matches."""
    matches, total = menu.search(category="Burgers", limit=len(menu))
    assert total > SEARCH_LIMIT
    target = matches[-1]
    out = run_tool("search_menu", {"item_id": target.item_id}, menu, Cart(menu))
    assert [i["item_id"] for i in out["items"]] == [target.item_id]
    if target.required_steps:
        assert "choices_required" in out["items"][0]


def test_prompt_forbids_paging_several_pages_in_one_reply(menu: Menu) -> None:
    prompt = build_system_prompt(menu)
    assert "never several pages in one" in prompt
    assert "whole menu" in prompt

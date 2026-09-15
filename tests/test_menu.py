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

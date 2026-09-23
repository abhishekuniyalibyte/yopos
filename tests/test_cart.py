"""
Cart validation tests.

This is the layer that stands between a model's output and a real order, so the cases
that matter most are the refusals: an invented item, an option borrowed from another
step, a required choice the model skipped.
"""

from __future__ import annotations

import pytest

from server.cart import Cart, CartError, InMemoryCart
from server.menu import Menu
from server.tools import run_tool


@pytest.fixture
def menu() -> Menu:
    return Menu(
        {
            "store": {"name": "Test Kitchen", "currency": "GBP"},
            "categories": ["Wraps", "Drinks"],
            "items": [
                {
                    "item_id": 1,
                    "name": "Chicken Wrap",
                    "category": "Wraps",
                    "price": 6.50,
                    "meal_times": ["lunch", "dinner"],
                    "steps": [
                        {
                            "step_id": 10,
                            "label": "Sauce choice",
                            "required": True,
                            "max_choices": 1,
                            "options": [
                                {"option_id": 100, "name": "Mayo", "extra_price": None},
                                {"option_id": 101, "name": "Chilli Sauce", "extra_price": None},
                            ],
                        },
                        {
                            "step_id": 11,
                            "label": "Salad choice",
                            "required": True,
                            "max_choices": 1,
                            "options": [
                                {"option_id": 110, "name": "Salad", "extra_price": None},
                                {"option_id": 111, "name": "No Salad", "extra_price": None},
                            ],
                        },
                    ],
                },
                {
                    "item_id": 2,
                    "name": "Cola Can",
                    "category": "Drinks",
                    "price": 1.20,
                    "meal_times": ["breakfast", "lunch", "dinner"],
                    "steps": [],
                },
                {
                    "item_id": 3,
                    "name": "Upgrade Box",
                    "category": "Wraps",
                    "price": 8.00,
                    "meal_times": ["dinner"],
                    "steps": [
                        {
                            "step_id": 30,
                            "label": "Drink choice",
                            "required": False,
                            "max_choices": 1,
                            "options": [{"option_id": 300, "name": "Lemon", "extra_price": 1.50}],
                        }
                    ],
                },
            ],
        }
    )


@pytest.fixture
def cart(menu: Menu) -> Cart:
    return Cart(menu, InMemoryCart())


# --------------------------------------------------------------------- refusals

def test_unknown_item_is_refused(cart: Cart) -> None:
    with pytest.raises(CartError) as exc:
        cart.add(item_id=999)
    assert "no item with id 999" in str(exc.value).lower()


def test_missing_required_choice_is_refused_with_the_options(cart: Cart) -> None:
    with pytest.raises(CartError) as exc:
        cart.add(item_id=1)

    # The assistant needs the step back so it can ask the customer, not guess again.
    detail = exc.value.detail["missing_step"]
    assert detail["label"] == "Sauce choice"
    assert {o["name"] for o in detail["options"]} == {"Mayo", "Chilli Sauce"}


def test_partially_answered_item_is_refused(cart: Cart) -> None:
    with pytest.raises(CartError) as exc:
        cart.add(item_id=1, choices=[{"step_id": 10, "option_id": 100}])
    assert exc.value.detail["missing_step"]["label"] == "Salad choice"


def test_option_from_another_step_is_refused(cart: Cart) -> None:
    """Option 110 is real, but it belongs to the salad step, not the sauce step."""
    with pytest.raises(CartError) as exc:
        cart.add(
            item_id=1,
            choices=[{"step_id": 10, "option_id": 110}, {"step_id": 11, "option_id": 110}],
        )
    assert "not one of" in str(exc.value)


def test_invented_option_id_is_refused(cart: Cart) -> None:
    with pytest.raises(CartError):
        cart.add(
            item_id=1,
            choices=[{"step_id": 10, "option_id": 9999}, {"step_id": 11, "option_id": 110}],
        )


def test_absurd_quantity_is_refused(cart: Cart) -> None:
    with pytest.raises(CartError):
        cart.add(item_id=2, quantity=500)


def test_removing_a_line_that_does_not_exist_is_refused(cart: Cart) -> None:
    with pytest.raises(CartError):
        cart.remove(line=1)


# ---------------------------------------------------------------- happy paths

def test_valid_add_records_choices_and_total(cart: Cart) -> None:
    result = cart.add(
        item_id=1,
        choices=[{"step_id": 10, "option_id": 101}, {"step_id": 11, "option_id": 111}],
    )
    assert result["added"] == "Chicken Wrap"
    assert result["unit_price"] == 6.50
    assert result["choices"] == ["Sauce choice: Chilli Sauce", "Salad choice: No Salad"]
    assert cart.total == 6.50


def test_item_without_required_steps_adds_directly(cart: Cart) -> None:
    assert cart.add(item_id=2)["added"] == "Cola Can"
    assert cart.total == 1.20


def test_optional_step_is_not_required_but_is_honoured(cart: Cart) -> None:
    assert cart.add(item_id=3)["unit_price"] == 8.00
    priced = cart.add(item_id=3, choices=[{"step_id": 30, "option_id": 300}])
    assert priced["unit_price"] == 9.50  # the one option on this menu that costs extra


def test_quantity_multiplies_the_total(cart: Cart) -> None:
    cart.add(item_id=2, quantity=3)
    assert cart.total == 3.60


def test_identical_lines_merge(cart: Cart) -> None:
    cart.add(item_id=2)
    cart.add(item_id=2)
    state = cart.state()
    assert len(state["lines"]) == 1
    assert state["lines"][0]["quantity"] == 2


def test_different_choices_do_not_merge(cart: Cart) -> None:
    cart.add(item_id=1, choices=[{"step_id": 10, "option_id": 100}, {"step_id": 11, "option_id": 110}])
    cart.add(item_id=1, choices=[{"step_id": 10, "option_id": 101}, {"step_id": 11, "option_id": 110}])
    assert len(cart.state()["lines"]) == 2


def test_remove_and_clear(cart: Cart) -> None:
    cart.add(item_id=2)
    cart.add(item_id=2, quantity=1)
    cart.remove(line=1)
    assert cart.total == 0.0
    cart.add(item_id=2)
    cart.clear()
    assert cart.state()["item_count"] == 0


# ---------------------------------------------------------------------- tools

def test_run_tool_returns_errors_instead_of_raising(menu: Menu, cart: Cart) -> None:
    """The model has to be able to recover from a bad call, so errors come back as data."""
    out = run_tool("add_to_cart", {"item_id": 1}, menu, cart)
    assert "error" in out
    assert out["missing_step"]["label"] == "Sauce choice"


def test_search_filters(menu: Menu, cart: Cart) -> None:
    assert run_tool("search_menu", {"query": "chicken"}, menu, cart)["count"] == 1
    assert run_tool("search_menu", {"category": "Drinks"}, menu, cart)["count"] == 1
    assert run_tool("search_menu", {"meal_time": "breakfast"}, menu, cart)["count"] == 1
    assert run_tool("search_menu", {"max_price": 2.00}, menu, cart)["count"] == 1


def test_search_returns_the_real_option_names(menu: Menu, cart: Cart) -> None:
    """
    The model asks the customer which sauce BEFORE it attempts an add, so it needs the
    real option names in the search result. Without them it invented options that are not
    on the menu (ketchup, lettuce) and offered them to customers.
    """
    item = run_tool("search_menu", {"query": "chicken"}, menu, cart)["items"][0]
    assert item["choices_required"] == {
        "Sauce choice": ["Mayo", "Chilli Sauce"],
        "Salad choice": ["Salad", "No Salad"],
    }


def test_search_omits_option_ids_and_prices(menu: Menu, cart: Cart) -> None:
    """Ids and prices are only needed at add_to_cart, which supplies them on refusal."""
    item = run_tool("search_menu", {"query": "chicken"}, menu, cart)["items"][0]
    assert "option_id" not in str(item)
    assert "extra_price" not in str(item)


def test_search_results_stay_small(menu: Menu, cart: Cart) -> None:
    """
    Tool results come back as input tokens on the next hop, against a 7k/minute ceiling.
    A result that balloons breaks the conversation, so keep the payload honest.
    """
    import json

    blob = json.dumps(run_tool("search_menu", {}, menu, cart))
    assert len(blob) / 4 < 600, f"search result grew to ~{len(blob)//4} tokens"


def test_names_mode_never_carries_choices(menu: Menu, cart: Cart) -> None:
    out = run_tool("search_menu", {"detail": "names", "max_price": 100}, menu, cart)
    assert out["items"]
    for item in out["items"]:
        assert set(item) == {"item_id", "name", "price"}


def test_only_one_extra_page_per_turn(menu: Menu, cart: Cart) -> None:
    """The prompt says wait for the customer before paging; this makes it a guarantee."""
    turn: dict = {}
    assert "error" not in run_tool("search_menu", {"offset": 1}, menu, cart, turn)
    assert "error" in run_tool("search_menu", {"offset": 2}, menu, cart, turn)


def test_first_pages_do_not_count_against_the_page_limit(menu: Menu, cart: Cart) -> None:
    turn: dict = {}
    for _ in range(3):
        assert "error" not in run_tool("search_menu", {"query": "cola"}, menu, cart, turn)
    assert "error" not in run_tool("search_menu", {"offset": 1}, menu, cart, turn)


def test_page_limit_resets_on_a_new_turn(menu: Menu, cart: Cart) -> None:
    run_tool("search_menu", {"offset": 1}, menu, cart, {})
    assert "error" not in run_tool("search_menu", {"offset": 1}, menu, cart, {})


def test_lookup_by_item_id_returns_that_item_with_choices(menu: Menu, cart: Cart) -> None:
    out = run_tool("search_menu", {"item_id": 1, "query": "cola"}, menu, cart)
    assert out["count"] == 1
    assert out["items"][0]["item_id"] == 1
    assert "Sauce choice" in out["items"][0]["choices_required"]


def test_lookup_of_unknown_item_id_is_an_error(menu: Menu, cart: Cart) -> None:
    assert "error" in run_tool("search_menu", {"item_id": 999}, menu, cart)


def test_offset_past_the_end_returns_nothing_but_the_true_count(menu: Menu, cart: Cart) -> None:
    out = run_tool("search_menu", {"offset": 50}, menu, cart)
    assert out["items"] == []
    assert out["count"] == len(menu)
    assert "next_offset" not in out


def test_unknown_detail_level_is_an_error(menu: Menu, cart: Cart) -> None:
    assert "error" in run_tool("search_menu", {"detail": "everything"}, menu, cart)


def test_unknown_tool_is_reported(menu: Menu, cart: Cart) -> None:
    assert "error" in run_tool("place_order", {}, menu, cart)


# ------------------------------------------------- choosing options by name

def test_choices_can_be_given_by_name(cart: Cart) -> None:
    """
    search_menu returns option NAMES, not ids. Requiring ids forced the model to submit a
    doomed add just to read the ids out of the refusal — a wasted call per step.
    """
    result = cart.add(
        item_id=1,
        choices=[
            {"step": "Sauce choice", "option": "Chilli Sauce"},
            {"step": "Salad choice", "option": "No Salad"},
        ],
    )
    assert result["choices"] == ["Sauce choice: Chilli Sauce", "Salad choice: No Salad"]


def test_name_matching_ignores_case_and_spacing(cart: Cart) -> None:
    cart.add(
        item_id=1,
        choices=[
            {"step": "sauce  CHOICE", "option": "  mayo "},
            {"step": "Salad choice", "option": "salad"},
        ],
    )
    assert cart.total == 6.50


def test_option_ids_still_work(cart: Cart) -> None:
    """The old id form must keep working; the model may use either."""
    cart.add(
        item_id=1,
        choices=[{"step_id": 10, "option_id": 100}, {"step_id": 11, "option_id": 110}],
    )
    assert cart.total == 6.50


def test_id_and_name_forms_can_be_mixed(cart: Cart) -> None:
    cart.add(
        item_id=1,
        choices=[{"step_id": 10, "option": "Mayo"}, {"step": "Salad choice", "option_id": 110}],
    )
    assert cart.total == 6.50


def test_an_option_that_is_not_on_the_menu_is_refused_by_name(cart: Cart) -> None:
    """"Ketchup" is not on this menu, however plausible it sounds."""
    with pytest.raises(CartError) as exc:
        cart.add(
            item_id=1,
            choices=[
                {"step": "Sauce choice", "option": "Ketchup"},
                {"step": "Salad choice", "option": "No Salad"},
            ],
        )
    assert "not one of" in str(exc.value)


def test_an_option_borrowed_from_another_step_is_refused_by_name(cart: Cart) -> None:
    with pytest.raises(CartError):
        cart.add(
            item_id=1,
            choices=[
                {"step": "Sauce choice", "option": "No Salad"},
                {"step": "Salad choice", "option": "No Salad"},
            ],
        )


def test_an_unknown_step_name_leaves_the_step_unanswered(cart: Cart) -> None:
    with pytest.raises(CartError) as exc:
        cart.add(
            item_id=1,
            choices=[
                {"step": "Topping choice", "option": "Mayo"},
                {"step": "Salad choice", "option": "Salad"},
            ],
        )
    assert exc.value.detail["missing_step"]["label"] == "Sauce choice"

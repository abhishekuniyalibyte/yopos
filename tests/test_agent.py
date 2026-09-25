"""
Agent tool-loop tests, with a scripted model standing in for Groq.

The live Groq path is still untested; these check what the agent enforces regardless of
what the model decides.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from server import config
from server.agent import Assistant
from server.cart import Cart
from server.menu import Menu

pytestmark = pytest.mark.skipif(
    not config.MENU_PATH.exists(), reason="menu_clean.json not generated yet"
)


def _search(call_id: str, **args) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "search_menu", "arguments": json.dumps(args)},
            }
        ],
    }


def _run(script: list[dict], turns: int = 1) -> list[dict]:
    """Play `script` as the model's replies; return the tool results the agent produced."""
    menu = Menu.load(config.MENU_PATH)
    assistant = Assistant(menu, api_key="test-key")
    replies = iter(script)

    async def fake_call(messages, client, tool_choice="auto"):
        return next(replies)

    assistant._call_with_retry = fake_call  # type: ignore[method-assign]
    history: list[dict] = []
    cart = Cart(menu)
    for _ in range(turns):
        asyncio.run(assistant.respond("show me more", history, cart))
    return [json.loads(m["content"]) for m in history if m["role"] == "tool"]


def test_model_cannot_page_through_a_listing_in_one_turn() -> None:
    done = {"role": "assistant", "content": "Here you go."}
    results = _run([_search("a", offset=8), _search("b", offset=16), done])
    assert "error" not in results[0]
    assert "error" in results[1]


def test_each_customer_turn_gets_its_own_extra_page() -> None:
    done = {"role": "assistant", "content": "Here you go."}
    results = _run([_search("a", offset=8), done, _search("b", offset=16), done], turns=2)
    assert all("error" not in r for r in results)


def _turn(script: list[dict], turns: int = 1):
    """Play `script` over `turns` customer messages; return the menu and last result."""
    menu = Menu.load(config.MENU_PATH)
    assistant = Assistant(menu, api_key="test-key")
    replies = iter(script)

    async def fake_call(messages, client, tool_choice="auto"):
        return next(replies)

    assistant._call_with_retry = fake_call  # type: ignore[method-assign]
    history: list[dict] = []
    cart = Cart(menu)
    for _ in range(turns):
        result = asyncio.run(assistant.respond("burgers?", history, cart))
    return menu, result


def _say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _burgers(menu: Menu) -> list:
    items, _ = menu.search(category="Burgers", limit=4)
    return items


LIST_BURGERS = _search("s", category="Burgers", detail="names")


def test_items_line_becomes_tiles_and_leaves_the_reply() -> None:
    menu = Menu.load(config.MENU_PATH)
    a, b, *_ = _burgers(menu)
    _, result = _turn([LIST_BURGERS, _say(f"Two of our burgers:\nITEMS: {b.item_id}, {a.item_id}")])

    assert "ITEMS" not in result.reply
    assert [t["item_id"] for t in result.items] == [b.item_id, a.item_id]
    assert result.items[0]["name"] == b.name and result.items[0]["price"] == b.price


def test_tiles_only_show_items_search_has_returned() -> None:
    """A real id the model never retrieved is still an item it has not seen."""
    menu = Menu.load(config.MENU_PATH)
    a = _burgers(menu)[0]
    unsearched = next(i for i in menu if i.category != "Burgers")
    reply = _say(f"Here:\nITEMS: {a.item_id}, {unsearched.item_id}, 999999")
    _, result = _turn([LIST_BURGERS, reply])

    assert [t["item_id"] for t in result.items] == [a.item_id]


def test_tiles_use_a_search_from_an_earlier_turn() -> None:
    """ "Show me the names" is often answered from the last turn's search, unsearched."""
    menu = Menu.load(config.MENU_PATH)
    a, b, *_ = _burgers(menu)
    script = [LIST_BURGERS, _say("We have 27 burgers."), _say(f"Here:\nITEMS: {a.item_id}, {b.item_id}")]
    _, result = _turn(script, turns=2)

    assert [t["item_id"] for t in result.items] == [a.item_id, b.item_id]


def test_tiles_fall_back_to_named_items_without_an_items_line() -> None:
    menu = Menu.load(config.MENU_PATH)
    a = _burgers(menu)[0]
    _, result = _turn([LIST_BURGERS, _say(f"The {a.name} is {a.price}.")])

    assert [t["item_id"] for t in result.items] == [a.item_id]


def test_a_longer_name_does_not_also_tile_the_shorter_one() -> None:
    menu = Menu.load(config.MENU_PATH)
    meal = next(i for i in menu if i.name == "Double Cheeseburger Meal")
    _, result = _turn([LIST_BURGERS, _say(f"The {meal.name} is £{meal.price:.2f}.")])

    assert [t["item_id"] for t in result.items] == [meal.item_id]


def test_a_listing_the_tiles_repeat_is_cut_from_the_text() -> None:
    menu = Menu.load(config.MENU_PATH)
    items = _burgers(menu)
    listing = ", ".join(f"{i.name} £{i.price:.2f}" for i in items)
    reply = f"Here are our burgers:\n\n{listing}.\n\nWhich one would you like?"
    _, result = _turn([LIST_BURGERS, _say(reply)])

    assert result.reply == "Here are our burgers:\n\nWhich one would you like?"
    assert len(result.items) == len(items)


def test_a_lead_in_before_a_colon_survives_the_cut() -> None:
    menu = Menu.load(config.MENU_PATH)
    items = _burgers(menu)
    listing = ", ".join(i.name for i in items)
    _, result = _turn([LIST_BURGERS, _say(f"Four burgers under £4: {listing}. Want one?")])

    assert result.reply == "Four burgers under £4: Want one?"


def test_a_question_naming_items_is_kept() -> None:
    menu = Menu.load(config.MENU_PATH)
    a, b, c, _ = _burgers(menu)
    question = f"Would you like the {a.name}, the {b.name} or the {c.name}?"
    _, result = _turn([LIST_BURGERS, _say(question)])

    assert result.reply == question and len(result.items) == 3


def test_a_cart_confirmation_gets_no_tiles() -> None:
    menu = Menu.load(config.MENU_PATH)
    item = next(i for i in menu if not i.required_steps)
    add = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "add",
                "type": "function",
                "function": {"name": "add_to_cart", "arguments": json.dumps({"item_id": item.item_id})},
            }
        ],
    }
    search = _search("s", item_id=item.item_id)
    _, result = _turn([search, add, _say(f"Added the {item.name}.\nITEMS: {item.item_id}")])

    assert result.items == [] and result.reply == f"Added the {item.name}."


def test_no_search_means_no_tiles() -> None:
    menu = Menu.load(config.MENU_PATH)
    a = _burgers(menu)[0]
    _, result = _turn([_say(f"Try this.\nITEMS: {a.item_id}")])

    assert result.items == [] and result.reply == "Try this."


def test_category_in_matches_singular_plural_and_ampersand() -> None:
    menu = Menu.load(config.MENU_PATH)
    assert menu.category_in("do we have burger?") == "Burgers"
    assert menu.category_in("any STARTERS") == "Starters"
    assert menu.category_in("got a pasty?") == "Pies & Pasties"
    assert menu.category_in("what boxes are there") == "Boxes"
    assert menu.category_in("hi there") is None
    assert menu.category_in("burgers or wraps?") is None  # ambiguous: let the model choose


def test_naming_a_category_forces_a_search_on_the_first_call_only() -> None:
    menu = Menu.load(config.MENU_PATH)
    assistant = Assistant(menu, api_key="test-key")
    replies = iter([_search("s", category="Starters", detail="names"), _say("Here you go.")])
    choices = []

    async def fake_call(messages, client, tool_choice="auto"):
        choices.append(tool_choice)
        return next(replies)

    assistant._call_with_retry = fake_call  # type: ignore[method-assign]
    asyncio.run(assistant.respond("do we have starters", [], Cart(menu)))

    assert choices[0] == {"type": "function", "function": {"name": "search_menu"}}
    assert choices[1] == "auto"


def test_a_names_listing_is_tiled_even_without_an_items_line() -> None:
    menu = Menu.load(config.MENU_PATH)
    _, total = menu.search(category="Burgers")
    _, result = _turn([LIST_BURGERS, _say(f"We have {total} burgers. Which one would you like?")])

    assert len(result.items) == total

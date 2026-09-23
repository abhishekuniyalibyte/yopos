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

    async def fake_call(messages, client):
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

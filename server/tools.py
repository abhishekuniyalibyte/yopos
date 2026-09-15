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


def tool_definitions(menu: Menu) -> list[dict[str, Any]]:
    """Schemas are built per-menu so the category enum always matches real data."""
    functions = [
        {
            "name": "search_menu",
            "description": (
                "Search the menu by name, category, meal time or maximum price. Returns "
                "matching items with their item_id, price and any required choices. Call "
                "this before add_to_cart to get real ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {
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
                "one option_id for each required step_id — search_menu lists them. Do not "
                "guess a choice on the customer's behalf; ask them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "quantity": {"type": "integer", "minimum": 1, "default": 1},
                    "choices": {
                        "type": "array",
                        "description": "One entry per required step",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_id": {"type": "integer"},
                                "option_id": {"type": "integer"},
                            },
                            "required": ["step_id", "option_id"],
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


def run_tool(name: str, payload: dict[str, Any], menu: Menu, cart: Cart) -> dict[str, Any]:
    """Execute one tool call. Never raises: failures come back as {"error": ...}."""
    payload = payload or {}
    try:
        if name == "search_menu":
            items, total = menu.search(
                query=payload.get("query"),
                category=payload.get("category"),
                meal_time=payload.get("meal_time"),
                max_price=payload.get("max_price"),
                limit=SEARCH_LIMIT,
            )
            result: dict[str, Any] = {
                "count": total,
                "items": [_compact(i) for i in items],
            }
            if total > len(items):
                result["note"] = (
                    f"Showing {len(items)} of {total} matches. Tell the customer there "
                    f"are more and offer to narrow it down, or search again with a "
                    f"category or max_price."
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

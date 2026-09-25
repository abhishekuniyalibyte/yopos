"""
The agent loop: call the model, run whatever tools it asks for, feed the results back,
repeat until it answers in plain text.

Groq speaks the OpenAI Chat Completions format:

  - the system prompt is the first message, role "system"
  - the model requests tools via `tool_calls` on an assistant message, with the arguments
    as a JSON *string*
  - each result goes back as its own message with role "tool" and a matching
    tool_call_id

Tool arguments arrive as strings, and an open-weight model will occasionally emit one that
does not parse. That is handled here as a normal tool error so the model can retry, rather
than failing the turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config
from .cart import Cart
from .keys import KeyPool
from .menu import Menu
from .prompts import build_system_prompt
from .tools import run_tool, tool_definitions

log = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """A failure worth surfacing to the caller as a clean message."""


class RateLimited(AgentError):
    """
    A 429 from Groq.

    Two very different conditions share this status code, so they are distinguished here:

      per_day=False  a short input-tokens-per-minute throttle, typically clearing in
                     well under a second. Worth retrying in-process.
      per_day=True   the model's daily token budget is spent. Retrying is pointless;
                     the fix is switching MODEL or waiting for the reset.
    """

    def __init__(self, message: str, per_day: bool, retry_after: float) -> None:
        super().__init__(message)
        self.per_day = per_day
        self.retry_after = retry_after


def _is_daily_cap(detail: str) -> bool:
    """Groq names the window in the message: 'tokens per day (TPD)' vs '... (ITPM)'."""
    lowered = detail.lower()
    return "per day" in lowered or "tpd" in lowered


def _retry_after(response: httpx.Response) -> float:
    """
    How long to wait before retrying a throttled call.

    Groq's `retry-after` header is whole seconds and rounds a 780ms wait up to the next
    minute, so prefer the precise figure in the error message when it is present.
    """
    import re

    match = re.search(r"try again in ([\d.]+)\s*(ms|s)", _error_message(response), re.I)
    if match:
        value = float(match.group(1))
        return value / 1000 if match.group(2).lower() == "ms" else value

    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return 1.0


def _error_message(response: httpx.Response) -> str:
    """Pull Groq's own error text out of a failed response, falling back to raw body."""
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]


@dataclass
class ToolTrace:
    """What the assistant did during a turn — useful in the UI and in logs."""

    name: str
    input: dict[str, Any]
    ok: bool


@dataclass
class TurnResult:
    reply: str
    cart: dict[str, Any]
    tools: list[ToolTrace] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)


# The model ends a reply with "ITEMS: 3120, 3121" to have the widget show those items as
# tiles. Tolerant of brackets and "#", since an open model will not type it identically
# every time.
_ITEMS_LINE = re.compile(r"^[ \t]*\[?ITEMS:([\d,# \t]*)\]?[ \t]*$", re.I | re.M)

# Tiles per reply. Matches NAMES_LIMIT, the most one listing can return.
MAX_TILES = 30

# A sentence naming this many tiled items is a list the tiles already show.
LISTING_NAMES = 3

# After one of these succeeds the cart panel shows what changed, and a reply like "Added
# the Zinger Burger Meal and a Single Cheese Burger" is a confirmation, not a list.
CART_TOOLS = {"add_to_cart", "remove_from_cart", "clear_cart"}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Assistant:
    """Stateless with respect to conversations; history is passed in per turn."""

    def __init__(self, menu: Menu, api_key: str | None = None) -> None:
        self._menu = menu
        self._keys = KeyPool.from_values([api_key] if api_key else config.GROQ_API_KEYS)
        self._tools = tool_definitions(menu)
        self._system = build_system_prompt(menu, config.ALLOW_NUTRITION_INFERENCE)

    @property
    def keys(self) -> KeyPool:
        return self._keys

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        client: httpx.AsyncClient,
        api_key: str,
        tool_choice: Any = "auto",
    ) -> dict[str, Any]:
        response = await client.post(
            f"{config.GROQ_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
            json={
                "model": config.MODEL,
                "max_tokens": config.MAX_TOKENS,
                "temperature": config.TEMPERATURE,
                # The system prompt carries the whole menu and is identical every call.
                "messages": [{"role": "system", "content": self._system}] + messages,
                "tools": self._tools,
                "tool_choice": tool_choice,
            },
        )

        if response.status_code == 401:
            raise AgentError("Groq rejected the API key.")
        if response.status_code == 429:
            # Groq returns 429 for both a short per-minute throttle and an exhausted
            # daily token budget, and the two need completely different responses.
            # Always surface its own message rather than guessing which one it was.
            detail = _error_message(response)
            log.error("groq rate limit: %s", detail)
            raise RateLimited(detail, per_day=_is_daily_cap(detail), retry_after=_retry_after(response))
        if response.status_code == 400:
            # Usually an unknown model id or a malformed tool schema — worth the detail.
            detail = _error_message(response)
            log.error("groq rejected the request: %s", detail)
            raise AgentError(f"Groq rejected the request: {detail}")
        if response.status_code == 404:
            detail = _error_message(response)
            log.error("groq 404: %s", detail)
            raise AgentError(f"Model not available: {detail}")
        if response.status_code >= 500:
            raise AgentError("Groq is unavailable. Try again shortly.")
        if response.status_code != 200:
            log.error("groq error %s: %s", response.status_code, response.text[:500])
            raise AgentError("The assistant could not process that request.")

        payload = response.json()
        if not payload.get("choices"):
            raise AgentError("The assistant returned an empty response.")
        return payload["choices"][0]["message"]

    async def _call_with_retry(
        self,
        messages: list[dict[str, Any]],
        client: httpx.AsyncClient,
        tool_choice: Any = "auto",
    ) -> dict[str, Any]:
        """
        Ride out Groq's rate limits.

        Both limits are per-organization, so BOTH are worth moving off a key for — the
        difference is only how long that key stands down:

          daily cap (TPD)   the account is done for the day; stand down for an hour.
          per-minute (ITPM) the window is momentarily full; stand down for the seconds
                            Groq names, then the key rejoins the rotation.

        Rotating on the short throttle is the important part. One key hitting its minute
        ceiling while the others sit idle should never reach a customer, and waiting on
        the throttled key when a fresh one is available just wastes the customer's time.
        Sleeping is the last resort, used only when every key is cooling off at once.
        """
        attempts = config.RATE_LIMIT_RETRIES + len(self._keys)
        spent_message = (
            "Every Groq key has hit its daily token limit. Try again later, or switch "
            "MODEL — each model has its own daily budget."
        )

        for attempt in range(attempts):
            key = self._keys.current()

            if key is None:
                # Nothing usable this instant. If it is only the short throttle, the
                # soonest key is seconds away and worth waiting for.
                wait = self._keys.seconds_until_free()
                if wait is None or wait > config.MAX_THROTTLE_WAIT_SECONDS:
                    raise AgentError(spent_message if wait is None else
                                     "All keys are rate limited. Try again shortly.")
                log.info("all keys cooling off, waiting %.2fs", wait)
                await asyncio.sleep(wait + 0.25)
                continue

            try:
                return await self._call_model(messages, client, key.value, tool_choice)
            except RateLimited as exc:
                if exc.per_day:
                    if not self._keys.retire(key) and self._keys.seconds_until_free() is None:
                        raise AgentError(spent_message) from exc
                    continue

                # Short throttle: stand this key down for exactly as long as Groq says,
                # and let the next key take the call immediately.
                self._keys.cool_off(key, exc.retry_after)
                if attempt == attempts - 1:
                    raise

        raise AgentError("Groq is throttling requests. Try again shortly.")

    async def respond(
        self,
        message: str,
        history: list[dict[str, Any]],
        cart: Cart,
    ) -> TurnResult:
        """Run one user message to completion, mutating `history` in place."""
        if not len(self._keys):
            raise AgentError("No Groq API key is set on the server.")

        history.append({"role": "user", "content": message})
        traces: list[ToolTrace] = []
        replies: list[str] = []
        turn: dict[str, Any] = {}  # per-message tool limits; see run_tool
        listed: list[int] = []  # item ids a detail="names" search returned this turn

        # "Do you have starters?" is answerable from the category index in the prompt,
        # and the model reliably answers it from there — a count and a price range, no
        # items, so nothing to show as tiles. The prompt forbids it and it still happens,
        # so when the message names a category the first call must search.
        choice: Any = "auto"
        if self._menu.category_in(message):
            choice = {"type": "function", "function": {"name": "search_menu"}}

        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(config.MAX_TOOL_HOPS):
                reply = await self._call_with_retry(history, client, choice)
                choice = "auto"
                history.append(reply)

                if reply.get("content"):
                    replies.append(reply["content"].strip())

                calls = reply.get("tool_calls") or []
                if not calls:
                    break

                for call in calls:
                    name = call["function"]["name"]
                    raw = call["function"].get("arguments") or "{}"
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        # Open models sometimes emit malformed JSON; let it retry.
                        log.warning("unparsable tool arguments for %s: %s", name, raw[:200])
                        output = {"error": f"Arguments for {name} were not valid JSON."}
                        parsed = {}
                    else:
                        output = run_tool(name, parsed, self._menu, cart, turn)
                        if name == "search_menu" and parsed.get("detail") == "names":
                            listed += [i["item_id"] for i in output.get("items", [])]

                    traces.append(ToolTrace(name, parsed, "error" not in output))
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": name,
                            "content": json.dumps(output, separators=(",", ":")),
                        }
                    )
            else:
                log.warning("hit MAX_TOOL_HOPS without a final reply")
                replies.append("Sorry — I got stuck working that out. Could you rephrase it?")

        cart_changed = any(t.ok and t.name in CART_TOOLS for t in traces)
        text, items = self._tiles(
            "\n\n".join(r for r in replies if r), _searched_ids(history), listed, cart_changed
        )
        _trim(history)

        return TurnResult(
            reply=text or "Sorry, I didn't catch that — could you rephrase?",
            cart=cart.state(),
            tools=traces,
            items=items,
        )

    def _tiles(
        self, text: str, searched: set[int], listed: list[int], cart_changed: bool
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Strip the ITEMS line from the reply and turn it into tiles.

        Only ids search_menu has returned in this conversation survive, and every tile is
        built from the menu itself — the model picks which items to show, never what a
        tile says. Earlier turns count: "show me the names" is often answered from the
        previous turn's search, and that is exactly when the tiles are wanted. If the
        model forgot the line, show the searched items its reply names outright, or
        failing that this turn's names listing — a listing exists to be shown.
        """
        requested: list[int] = []
        for match in _ITEMS_LINE.finditer(text):
            requested += [int(n) for n in re.findall(r"\d+", match.group(1))]
        text = re.sub(r"\n{3,}", "\n\n", _ITEMS_LINE.sub("", text)).strip()

        if cart_changed:
            return text, []

        known = [item for i in searched if (item := self._menu.get(i))]
        if not requested:
            requested = [item_id for _, item_id in _mentions(text, known)] or listed

        tiles: list[dict[str, Any]] = []
        for item_id in dict.fromkeys(requested):
            item = self._menu.get(item_id)
            if item is None or item_id not in searched:
                continue
            tiles.append(
                {
                    "item_id": item.item_id,
                    "name": item.name,
                    "price": item.price,
                    "category": item.category,
                    "has_choices": bool(item.required_steps),
                }
            )
        tiles = tiles[:MAX_TILES]

        if tiles:
            shown = [self._menu.get(t["item_id"]) for t in tiles]
            text = _drop_listings(text, shown) or "Here's what we have:"
        return text, tiles


def _searched_ids(history: list[dict[str, Any]]) -> set[int]:
    """Every item id search_menu has returned in the conversation still held."""
    ids: set[int] = set()
    for message in history:
        if message.get("role") != "tool" or message.get("name") != "search_menu":
            continue
        try:
            ids.update(i["item_id"] for i in json.loads(message["content"]).get("items", []))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return ids


def _mentions(text: str, items: list) -> list[tuple[int, int]]:
    """
    (position, item_id) for each item named in `text`, in reading order.

    Longest names match first and are masked, so "Double Cheeseburger Meal" does not also
    count as "Double Cheeseburger".
    """
    found: list[tuple[int, int]] = []
    for item in sorted(items, key=lambda i: -len(i.name)):
        pattern = re.compile(r"(?<!\w)" + re.escape(item.name) + r"(?!\w)", re.I)
        found += [(m.start(), item.item_id) for m in pattern.finditer(text)]
        text = pattern.sub(lambda m: "\0" * len(m.group()), text)
    return sorted(found)


def _drop_listings(text: str, items: list) -> str:
    """
    Remove sentences that list the tiled items, keeping the lead-in and any question.

    The model is asked to leave the list to the tiles and often writes it out anyway —
    27 burgers as a wall of prose above 27 tiles. A sentence naming several tiled items
    goes; if it opened with a lead-in ("Four chicken items under £3: ..."), that part
    stays. Questions are kept whole: dropping one would leave the customer without it.
    """
    lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in _SENTENCE_END.split(line):
            named = _mentions(sentence, items)
            if len({i for _, i in named}) < LISTING_NAMES or sentence.rstrip().endswith("?"):
                kept.append(sentence)
                continue
            colon = sentence.find(":")
            if 0 <= colon < named[0][0]:
                kept.append(sentence[: colon + 1])
        lines.append(" ".join(kept).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _trim(history: list[dict[str, Any]]) -> None:
    """
    Bound history growth without splitting an assistant tool_calls message from the tool
    results that answer it — the API rejects a dangling tool result.
    """
    limit = config.MAX_HISTORY_MESSAGES
    if len(history) <= limit:
        return

    cut = len(history) - limit
    while cut < len(history) and history[cut].get("role") == "tool":
        cut += 1

    del history[:cut]

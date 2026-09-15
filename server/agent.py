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
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config
from .cart import Cart
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


class Assistant:
    """Stateless with respect to conversations; history is passed in per turn."""

    def __init__(self, menu: Menu, api_key: str | None = None) -> None:
        self._menu = menu
        self._api_key = api_key or config.GROQ_API_KEY
        self._tools = tool_definitions(menu)
        self._system = build_system_prompt(menu, config.ALLOW_NUTRITION_INFERENCE)

    async def _call_model(
        self, messages: list[dict[str, Any]], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        response = await client.post(
            f"{config.GROQ_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key}",
            },
            json={
                "model": config.MODEL,
                "max_tokens": config.MAX_TOKENS,
                "temperature": config.TEMPERATURE,
                # The system prompt carries the whole menu and is identical every call.
                "messages": [{"role": "system", "content": self._system}] + messages,
                "tools": self._tools,
                "tool_choice": "auto",
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
        self, messages: list[dict[str, Any]], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """
        Ride out Groq's short per-minute throttle.

        A tool result can push the conversation over the input-tokens-per-minute ceiling
        mid-turn, and that clears in under a second — so retrying is far better than
        failing the customer's message. A spent daily budget is not retried: no amount of
        waiting inside one request will fix it.
        """
        for attempt in range(config.RATE_LIMIT_RETRIES):
            try:
                return await self._call_model(messages, client)
            except RateLimited as exc:
                # A long wait means the window is genuinely full rather than momentarily
                # busy. Sleeping through it would leave the customer staring at a spinner
                # and would not free capacity, so fail fast and let them retry.
                too_long = exc.retry_after > config.MAX_THROTTLE_WAIT_SECONDS
                if exc.per_day or too_long or attempt == config.RATE_LIMIT_RETRIES - 1:
                    raise
                delay = min(exc.retry_after + 0.25, config.MAX_THROTTLE_WAIT_SECONDS)
                log.info("throttled, retrying in %.2fs", delay)
                await asyncio.sleep(delay)
        raise AgentError("Groq is throttling requests. Try again shortly.")

    async def respond(
        self,
        message: str,
        history: list[dict[str, Any]],
        cart: Cart,
    ) -> TurnResult:
        """Run one user message to completion, mutating `history` in place."""
        if not self._api_key:
            raise AgentError("GROQ_API_KEY is not set on the server.")

        history.append({"role": "user", "content": message})
        traces: list[ToolTrace] = []
        replies: list[str] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(config.MAX_TOOL_HOPS):
                reply = await self._call_with_retry(history, client)
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
                        output = run_tool(name, parsed, self._menu, cart)

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

        _trim(history)

        return TurnResult(
            reply="\n\n".join(r for r in replies if r)
            or "Sorry, I didn't catch that — could you rephrase?",
            cart=cart.state(),
            tools=traces,
        )


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

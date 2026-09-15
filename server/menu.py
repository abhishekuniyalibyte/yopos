"""
Load menu_clean.json and answer questions about it.

This is the single source of truth for what exists on the menu. Every id the model
produces is checked against this before anything touches a cart, so a hallucinated item
or an option that belongs to a different step fails here rather than downstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class MenuError(RuntimeError):
    """Raised when the menu file is missing or unusable."""


@dataclass(frozen=True)
class Option:
    option_id: int
    name: str
    extra_price: float

    @property
    def as_prompt(self) -> dict[str, Any]:
        return {"option_id": self.option_id, "name": self.name}


@dataclass(frozen=True)
class Step:
    step_id: int
    label: str
    required: bool
    max_choices: int | None
    options: tuple[Option, ...]

    def option(self, option_id: int) -> Option | None:
        return next((o for o in self.options if o.option_id == option_id), None)

    @property
    def as_prompt(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "label": self.label,
            "options": [o.as_prompt for o in self.options],
        }


@dataclass(frozen=True)
class Item:
    item_id: int
    name: str
    category: str
    price: float
    meal_times: tuple[str, ...]
    steps: tuple[Step, ...]
    description: str | None = None

    @property
    def required_steps(self) -> tuple[Step, ...]:
        return tuple(s for s in self.steps if s.required)

    def step(self, step_id: int) -> Step | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def summary(self) -> dict[str, Any]:
        """The shape handed back to the model from search_menu."""
        out: dict[str, Any] = {
            "item_id": self.item_id,
            "name": self.name,
            "category": self.category,
            "price": self.price,
            "meal_times": list(self.meal_times),
        }
        if self.description:
            out["description"] = self.description
        if self.required_steps:
            out["required_choices"] = [s.as_prompt for s in self.required_steps]
        return out


class Menu:
    """An immutable view over the cleaned menu."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.store: dict[str, Any] = data.get("store", {})
        self.dietary_policy: dict[str, Any] = data.get("dietary_policy", {})
        self.categories: list[str] = list(data.get("categories", []))
        self._items: dict[int, Item] = {}

        for raw in data.get("items", []):
            item = Item(
                item_id=raw["item_id"],
                name=raw["name"],
                category=raw.get("category") or "",
                price=float(raw["price"]),
                meal_times=tuple(raw.get("meal_times") or ()),
                description=raw.get("description"),
                steps=tuple(
                    Step(
                        step_id=s["step_id"],
                        label=s.get("label") or "Please choose one",
                        required=bool(s.get("required")),
                        max_choices=s.get("max_choices"),
                        options=tuple(
                            Option(
                                option_id=o["option_id"],
                                name=o["name"],
                                extra_price=float(o.get("extra_price") or 0.0),
                            )
                            for o in s.get("options", [])
                        ),
                    )
                    for s in raw.get("steps", [])
                ),
            )
            self._items[item.item_id] = item

        if not self._items:
            raise MenuError("menu contains no items")

    @classmethod
    def load(cls, path: Path) -> "Menu":
        if not path.exists():
            raise MenuError(
                f"{path.name} not found. Generate it first: python3 clean_menu.py"
            )
        try:
            return cls(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            raise MenuError(f"{path.name} is not valid JSON: {exc}") from exc

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Item]:
        return iter(self._items.values())

    def get(self, item_id: int) -> Item | None:
        return self._items.get(item_id)

    @property
    def customisable_count(self) -> int:
        return sum(1 for i in self if i.required_steps)

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        meal_time: str | None = None,
        max_price: float | None = None,
        limit: int = 25,
    ) -> tuple[list[Item], int]:
        """
        Filter the menu. Returns (page, total_matches) so the model can be told when its
        query was too broad instead of silently seeing a truncated list.
        """
        results = list(self)

        if query:
            q = query.lower()
            results = [i for i in results if q in i.name.lower() or q in i.category.lower()]
        if category:
            c = category.lower()
            results = [i for i in results if i.category.lower() == c]
        if meal_time:
            m = meal_time.lower()
            results = [i for i in results if m in i.meal_times]
        if max_price is not None:
            results = [i for i in results if i.price <= max_price]

        results.sort(key=lambda i: (i.category, i.name))
        return results[:limit], len(results)

    def prompt_payload(self) -> list[dict[str, Any]]:
        """
        The full menu, as served by /api/menu and used in tests.

        This is deliberately NOT in the system prompt. Inlining all 135 items costs ~20k
        tokens per call, and Groq keys are limited on tokens *per minute* (8k on a free
        key), so a full-menu prompt fails on the first request no matter how large the
        model's context window is. The model uses search_menu instead.
        """
        return [i.summary() for i in self]

    def category_summary(self) -> str:
        """
        A compact index of the menu for the system prompt: one line per category with the
        item count and price range. Roughly 300 tokens instead of 20k, enough for the
        model to know what exists and route to search_menu for the detail.
        """
        by_category: dict[str, list[Item]] = {}
        for item in self:
            by_category.setdefault(item.category, []).append(item)

        lines = []
        for category in sorted(by_category):
            items = by_category[category]
            low = min(i.price for i in items)
            high = max(i.price for i in items)
            price = f"£{low:.2f}" if low == high else f"£{low:.2f}–£{high:.2f}"
            lines.append(f"- {category}: {len(items)} items, {price}")
        return "\n".join(lines)

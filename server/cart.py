"""
Cart state and the adapter that will eventually write to YOPOS.

Two layers, deliberately separated:

  CartBackend    where the cart actually lives.
  Cart           validation. Runs before the backend is ever called.

The validation is the part that matters and it does not change when the backend does.
Every item id, step id and option id the model supplies is checked against the menu, so
an invented item or a missing required choice is refused here with a message the model
can act on, rather than being written into a real order.

Today the only backend is InMemoryCart, because the YOPOS cart API is not documented and
the site is not accessible. When that contract is known, implement CartBackend against it
and pass it to Cart — nothing else in the codebase changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    """A resolved customisation: which option was picked for which step."""

    step_id: int
    label: str
    option_id: int
    name: str
    extra_price: float = 0.0


@dataclass
class Line:
    item_id: int
    name: str
    quantity: int
    unit_price: float
    choices: list[Choice] = field(default_factory=list)

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)

    def as_dict(self, line_no: int) -> dict[str, Any]:
        return {
            "line": line_no,
            "item_id": self.item_id,
            "name": self.name,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "line_total": self.line_total,
            "choices": [f"{c.label}: {c.name}" for c in self.choices],
        }


class CartBackend(Protocol):
    """Storage for one session's cart."""

    def lines(self) -> list[Line]: ...
    def add(self, line: Line) -> None: ...
    def remove(self, index: int) -> Line: ...
    def clear(self) -> None: ...


class InMemoryCart:
    """Process-local cart. Fine for a single instance; swap for the real backend later."""

    def __init__(self) -> None:
        self._lines: list[Line] = []

    def lines(self) -> list[Line]:
        return list(self._lines)

    def add(self, line: Line) -> None:
        # Identical item with identical choices collapses into one line.
        key = (line.item_id, tuple(sorted(c.option_id for c in line.choices)))
        for existing in self._lines:
            if (existing.item_id, tuple(sorted(c.option_id for c in existing.choices))) == key:
                existing.quantity += line.quantity
                return
        self._lines.append(line)

    def remove(self, index: int) -> Line:
        return self._lines.pop(index)

    def clear(self) -> None:
        self._lines.clear()


class CartError(ValueError):
    """
    A rejected cart operation.

    `detail` carries structured context — typically the step the model failed to answer,
    with its options — so the assistant can ask the customer the right question instead of
    guessing again.
    """

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_result(self) -> dict[str, Any]:
        return {"error": self.message, **self.detail}


class Cart:
    """Validates every operation against the menu before touching the backend."""

    MAX_QUANTITY = 20

    def __init__(self, menu, backend: CartBackend | None = None) -> None:
        self._menu = menu
        self._backend = backend or InMemoryCart()

    def add(
        self,
        item_id: int,
        quantity: int = 1,
        choices: list[dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        item = self._menu.get(item_id)
        if item is None:
            raise CartError(
                f"There is no item with id {item_id}. Use search_menu to find a real item_id."
            )

        if not isinstance(quantity, int) or quantity < 1:
            quantity = 1
        if quantity > self.MAX_QUANTITY:
            raise CartError(f"Maximum {self.MAX_QUANTITY} of a single item per order.")

        supplied = {c["step_id"]: c["option_id"] for c in (choices or []) if "step_id" in c}
        resolved: list[Choice] = []

        for step in item.required_steps:
            if step.step_id not in supplied:
                # Hand back the options so the assistant can ask, rather than re-guess.
                raise CartError(
                    f'"{item.name}" needs a {step.label} before it can be added.',
                    {"missing_step": step.as_prompt},
                )
            option = step.option(supplied[step.step_id])
            if option is None:
                raise CartError(
                    f"option_id {supplied[step.step_id]} is not valid for "
                    f'"{step.label}" on {item.name}.',
                    {"valid_options": step.as_prompt},
                )
            resolved.append(
                Choice(step.step_id, step.label, option.option_id, option.name, option.extra_price)
            )

        # Optional steps are honoured when supplied but never required.
        for step in item.steps:
            if step.required or step.step_id not in supplied:
                continue
            option = step.option(supplied[step.step_id])
            if option is not None:
                resolved.append(
                    Choice(step.step_id, step.label, option.option_id, option.name, option.extra_price)
                )

        unit_price = round(item.price + sum(c.extra_price for c in resolved), 2)
        self._backend.add(Line(item.item_id, item.name, quantity, unit_price, resolved))

        return {
            "added": item.name,
            "quantity": quantity,
            "unit_price": unit_price,
            "choices": [f"{c.label}: {c.name}" for c in resolved],
            "cart_total": self.total,
        }

    def remove(self, line: int) -> dict[str, Any]:
        lines = self._backend.lines()
        if not isinstance(line, int) or not 1 <= line <= len(lines):
            raise CartError(
                f"There is no line {line}. The cart has {len(lines)} line(s)."
                if lines else "The cart is already empty."
            )
        removed = self._backend.remove(line - 1)
        return {"removed": removed.name, "cart_total": self.total}

    def clear(self) -> dict[str, Any]:
        self._backend.clear()
        return {"cleared": True, "cart_total": 0.0}

    @property
    def total(self) -> float:
        return round(sum(l.line_total for l in self._backend.lines()), 2)

    def state(self) -> dict[str, Any]:
        lines = self._backend.lines()
        return {
            "lines": [l.as_dict(n) for n, l in enumerate(lines, 1)],
            "item_count": sum(l.quantity for l in lines),
            "total": self.total,
        }

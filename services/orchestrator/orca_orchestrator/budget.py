"""Bounded query budgets (PLAN.md Phase 5.3, gap M4).

Every query runs under a ceiling on tokens, wall-clock and API calls. Two mechanisms,
and the distinction matters:

* **Pruning**, before execution. The planner's proposed DAG is costed against the budget
  and steps that do not fit are dropped, cheapest-value-first. This happens while there
  is still a choice to make.
* **The ledger**, during execution. Spend is recorded as it happens and the engine stops
  starting new work once a ceiling is hit.

Pruning alone would be optimistic — estimates are wrong. The ledger alone would be too
late — you discover the overrun after paying for it. Doing both is what makes "agentic
under a budget" a real claim rather than a slide.

A pruned step is **recorded, not hidden**. The trace shows what was dropped and why, so a
thin answer is explainable rather than mysterious.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from orca_schemas import OrcaModel
from pydantic import Field


class BudgetDimension(StrEnum):
    """Which ceiling was hit."""

    TOKENS = "tokens"
    WALL_CLOCK = "wall_clock"
    API_CALLS = "api_calls"


class QueryBudget(OrcaModel):
    """Ceilings for one query. Defaults mirror ``config/models.yaml``."""

    max_total_tokens: int = Field(default=120_000, gt=0)
    max_wall_clock_seconds: float = Field(default=45.0, gt=0)
    max_tool_calls: int = Field(default=24, gt=0)

    def fits(self, spend: Spend) -> bool:
        return (
            spend.tokens <= self.max_total_tokens
            and spend.seconds <= self.max_wall_clock_seconds
            and spend.api_calls <= self.max_tool_calls
        )

    def exceeded_by(self, spend: Spend) -> BudgetDimension | None:
        """Which dimension a spend breaches, if any. Checked in a fixed order."""
        if spend.api_calls > self.max_tool_calls:
            return BudgetDimension.API_CALLS
        if spend.tokens > self.max_total_tokens:
            return BudgetDimension.TOKENS
        if spend.seconds > self.max_wall_clock_seconds:
            return BudgetDimension.WALL_CLOCK
        return None


@dataclass(frozen=True)
class Spend:
    """A cost, estimated or actual."""

    tokens: int = 0
    seconds: float = 0.0
    api_calls: int = 0

    def __add__(self, other: Spend) -> Spend:
        return Spend(
            tokens=self.tokens + other.tokens,
            seconds=self.seconds + other.seconds,
            api_calls=self.api_calls + other.api_calls,
        )

    @property
    def is_zero(self) -> bool:
        return self.tokens == 0 and self.seconds == 0.0 and self.api_calls == 0


@dataclass
class BudgetLedger:
    """Records spend during execution and reports when a ceiling is reached.

    Not thread-safe by design: the engine records spend from the coordinating task after
    a node completes, which keeps accounting in one place and ordering deterministic.
    """

    budget: QueryBudget
    spent: Spend = field(default_factory=Spend)
    entries: list[tuple[str, Spend]] = field(default_factory=list)

    def record(self, node: str, spend: Spend) -> None:
        """Attribute spend to a node."""
        self.spent = self.spent + spend
        self.entries.append((node, spend))

    @property
    def exhausted(self) -> BudgetDimension | None:
        """Which ceiling has been reached, if any."""
        return self.budget.exceeded_by(self.spent)

    def would_exceed(self, spend: Spend) -> BudgetDimension | None:
        """Whether adding a spend would breach a ceiling, without recording it."""
        return self.budget.exceeded_by(self.spent + spend)

    def remaining(self) -> Spend:
        return Spend(
            tokens=max(0, self.budget.max_total_tokens - self.spent.tokens),
            seconds=max(0.0, self.budget.max_wall_clock_seconds - self.spent.seconds),
            api_calls=max(0, self.budget.max_tool_calls - self.spent.api_calls),
        )

    def summary(self) -> dict[str, object]:
        """For the trace and the cost slide."""
        return {
            "tokens": self.spent.tokens,
            "seconds": round(self.spent.seconds, 3),
            "api_calls": self.spent.api_calls,
            "budget": {
                "max_total_tokens": self.budget.max_total_tokens,
                "max_wall_clock_seconds": self.budget.max_wall_clock_seconds,
                "max_tool_calls": self.budget.max_tool_calls,
            },
            "by_node": [
                {
                    "node": node,
                    "tokens": s.tokens,
                    "seconds": round(s.seconds, 3),
                    "api_calls": s.api_calls,
                }
                for node, s in self.entries
            ],
            "exhausted": self.exhausted.value if self.exhausted else None,
        }

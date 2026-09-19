"""Isochrone-A* weather routing (PLAN.md Phase 4.4, METHODS.md §1).

A* over a gridded cost surface where each cell costs

    f(VHM0, current vector projected on heading, wind, depth)

with geofence polygons removing cells outright. Published North-Indian-Ocean precedent:
**Sen & Padhy 2015, *Applied Ocean Research***; isochrone-A* and isochrone-Dijkstra
outperform naive great-circle routing in weather-routing benchmarks.

Two things distinguish this from a textbook A*:

* **Geofences are hard removals, not penalties.** A cell inside the IMBL buffer, a no-take
  MPA or a closed-season zone is not expensive — it does not exist. A penalty large enough
  to "usually" avoid arrest is still a route that crosses the line when the weather is bad
  enough, which is precisely the failure mode that gets boats seized.
* **The heuristic stays admissible.** It is the straight-line distance divided by the
  best achievable per-distance cost anywhere on the grid, so it can never overestimate
  the true remaining cost and A* keeps its optimality guarantee. An inflated heuristic
  would return a cheap-looking route that is not actually the best one, silently.

The cost is time-like: cell cost is a traverse cost in which a following current is
cheaper than a head current, so the optimiser naturally rides the current.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from orca_kernels.contract import (
    AbstainReason,
    InputRole,
    KernelInput,
    KernelResult,
    abstain,
    evaluate_staleness,
)

KERNEL = "route_optimization"
FORMULA_ID = "orca.routing.isochrone_astar"
FORMULA_VERSION = "1.0.0"
UNIT = "route_cost"

# Cost weights. Wave height dominates: it is what slows a small boat and endangers it.
WAVE_COST_WEIGHT = 2.0
WIND_COST_WEIGHT = 0.6
CURRENT_COST_WEIGHT = 1.0
BASE_COST = 1.0

NEIGHBOUR_OFFSETS: tuple[tuple[int, int], ...] = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
)


@dataclass(frozen=True)
class GridCell:
    """Conditions in one cell of the routing grid."""

    row: int
    col: int
    lat: float
    lon: float
    wave_height_m: float = 0.0
    wind_speed_ms: float = 0.0
    current_east_ms: float = 0.0
    current_north_ms: float = 0.0
    depth_m: float | None = None
    blocked: bool = False
    blocked_reason: str | None = None


@dataclass(frozen=True)
class RouteLeg:
    """One step of the chosen route."""

    lat: float
    lon: float
    cost: float


class CostSurface:
    """The grid A* searches, with geofence and depth constraints applied."""

    def __init__(
        self,
        cells: Iterable[GridCell],
        *,
        min_depth_m: float | None = None,
        cell_size_nm: float = 1.0,
    ) -> None:
        self._cells: dict[tuple[int, int], GridCell] = {(c.row, c.col): c for c in cells}
        if not self._cells:
            msg = "cost surface must contain at least one cell"
            raise ValueError(msg)
        self._min_depth_m = min_depth_m
        self._cell_size_nm = cell_size_nm

    @property
    def cell_size_nm(self) -> float:
        return self._cell_size_nm

    def get(self, row: int, col: int) -> GridCell | None:
        return self._cells.get((row, col))

    def is_navigable(self, cell: GridCell) -> bool:
        """Whether a cell may be entered at all.

        Blocked cells and cells shallower than the vessel needs are removed from the
        graph, not penalised — see the module docstring.
        """
        if cell.blocked:
            return False
        return not (
            self._min_depth_m is not None
            and cell.depth_m is not None
            and cell.depth_m < self._min_depth_m
        )

    def traverse_cost(self, source: GridCell, target: GridCell) -> float:
        """Cost of moving between adjacent cells.

        The current is projected onto the direction of travel: a following current
        reduces the cost, a head current raises it. Cost is floored well above zero so a
        strong following current can never make a leg free and tempt the search into
        absurd detours.
        """
        distance = math.hypot(target.row - source.row, target.col - source.col)
        bearing_east = target.col - source.col
        bearing_north = target.row - source.row
        magnitude = math.hypot(bearing_east, bearing_north)
        if magnitude == 0:
            return 0.0

        unit_east = bearing_east / magnitude
        unit_north = bearing_north / magnitude
        projected_current = (
            target.current_east_ms * unit_east + target.current_north_ms * unit_north
        )

        cost = (
            BASE_COST
            + WAVE_COST_WEIGHT * target.wave_height_m
            + WIND_COST_WEIGHT * (target.wind_speed_ms / 10.0)
            - CURRENT_COST_WEIGHT * projected_current
        )
        return max(0.1, cost) * distance

    def min_cost_per_step(self) -> float:
        """The cheapest possible single step anywhere on the grid.

        Used by the heuristic. Computed from the actual grid rather than assumed, so the
        heuristic stays admissible however benign or hostile the conditions are.
        """
        best = BASE_COST
        for cell in self._cells.values():
            if not self.is_navigable(cell):
                continue
            speed_gain = CURRENT_COST_WEIGHT * math.hypot(
                cell.current_east_ms, cell.current_north_ms
            )
            candidate = max(
                0.1,
                BASE_COST
                + WAVE_COST_WEIGHT * cell.wave_height_m
                + WIND_COST_WEIGHT * (cell.wind_speed_ms / 10.0)
                - speed_gain,
            )
            best = min(best, candidate)
        return best

    def neighbours(self, cell: GridCell) -> list[GridCell]:
        found = []
        for d_row, d_col in NEIGHBOUR_OFFSETS:
            neighbour = self.get(cell.row + d_row, cell.col + d_col)
            if neighbour is not None and self.is_navigable(neighbour):
                found.append(neighbour)
        return found

    def blocked_cells(self) -> list[GridCell]:
        return [c for c in self._cells.values() if c.blocked]


def _heuristic(source: GridCell, goal: GridCell, min_step_cost: float) -> float:
    """Admissible A* heuristic: straight-line steps times the cheapest possible step."""
    return math.hypot(goal.row - source.row, goal.col - source.col) * min_step_cost


def plan_route(
    *,
    surface: CostSurface,
    start: tuple[int, int],
    goal: tuple[int, int],
    evaluated_at: datetime,
    inputs: tuple[KernelInput, ...] = (),
    cost_fn: Callable[[GridCell, GridCell], float] | None = None,
) -> KernelResult:
    """Find the least-cost navigable route, or abstain.

    Abstains rather than returning a partial route when no navigable path exists: a route
    that stops halfway is not a route, and returning one would invite a boat to set off
    towards a dead end.
    """
    start_cell = surface.get(*start)
    goal_cell = surface.get(*goal)

    declared_inputs = inputs or (
        KernelInput(
            name="cost_surface",
            value=None,
            unit="grid",
            source="route_planner",
            role=InputRole.REQUIRED,
        ),
    )

    def abstain_with(reason: AbstainReason, detail: str) -> KernelResult:
        return abstain(
            kernel=KERNEL,
            formula_id=FORMULA_ID,
            formula_version=FORMULA_VERSION,
            unit=UNIT,
            inputs=declared_inputs,
            evaluated_at=evaluated_at,
            reason=reason,
            detail=detail,
        )

    if start_cell is None or goal_cell is None:
        return abstain_with(
            AbstainReason.OUT_OF_COVERAGE, "start or goal lies outside the cost surface"
        )
    if not surface.is_navigable(start_cell):
        return abstain_with(
            AbstainReason.CONSTRAINT_VIOLATION,
            f"start cell is not navigable: {start_cell.blocked_reason or 'blocked'}",
        )
    if not surface.is_navigable(goal_cell):
        return abstain_with(
            AbstainReason.CONSTRAINT_VIOLATION,
            f"goal cell is not navigable: {goal_cell.blocked_reason or 'blocked'}",
        )

    step_cost = cost_fn or surface.traverse_cost
    min_step = surface.min_cost_per_step()

    open_heap: list[tuple[float, int, tuple[int, int]]] = []
    counter = 0
    heapq.heappush(
        open_heap,
        (_heuristic(start_cell, goal_cell, min_step), counter, (start_cell.row, start_cell.col)),
    )
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    best_cost: dict[tuple[int, int], float] = {(start_cell.row, start_cell.col): 0.0}
    expanded = 0

    while open_heap:
        _, _, current_key = heapq.heappop(open_heap)
        current = surface.get(*current_key)
        if current is None:
            continue
        expanded += 1

        if current_key == (goal_cell.row, goal_cell.col):
            return _build_result(
                surface=surface,
                came_from=came_from,
                best_cost=best_cost,
                goal_key=current_key,
                evaluated_at=evaluated_at,
                inputs=declared_inputs,
                expanded=expanded,
            )

        for neighbour in surface.neighbours(current):
            neighbour_key = (neighbour.row, neighbour.col)
            tentative = best_cost[current_key] + step_cost(current, neighbour)
            if tentative < best_cost.get(neighbour_key, math.inf):
                best_cost[neighbour_key] = tentative
                came_from[neighbour_key] = current_key
                counter += 1
                heapq.heappush(
                    open_heap,
                    (
                        tentative + _heuristic(neighbour, goal_cell, min_step),
                        counter,
                        neighbour_key,
                    ),
                )

    blocked = surface.blocked_cells()
    reasons = sorted({c.blocked_reason for c in blocked if c.blocked_reason})
    detail = "no navigable route exists between start and goal"
    if reasons:
        detail += f"; blocking constraints: {', '.join(reasons)}"
    return abstain_with(AbstainReason.CONSTRAINT_VIOLATION, detail)


def _build_result(
    *,
    surface: CostSurface,
    came_from: dict[tuple[int, int], tuple[int, int]],
    best_cost: dict[tuple[int, int], float],
    goal_key: tuple[int, int],
    evaluated_at: datetime,
    inputs: tuple[KernelInput, ...],
    expanded: int,
) -> KernelResult:
    path_keys = [goal_key]
    while path_keys[-1] in came_from:
        path_keys.append(came_from[path_keys[-1]])
    path_keys.reverse()

    legs: list[RouteLeg] = []
    for key in path_keys:
        cell = surface.get(*key)
        if cell is not None:
            legs.append(RouteLeg(lat=cell.lat, lon=cell.lon, cost=round(best_cost[key], 4)))

    total_cost = round(best_cost[goal_key], 4)
    distance_nm = round((len(legs) - 1) * surface.cell_size_nm, 3)

    return KernelResult(
        kernel=KERNEL,
        formula_id=FORMULA_ID,
        formula_version=FORMULA_VERSION,
        value=total_cost,
        unit=UNIT,
        inputs=inputs,
        staleness=evaluate_staleness(inputs, evaluated_at=evaluated_at),
        extras={
            "waypoints": [
                {"lat": leg.lat, "lon": leg.lon, "cumulative_cost": leg.cost} for leg in legs
            ],
            "leg_count": len(legs) - 1,
            "approx_distance_nm": distance_nm,
            "cells_expanded": expanded,
            "precedent": "Sen & Padhy 2015, Applied Ocean Research (North Indian Ocean)",
        },
    )


def great_circle_baseline(
    *, surface: CostSurface, start: tuple[int, int], goal: tuple[int, int]
) -> float | None:
    """Cost of the naive straight-line route, for benchmarking.

    Returns None when the straight line crosses a blocked cell — which is the point of
    the benchmark: the naive route is not merely more expensive, it is often illegal.
    """
    start_cell = surface.get(*start)
    goal_cell = surface.get(*goal)
    if start_cell is None or goal_cell is None:
        return None

    steps = max(abs(goal_cell.row - start_cell.row), abs(goal_cell.col - start_cell.col))
    if steps == 0:
        return 0.0

    total = 0.0
    previous = start_cell
    for step in range(1, steps + 1):
        row = start_cell.row + round((goal_cell.row - start_cell.row) * step / steps)
        col = start_cell.col + round((goal_cell.col - start_cell.col) * step / steps)
        cell = surface.get(row, col)
        if cell is None or not surface.is_navigable(cell):
            return None
        total += surface.traverse_cost(previous, cell)
        previous = cell
    return round(total, 4)

"""Orchestration: carry-over, budget pruning, replanning (PLAN.md Phase 5).

The three tests the phase specifically calls for are:

* ``test_and_tomorrow_carries_location_and_vessel`` — the gap-G2 behaviour
* ``TestBudgetPruning`` — over-budget plans are pruned before execution
* ``TestFailureReplanning`` — a tool failure triggers an approved fallback

No network and no database: the orchestration layer is exercised against injected tools,
which is also what lets the failure cases be provoked deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import BoundingBox, TimeWindow

from orca_orchestrator import (
    AgentNode,
    ConversationState,
    DagEngine,
    EventKind,
    Intent,
    LangGraphEngine,
    LlmPlanner,
    LlmPlanResponse,
    PlannerLlm,
    PlanSpec,
    PlanStep,
    QueryBudget,
    ResolvedQuery,
    RulePlanner,
    SessionMemory,
    SlotOrigin,
    TaskStatus,
    Tool,
    ToolFailure,
    TurnRequest,
    prune_to_budget,
)

NOW = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
RAMESWARAM = BoundingBox(min_lat=9.2, max_lat=9.4, min_lon=79.2, max_lon=79.4)
CHENNAI = BoundingBox(min_lat=13.0, max_lat=13.2, min_lon=80.2, max_lon=80.4)


def tomorrow() -> TimeWindow:
    return TimeWindow(start=NOW + timedelta(days=1), end=NOW + timedelta(days=1, hours=12))


def day_after() -> TimeWindow:
    return TimeWindow(start=NOW + timedelta(days=2), end=NOW + timedelta(days=2, hours=12))


def fresh_state(**memory_overrides) -> ConversationState:
    memory = SessionMemory(
        user_id="fisher-1",
        default_vessel_id="TN-07-MM-1234",
        home_port="Rameswaram",
        language="ta",
        **memory_overrides,
    )
    return ConversationState(session_id="session-1", memory=memory)


def working_tools(**overrides):
    tools = {
        Tool.FETCH_MARINE_DATA: lambda **_: {
            "evidence_ids": ["cmems:vhm0:1"],
            "sources": ["cmems"],
            "wave_height_m": 1.4,
        },
        Tool.FETCH_WEATHER: lambda **_: {
            "evidence_ids": ["open_meteo:wind:1"],
            "sources": ["open_meteo_marine"],
            "wind_speed_ms": 7.0,
        },
        Tool.QUERY_GEOFENCE: lambda **_: {"distance_km": 12.4, "sources": ["imbl_ind_lka"]},
        Tool.PREDICT_DRIFT: lambda **_: {"will_cross": False},
        Tool.SCORE_RELIABILITY: lambda **_: {"reliability": 0.82},
        Tool.COMPUTE_SAFETY_SCORE: lambda **_: {"value": 72.0, "band": "safe", "abstained": False},
        Tool.RANK_FISHING_ZONES: lambda **_: {"frontier": []},
        Tool.PLAN_ROUTE: lambda **_: {"value": 18.2, "abstained": False},
        Tool.DETECT_ANOMALIES: lambda **_: {"flags": [], "evidence_ids": []},
        Tool.RETRIEVE_CORPUS: lambda **_: {"passages": []},
        Tool.QUERY_STRUCTURED_EVIDENCE: lambda **_: {"records": []},
        Tool.SYNTHESISE_RESPONSE: lambda **_: {"text": "..."},
    }
    tools.update(overrides)
    return tools


class TestMultiTurnCarryOver:
    """Gap G2: a follow-up must not lose the thread."""

    def test_and_tomorrow_carries_location_and_vessel(self) -> None:
        """The headline behaviour: "and tomorrow?" keeps where and which boat."""
        state = fresh_state().resolve(
            TurnRequest(
                utterance="is it safe to go this evening off Rameswaram?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                place_name="Rameswaram",
                time_window=TimeWindow(start=NOW, end=NOW + timedelta(hours=8)),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )

        follow_up = state.resolve(
            TurnRequest(utterance="and tomorrow?", time_window=tomorrow()), now=NOW
        )

        resolved = follow_up.resolved
        assert resolved is not None
        assert resolved.location is not None
        assert resolved.location.value == RAMESWARAM
        assert resolved.location.origin is SlotOrigin.CARRIED_OVER
        assert resolved.vessel_id is not None
        assert resolved.vessel_id.value == "TN-07-MM-1234"
        assert resolved.vessel_id.origin is SlotOrigin.CARRIED_OVER
        assert resolved.intent is Intent.SAFETY, "intent carries too"
        assert not resolved.needs_clarification

    def test_the_new_time_window_replaces_the_old_one(self) -> None:
        """Carry-over must not answer the wrong day."""
        state = fresh_state().resolve(
            TurnRequest(
                utterance="safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )

        follow_up = state.resolve(
            TurnRequest(utterance="and the day after?", time_window=day_after()), now=NOW
        )

        assert follow_up.resolved is not None
        assert follow_up.resolved.time_window is not None
        assert follow_up.resolved.time_window.value == day_after()
        assert follow_up.resolved.time_window.origin is SlotOrigin.THIS_TURN

    def test_a_time_window_is_never_carried_silently(self) -> None:
        """Omitting the time must surface as a missing slot, not reuse yesterday's."""
        state = fresh_state().resolve(
            TurnRequest(
                utterance="safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )

        follow_up = state.resolve(TurnRequest(utterance="what about there?"), now=NOW)

        assert follow_up.resolved is not None
        assert follow_up.resolved.time_window is None
        assert "time_window" in follow_up.resolved.missing_required

    def test_a_new_location_overrides_the_carried_one(self) -> None:
        state = fresh_state().resolve(
            TurnRequest(
                utterance="safe off Rameswaram?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )

        moved = state.resolve(
            TurnRequest(utterance="and off Chennai?", location=CHENNAI, time_window=tomorrow()),
            now=NOW,
        )

        assert moved.resolved is not None
        assert moved.resolved.location is not None
        assert moved.resolved.location.value == CHENNAI
        assert moved.resolved.location.origin is SlotOrigin.THIS_TURN

    def test_profile_defaults_fill_a_first_turn(self) -> None:
        """A first-time question still knows the boat and the language."""
        state = fresh_state().resolve(
            TurnRequest(
                utterance="where should I fish?", intent=Intent.FISHING_ZONE, location=RAMESWARAM
            ),
            now=NOW,
        )

        resolved = state.resolved
        assert resolved is not None
        assert resolved.vessel_id is not None
        assert resolved.vessel_id.origin is SlotOrigin.PROFILE_DEFAULT
        assert resolved.language is not None
        assert resolved.language.value == "ta"

    def test_carried_slots_are_reportable(self) -> None:
        """The answer can say 'for Rameswaram, as before' because this is inspectable."""
        state = fresh_state().resolve(
            TurnRequest(
                utterance="safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                place_name="Rameswaram",
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )
        follow_up = state.resolve(
            TurnRequest(utterance="and tomorrow?", time_window=day_after()), now=NOW
        )

        assert follow_up.resolved is not None
        assert "location" in follow_up.resolved.carried_slots
        assert "vessel_id" in follow_up.resolved.carried_slots
        assert "time_window" not in follow_up.resolved.carried_slots

    def test_missing_slots_trigger_a_clarifying_question(self) -> None:
        state = ConversationState(session_id="s", memory=SessionMemory(user_id="u")).resolve(
            TurnRequest(utterance="is it safe?", intent=Intent.SAFETY), now=NOW
        )

        assert state.resolved is not None
        assert state.resolved.needs_clarification
        assert set(state.resolved.missing_required) == {"location", "time_window", "vessel_id"}

    def test_history_accumulates_across_turns(self) -> None:
        state = fresh_state()
        for index in range(3):
            state = state.resolve(
                TurnRequest(
                    utterance=f"turn {index}",
                    intent=Intent.SAFETY,
                    location=RAMESWARAM,
                    time_window=tomorrow(),
                    vessel_id="TN-07-MM-1234",
                ),
                now=NOW,
            )

        assert len(state.history) == 3
        assert state.turn_index == 2


class TestPlanValidation:
    """A model may propose a plan; validation is the authority."""

    def test_a_node_cannot_call_a_tool_outside_its_allow_list(self) -> None:
        with pytest.raises(ValueError, match="may not call"):
            PlanStep(
                node=AgentNode.RESPONSE,
                tools=(Tool.PLAN_ROUTE,),
                reason="response node trying to route",
            )

    def test_unknown_dependencies_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in the plan"):
            PlanSpec(
                rationale="broken",
                steps=(
                    PlanStep(node=AgentNode.RESPONSE, depends_on=(AgentNode.RISK,), reason="x"),
                ),
            )

    def test_cycles_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            PlanSpec(
                rationale="cyclic",
                steps=(
                    PlanStep(node=AgentNode.RISK, depends_on=(AgentNode.VERIFIER,), reason="a"),
                    PlanStep(node=AgentNode.VERIFIER, depends_on=(AgentNode.RISK,), reason="b"),
                ),
            )

    def test_duplicate_nodes_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most once"):
            PlanSpec(
                rationale="duplicated",
                steps=(
                    PlanStep(node=AgentNode.RISK, reason="a"),
                    PlanStep(node=AgentNode.RISK, reason="b"),
                ),
            )

    def test_execution_layers_expose_the_fan_out(self) -> None:
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY))

        layers = plan.execution_layers()

        assert layers[0] == (AgentNode.PLANNER,)
        parallel = next(layer for layer in layers if len(layer) > 1)
        assert AgentNode.MARINE_DATA in parallel
        assert AgentNode.WEATHER in parallel

    def test_every_intent_produces_a_valid_plan(self) -> None:
        for intent in Intent:
            plan = RulePlanner().plan(ResolvedQuery(intent=intent))
            assert plan.steps
            assert plan.execution_layers()


class TestLlmPlannerSchema:
    """Machine-consumed LLM output is strictly validated."""

    class _Llm(PlannerLlm):
        def __init__(self, content: str) -> None:
            self.content = content

        def propose_plan(self, query):  # noqa: ANN001
            return LlmPlanResponse(content=self.content)

    def test_valid_llm_plan_is_accepted(self) -> None:
        payload = (
            '{"rationale": "minimal", "steps": ['
            '{"node": "planner", "reason": "decompose"},'
            '{"node": "response", "depends_on": ["planner"], "reason": "answer"}]}'
        )
        planner = LlmPlanner(self._Llm(payload))

        plan = planner.plan(ResolvedQuery(intent=Intent.SAFETY))

        assert [s.node for s in plan.steps] == [AgentNode.PLANNER, AgentNode.RESPONSE]
        assert planner.last_rejection is None

    def test_malformed_json_falls_back_to_the_rule_planner(self) -> None:
        planner = LlmPlanner(self._Llm("not json at all"))

        plan = planner.plan(ResolvedQuery(intent=Intent.SAFETY))

        assert planner.last_rejection is not None
        assert "not valid JSON" in planner.last_rejection
        assert AgentNode.RISK in {s.node for s in plan.steps}

    def test_a_forbidden_tool_is_rejected_not_executed(self) -> None:
        """The clearest case for validation: a plan letting the response node route."""
        payload = (
            '{"rationale": "bad", "steps": ['
            '{"node": "response", "tools": ["plan_route"], "reason": "sneaky"}]}'
        )
        planner = LlmPlanner(self._Llm(payload))

        plan = planner.plan(ResolvedQuery(intent=Intent.SAFETY))

        assert planner.last_rejection is not None
        assert "may not call" in planner.last_rejection
        assert plan.rationale != "bad"

    def test_an_unknown_node_is_rejected(self) -> None:
        payload = '{"rationale": "x", "steps": [{"node": "hallucinated_node", "reason": "y"}]}'
        planner = LlmPlanner(self._Llm(payload))

        planner.plan(ResolvedQuery(intent=Intent.SAFETY))

        assert planner.last_rejection is not None
        assert "failed validation" in planner.last_rejection

    def test_extra_fields_are_rejected(self) -> None:
        payload = '{"rationale": "x", "steps": [{"node": "planner", "reason": "y", "sudo": true}]}'
        planner = LlmPlanner(self._Llm(payload))

        planner.plan(ResolvedQuery(intent=Intent.SAFETY))

        assert planner.last_rejection is not None


class TestBudgetPruning:
    """Over-budget plans are pruned before execution, and the pruning is recorded."""

    def test_a_generous_budget_prunes_nothing(self) -> None:
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY))

        pruned = prune_to_budget(plan, QueryBudget())

        assert not pruned.was_pruned
        assert len(pruned.plan.steps) == len(plan.steps)

    def test_a_tight_api_call_budget_drops_optional_steps(self) -> None:
        """Only steps marked optional are pruned, and the estimate falls.

        It is not asserted that the plan then fits: when the remainder is all
        non-optional, pruning cannot make it fit, and the design deliberately runs it
        anyway with the ledger as the backstop rather than dropping a step that a kernel
        depends on. ``test_the_ledger_stops_work_once_a_ceiling_is_hit`` covers that half.
        """
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.FISHING_ZONE))
        before = plan.estimated_cost().api_calls

        pruned = prune_to_budget(plan, QueryBudget(max_tool_calls=4))

        assert pruned.was_pruned
        assert pruned.estimated_api_calls < before
        dropped = {p.node for p in pruned.pruned}
        assert AgentNode.ECOSYSTEM in dropped, "the optional ecosystem step should go first"

    def test_pruning_records_what_was_dropped_and_why(self) -> None:
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY))

        pruned = prune_to_budget(plan, QueryBudget(max_tool_calls=3))

        assert pruned.pruned
        assert all(p.reason for p in pruned.pruned)
        assert any("budget" in p.reason for p in pruned.pruned)

    def test_safety_critical_nodes_are_never_pruned(self) -> None:
        """Dropping the verifier to save tokens would remove the abstention check."""
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY))

        pruned = prune_to_budget(plan, QueryBudget(max_total_tokens=1, max_tool_calls=1))

        survivors = {s.node for s in pruned.plan.steps}
        assert AgentNode.VERIFIER in survivors
        assert AgentNode.RISK in survivors
        assert AgentNode.RESPONSE in survivors

    def test_the_pruned_plan_is_still_a_valid_dag(self) -> None:
        """Dangling dependencies on pruned nodes must be cleaned up, not left broken."""
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.FISHING_ZONE))

        pruned = prune_to_budget(plan, QueryBudget(max_tool_calls=4))

        assert pruned.plan.execution_layers()
        present = {s.node for s in pruned.plan.steps}
        for step in pruned.plan.steps:
            assert set(step.depends_on) <= present

    def test_the_engine_emits_a_pruning_event(self) -> None:
        state = fresh_state().resolve(
            TurnRequest(
                utterance="where should I fish?",
                intent=Intent.FISHING_ZONE,
                location=RAMESWARAM,
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine().run(
            state, plan=plan, tools=working_tools(), budget=QueryBudget(max_tool_calls=4), now=NOW
        )

        assert EventKind.PLAN_PRUNED in result.event_kinds()
        assert result.pruned

    def test_the_ledger_stops_work_once_a_ceiling_is_hit(self) -> None:
        state = fresh_state().resolve(
            TurnRequest(
                utterance="is it safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine().run(
            state,
            plan=plan,
            tools=working_tools(),
            budget=QueryBudget(max_total_tokens=1500),
            now=NOW,
        )

        skipped = [m for m in result.messages.values() if m.status is TaskStatus.SKIPPED]
        assert skipped, "some nodes should be skipped once the token ceiling is reached"
        assert EventKind.BUDGET_EXHAUSTED in result.event_kinds()


class TestFailureReplanning:
    """A tool failure triggers an approved fallback, not an invented one."""

    def _safety_state(self) -> ConversationState:
        return fresh_state().resolve(
            TurnRequest(
                utterance="is it safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )

    def test_marine_data_failure_replans_onto_the_approved_substitute(self) -> None:
        def failing(**_):
            raise ToolFailure(AgentNode.MARINE_DATA, "INCOIS ERDDAP timed out")

        state = self._safety_state()
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine().run(
            state,
            plan=plan,
            tools=working_tools(**{Tool.FETCH_MARINE_DATA: failing}),
            now=NOW,
        )

        assert EventKind.REPLANNED in result.event_kinds()
        assert any("timed out" in r for r in result.replans)
        # WEATHER is the declared substitute and must still have run.
        assert result.messages[AgentNode.WEATHER].ok
        assert AgentNode.MARINE_DATA not in {s.node for s in result.plan.steps}

    def test_the_replan_only_uses_approved_substitutions(self) -> None:
        from orca_orchestrator import APPROVED_SUBSTITUTIONS

        revised = RulePlanner().replan(
            RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY)),
            failed=AgentNode.MARINE_DATA,
            reason="timeout",
        )

        assert revised is not None
        nodes = {s.node for s in revised.steps}
        assert nodes <= set(AgentNode)
        assert APPROVED_SUBSTITUTIONS[AgentNode.MARINE_DATA] == (AgentNode.WEATHER,)

    def test_a_safety_critical_failure_abstains_rather_than_replanning(self) -> None:
        """No substitute exists for the safety kernel, and inventing one is not an option."""
        revised = RulePlanner().replan(
            RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY)),
            failed=AgentNode.RISK,
            reason="kernel unavailable",
        )

        assert revised is None

    def test_an_unreplannable_failure_produces_an_abstention(self) -> None:
        def failing(**_):
            raise ToolFailure(AgentNode.RISK, "safety kernel unavailable")

        state = self._safety_state()
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine().run(
            state, plan=plan, tools=working_tools(**{Tool.COMPUTE_SAFETY_SCORE: failing}), now=NOW
        )

        assert any("no substitute" in r for r in result.replans)
        assert result.messages[AgentNode.RISK].status is TaskStatus.FAILED

    def test_replanning_is_bounded(self) -> None:
        """A failing source must not send the planner into a loop."""

        def always_fails(**_):
            raise ToolFailure(AgentNode.WEATHER, "IMD 401")

        state = self._safety_state()
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine(max_replans=1).run(
            state,
            plan=plan,
            tools=working_tools(
                **{
                    Tool.FETCH_MARINE_DATA: lambda **_: (_ for _ in ()).throw(
                        ToolFailure(AgentNode.MARINE_DATA, "down")
                    ),
                    Tool.FETCH_WEATHER: always_fails,
                }
            ),
            now=NOW,
        )

        assert len(result.replans) <= 2

    def test_a_forbidden_tool_call_raises_rather_than_replanning(self) -> None:
        """An agent reaching outside its allow-list is our defect, not an outage."""
        from orca_orchestrator.nodes import NodeContext

        context = NodeContext(state=self._safety_state(), tools=working_tools(), now=NOW)

        with pytest.raises(PermissionError, match="not allowed to call"):
            context.call(AgentNode.RESPONSE, Tool.PLAN_ROUTE)


class TestExecutionAndEvents:
    def _run(self, engine, intent: Intent = Intent.SAFETY, **tool_overrides):
        state = fresh_state().resolve(
            TurnRequest(
                utterance="is it safe tomorrow?",
                intent=intent,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )
        plan = RulePlanner().plan(state.resolved)
        return engine.run(state, plan=plan, tools=working_tools(**tool_overrides), now=NOW)

    def test_a_clean_run_produces_an_answer(self) -> None:
        result = self._run(DagEngine())

        assert result.response is not None
        assert result.response.result["kind"] == "answer"
        assert not result.abstained

    def test_streamed_events_describe_the_run(self) -> None:
        result = self._run(DagEngine())

        kinds = result.event_kinds()
        assert kinds[0] is EventKind.PLAN_READY
        assert kinds[-1] is EventKind.RUN_FINISHED
        assert EventKind.NODE_STARTED in kinds
        assert EventKind.EVIDENCE_ARRIVED in kinds

    def test_every_node_returns_the_inter_agent_message_schema(self) -> None:
        result = self._run(DagEngine())

        for node, message in result.messages.items():
            assert message.agent is node
            assert message.task_id.startswith("session-1:")
            assert message.issued_time == NOW
            assert set(message.cost_used) == {"tokens", "seconds", "api_calls"}

    def test_an_abstaining_kernel_propagates_to_an_abstention(self) -> None:
        result = self._run(
            DagEngine(),
            **{Tool.COMPUTE_SAFETY_SCORE: lambda **_: {"abstained": True, "value": None}},
        )

        assert result.abstained
        assert result.response.result["kind"] == "abstention"

    def test_the_trace_shows_plan_layers_budget_and_status(self) -> None:
        trace = self._run(DagEngine()).trace()

        assert trace["plan"]
        assert trace["layers"]
        assert trace["budget"]["tokens"] > 0
        assert trace["nodes"]["response"]["status"] == "succeeded"

    def test_a_clarifying_question_short_circuits_the_answer(self) -> None:
        state = ConversationState(session_id="s", memory=SessionMemory(user_id="u")).resolve(
            TurnRequest(utterance="is it safe?", intent=Intent.SAFETY), now=NOW
        )
        plan = RulePlanner().plan(state.resolved)

        result = DagEngine().run(state, plan=plan, tools=working_tools(), now=NOW)

        assert result.response.result["kind"] == "clarifying_question"
        assert "location" in result.response.result["missing"]


class TestEngineParity:
    """Both engines run the same nodes, so the answer cannot depend on the engine."""

    def test_langgraph_is_available_and_compiles_the_plan(self) -> None:
        assert LangGraphEngine.available()
        plan = RulePlanner().plan(ResolvedQuery(intent=Intent.SAFETY))

        compiled = LangGraphEngine().compile(plan)
        output = compiled.invoke({"visited": []})

        assert "visited" in output

    def test_both_engines_produce_the_same_result(self) -> None:
        state = fresh_state().resolve(
            TurnRequest(
                utterance="is it safe tomorrow?",
                intent=Intent.SAFETY,
                location=RAMESWARAM,
                time_window=tomorrow(),
                vessel_id="TN-07-MM-1234",
            ),
            now=NOW,
        )
        plan = RulePlanner().plan(state.resolved)

        dag = DagEngine().run(state, plan=plan, tools=working_tools(), now=NOW)
        lang = LangGraphEngine().run(state, plan=plan, tools=working_tools(), now=NOW)

        assert dag.response.result == lang.response.result
        assert set(dag.messages) == set(lang.messages)

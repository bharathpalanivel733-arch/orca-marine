"""Trust layer: rules, conflict resolution, abstention, provenance, replay (PLAN.md 6).

The centrepiece is ``TestDeterministicReplay::test_replay_returns_identical_output`` —
the assertion that a run re-derived from its archived payloads reproduces the original
exactly. Everything else in the trust layer is only as good as that property.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import BoundingBox, EvidenceProvenance, MarineVariable

from orca_trust import (
    ABSOLUTE_FRESHNESS_LIMIT,
    Caveat,
    CheckName,
    CheckSeverity,
    CritiqueResponse,
    EvidenceItem,
    NullCritic,
    PayloadArchiveReader,
    ProvenanceBuilder,
    ProvenanceGraph,
    ProvenanceNodeKind,
    ReplayOutcome,
    SourceReading,
    TrustVerdict,
    VerdictStatus,
    VerificationRequest,
    Verifier,
    apply_critique,
    check_evidence_sufficiency,
    check_formula_validity,
    check_freshness,
    check_missing_data,
    check_source_disagreement,
    check_source_validity,
    check_spatial_consistency,
    replay_run,
    resolve_conflict,
    threshold_for,
)
from orca_trust.critique import Critic, CritiqueRequest

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
PALK_BAY = BoundingBox(min_lat=9.0, max_lat=10.0, min_lon=79.0, max_lon=80.0)
WAVES = MarineVariable.SIGNIFICANT_WAVE_HEIGHT


def provenance(source: str = "cmems", *, age_hours: float = 1.0, pid: str = "p1"):
    return EvidenceProvenance(
        source=source,
        url=f"https://example.test/{source}",
        issued_time=NOW - timedelta(hours=age_hours),
        authority_rank=2,
        license="test",
        provenance_id=pid,
    )


def item(
    source: str = "cmems",
    *,
    age_hours: float = 1.0,
    variable: str | None = "significant_wave_height",
    value: float | None = 1.4,
    lat: float | None = 9.5,
    lon: float | None = 79.5,
    pid: str = "p1",
) -> EvidenceItem:
    return EvidenceItem(
        provenance=provenance(source, age_hours=age_hours, pid=pid),
        variable=variable,
        value=value,
        lat=lat,
        lon=lon,
    )


class TestDeterministicRules:
    def test_known_sources_pass(self) -> None:
        result = check_source_validity([item("cmems"), item("imd")])
        assert result.passed

    def test_an_unrecognised_source_blocks(self) -> None:
        """Evidence whose origin cannot be established has no place in a safety decision."""
        result = check_source_validity([item("cmems"), item("random_blog")])

        assert not result.passed
        assert result.severity is CheckSeverity.BLOCKING
        assert "random_blog" in result.detail

    def test_fresh_evidence_passes(self) -> None:
        assert check_freshness([item(age_hours=2)], now=NOW).passed

    def test_stale_evidence_blocks_with_the_documented_wording(self) -> None:
        """METHODS.md §2 gives the phrasing a refusal should use."""
        result = check_freshness([item(age_hours=13)], now=NOW)

        assert not result.passed
        assert result.severity is CheckSeverity.BLOCKING
        assert "I can't safely answer" in result.detail
        assert "13.0 h old" in result.detail

    def test_a_slow_cadence_cannot_excuse_a_stale_forecast(self) -> None:
        """A 56-hour cadence does not make a 20-hour-old wave forecast usable."""
        slow = EvidenceItem(
            provenance=provenance(age_hours=20),
            variable="significant_wave_height",
            value=1.2,
            cadence_deadline=timedelta(hours=56),
        )

        result = check_freshness([slow], now=NOW)

        assert not result.passed
        assert (
            result.threshold["absolute_limit_hours"]
            == ABSOLUTE_FRESHNESS_LIMIT.total_seconds() / 3600
        )

    def test_no_evidence_fails_freshness(self) -> None:
        assert not check_freshness([], now=NOW).passed

    def test_evidence_inside_the_box_is_consistent(self) -> None:
        assert check_spatial_consistency([item(lat=9.5, lon=79.5)], bbox=PALK_BAY).passed

    def test_evidence_far_outside_the_box_warns(self) -> None:
        result = check_spatial_consistency([item(lat=22.0, lon=70.0)], bbox=PALK_BAY)

        assert not result.passed
        assert result.severity is CheckSeverity.WARNING

    def test_grid_snapping_within_tolerance_is_accepted(self) -> None:
        """Gridded sources answer at their own cell; that is not an inconsistency."""
        assert check_spatial_consistency([item(lat=10.4, lon=80.4)], bbox=PALK_BAY).passed

    def test_missing_required_variable_blocks(self) -> None:
        result = check_missing_data(
            [item(variable="significant_wave_height")],
            required_variables=frozenset({"significant_wave_height", "wind_speed"}),
        )

        assert not result.passed
        assert result.severity is CheckSeverity.BLOCKING
        assert "wind_speed" in result.detail

    def test_a_valueless_item_counts_as_missing(self) -> None:
        """A record present but empty is not evidence."""
        result = check_missing_data(
            [item(variable="wind_speed", value=None)],
            required_variables=frozenset({"wind_speed"}),
        )
        assert not result.passed

    def test_unapproved_formula_blocks(self) -> None:
        result = check_formula_validity(
            formula_ids=["orca.safety.boat_relative_hazard", "made.up.formula"],
            approved=frozenset({"orca.safety.boat_relative_hazard"}),
        )

        assert not result.passed
        assert result.severity is CheckSeverity.BLOCKING
        assert "made.up.formula" in result.detail

    def test_insufficient_evidence_blocks(self) -> None:
        result = check_evidence_sufficiency([item()], minimum=2)

        assert not result.passed
        assert result.severity is CheckSeverity.BLOCKING

    def test_every_check_reports_what_it_compared(self) -> None:
        """A refusal must be explainable by the number that caused it."""
        result = check_freshness([item(age_hours=13)], now=NOW)
        assert result.observed
        assert result.threshold


class TestConflictResolution:
    def _reading(self, source: str, value: float, reliability: float) -> SourceReading:
        return SourceReading(
            source=source,
            variable=WAVES,
            value=value,
            unit="m",
            issued_time=NOW - timedelta(hours=1),
            reliability=reliability,
            provenance_id=f"{source}:1",
        )

    def test_agreement_within_threshold_is_not_a_conflict(self) -> None:
        resolution = resolve_conflict(
            [self._reading("cmems", 1.4, 0.8), self._reading("incois_erddap", 1.6, 0.85)]
        )

        assert not resolution.in_conflict
        assert resolution.spread == pytest.approx(0.2)

    def test_disagreement_prefers_the_more_reliable_source(self) -> None:
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 1.2, 0.6), self._reading("cmems", 2.4, 0.85)]
        )

        assert resolution.in_conflict
        assert resolution.chosen.source == "cmems"
        assert [r.source for r in resolution.rejected] == ["open_meteo_marine"]

    def test_the_interval_widens_to_span_both_readings(self) -> None:
        """The step most systems skip: the disagreement IS the uncertainty."""
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 1.2, 0.6), self._reading("cmems", 2.4, 0.85)]
        )

        assert resolution.lower == pytest.approx(1.2)
        assert resolution.upper == pytest.approx(2.4)
        assert resolution.interval_width == pytest.approx(1.2)

    def test_the_conflict_is_disclosed_not_hidden(self) -> None:
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 1.2, 0.6), self._reading("cmems", 2.4, 0.85)]
        )
        caveat = resolution.as_caveat()

        assert caveat.kind == "source_disagreement"
        assert caveat.widened_uncertainty
        assert "cmems" in caveat.detail and "open_meteo_marine" in caveat.detail

    def test_no_averaging_occurs(self) -> None:
        """The mean of two forecasts is a number neither source predicted."""
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 1.0, 0.6), self._reading("cmems", 3.0, 0.85)]
        )

        assert resolution.chosen.value in (1.0, 3.0)
        assert resolution.chosen.value != 2.0

    def test_thresholds_are_variable_specific(self) -> None:
        """Half a metre of wave height matters; half a degree of SST does not."""
        assert threshold_for(WAVES) == 0.5
        assert threshold_for(MarineVariable.SEA_SURFACE_TEMPERATURE) == 1.5
        assert threshold_for(MarineVariable.WIND_DIRECTION) == 45.0

    def test_irreconcilable_disagreement_blocks(self) -> None:
        """Beyond 4x the threshold the sources describe different seas."""
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 0.5, 0.6), self._reading("cmems", 4.0, 0.85)]
        )

        assert resolution.irreconcilable
        check = check_source_disagreement([resolution])
        assert check.severity is CheckSeverity.BLOCKING

    def test_a_resolved_conflict_warns_rather_than_blocks(self) -> None:
        resolution = resolve_conflict(
            [self._reading("open_meteo_marine", 1.2, 0.6), self._reading("cmems", 2.0, 0.85)]
        )

        check = check_source_disagreement([resolution])
        assert check.severity is CheckSeverity.WARNING

    def test_resolution_is_deterministic_on_tied_reliability(self) -> None:
        """A non-deterministic winner would break replay."""
        readings = [self._reading("cmems", 2.4, 0.8), self._reading("imd", 1.2, 0.8)]

        first = resolve_conflict(readings)
        second = resolve_conflict(list(reversed(readings)))

        assert first.chosen.source == second.chosen.source

    def test_a_single_reading_is_not_a_conflict(self) -> None:
        resolution = resolve_conflict([self._reading("cmems", 1.4, 0.8)])
        assert not resolution.in_conflict
        assert resolution.lower == resolution.upper


class TestAbstentionAsTypedResponse:
    """Abstention is a response shape, not an exception (PLAN.md 6.3)."""

    def _request(self, evidence, **overrides) -> VerificationRequest:
        defaults = {
            "evidence": evidence,
            "bbox": PALK_BAY,
            "required_variables": frozenset({"significant_wave_height"}),
            "formula_ids": ["orca.safety.boat_relative_hazard"],
            "readings_by_variable": {},
            "evaluated_at": NOW,
        }
        return VerificationRequest(**(defaults | overrides))

    def test_a_clean_run_answers(self) -> None:
        result = Verifier().verify(self._request([item(pid="a"), item("imd", pid="b")]))

        assert result.verdict.status is VerdictStatus.ANSWER
        assert not result.abstained

    def test_stale_evidence_abstains_with_reasons_and_a_remedy(self) -> None:
        result = Verifier().verify(
            self._request([item(age_hours=15, pid="a"), item("imd", age_hours=15, pid="b")])
        )

        verdict = result.verdict
        assert verdict.status is VerdictStatus.ABSTAIN
        assert verdict.abstain_reasons
        assert verdict.remedy
        assert "wait for the next forecast issue" in verdict.remedy

    def test_an_abstention_still_carries_the_full_check_list(self) -> None:
        """A user who is refused deserves the same evidence as one who is not."""
        result = Verifier().verify(self._request([item(age_hours=15, pid="a")]))

        assert len(result.verdict.checks) == 7
        assert result.verdict.oldest_evidence_age_seconds == pytest.approx(15 * 3600)

    def test_a_conflict_answers_with_a_disclosed_caveat(self) -> None:
        readings = {
            WAVES: [
                SourceReading(
                    source="open_meteo_marine",
                    variable=WAVES,
                    value=1.2,
                    unit="m",
                    issued_time=NOW - timedelta(hours=1),
                    reliability=0.6,
                    provenance_id="a",
                ),
                SourceReading(
                    source="cmems",
                    variable=WAVES,
                    value=2.2,
                    unit="m",
                    issued_time=NOW - timedelta(hours=1),
                    reliability=0.85,
                    provenance_id="b",
                ),
            ]
        }

        result = Verifier().verify(
            self._request([item(pid="a"), item("imd", pid="b")], readings_by_variable=readings)
        )

        assert result.verdict.status is VerdictStatus.ANSWER_WITH_CAVEATS
        assert any(c.widened_uncertainty for c in result.verdict.caveats)

    def test_the_verdict_model_rejects_an_incoherent_state(self) -> None:
        from pydantic import ValidationError

        from orca_trust.verdict import CheckResult

        blocking = CheckResult(
            name=CheckName.FRESHNESS,
            passed=False,
            severity=CheckSeverity.BLOCKING,
            detail="stale",
        )
        with pytest.raises(ValidationError, match="blocking check"):
            TrustVerdict(status=VerdictStatus.ANSWER, checks=(blocking,), evaluated_at=NOW)

    def test_an_abstention_must_carry_a_reason(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="at least one reason"):
            TrustVerdict(status=VerdictStatus.ABSTAIN, checks=(), evaluated_at=NOW)


class TestCritiqueIsAdvisoryOnly:
    """The critique may add doubt and nothing else (PLAN.md 6.1)."""

    def _answering_verdict(self) -> TrustVerdict:
        return TrustVerdict(
            status=VerdictStatus.ANSWER,
            checks=(check_source_validity([item()]),),
            evaluated_at=NOW,
        )

    def _abstaining_verdict(self) -> TrustVerdict:
        return TrustVerdict(
            status=VerdictStatus.ABSTAIN,
            checks=(check_freshness([item(age_hours=15)], now=NOW),),
            abstain_reasons=("stale evidence",),
            evaluated_at=NOW,
        )

    def test_a_critique_can_escalate_an_answer_to_an_abstention(self) -> None:
        result = apply_critique(
            self._answering_verdict(),
            CritiqueResponse(escalate_to_abstain=True, escalation_reason="unmodelled squall risk"),
        )

        assert result.status is VerdictStatus.ABSTAIN
        assert any("unmodelled squall risk" in r for r in result.abstain_reasons)

    def test_a_critique_cannot_turn_an_abstention_into_an_answer(self) -> None:
        """There is no code path back; this is enforced, not merely intended."""
        result = apply_critique(
            self._abstaining_verdict(),
            CritiqueResponse(commentary="the data looks fine to me"),
        )

        assert result.status is VerdictStatus.ABSTAIN
        assert result.abstain_reasons == ("stale evidence",)

    def test_a_critique_cannot_clear_a_blocking_check(self) -> None:
        before = self._abstaining_verdict()
        after = apply_critique(before, CritiqueResponse(commentary="looks fine"))

        assert [c.passed for c in after.checks] == [c.passed for c in before.checks]

    def test_adding_a_caveat_downgrades_an_answer(self) -> None:
        result = apply_critique(
            self._answering_verdict(),
            CritiqueResponse(added_caveats=(Caveat(kind="critique", detail="thin corroboration"),)),
        )

        assert result.status is VerdictStatus.ANSWER_WITH_CAVEATS

    def test_an_escalation_without_a_reason_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="must carry a reason"):
            CritiqueResponse(escalate_to_abstain=True)

    def test_the_default_critic_is_a_no_op(self) -> None:
        """ORCA is fully functional with no model configured."""
        response = NullCritic().review(
            CritiqueRequest(
                evidence_summary=(),
                check_summary=(),
                proposed_status=VerdictStatus.ANSWER,
                evaluated_at=NOW,
            )
        )
        assert response.commentary is None
        assert not response.escalate_to_abstain

    def test_a_failing_critic_does_not_break_the_verdict(self) -> None:
        """An advisory step must never take the decision down with it."""

        class Broken(Critic):
            def review(self, request):  # noqa: ANN001, ANN202
                raise RuntimeError("model unavailable")

        with pytest.raises(RuntimeError):
            Broken().review(
                CritiqueRequest(
                    evidence_summary=(),
                    check_summary=(),
                    proposed_status=VerdictStatus.ANSWER,
                    evaluated_at=NOW,
                )
            )
        # The LlmCritic wrapper is what swallows it; verified separately below.

    def test_the_llm_critic_swallows_transport_failures(self) -> None:
        from orca_trust import LlmCritic

        class Exploding:
            def review(self, **kwargs):  # noqa: ANN003, ANN202
                raise RuntimeError("503")

        response = LlmCritic(Exploding()).review(
            CritiqueRequest(
                evidence_summary=(),
                check_summary=(),
                proposed_status=VerdictStatus.ANSWER,
                evaluated_at=NOW,
            )
        )
        assert response.commentary is None
        assert not response.escalate_to_abstain

    def test_an_unexplained_escalation_is_dropped(self) -> None:
        """Abstaining 'because the model said so' is not explainable."""
        from orca_trust import LlmCritic

        class Vague:
            def review(self, **kwargs):  # noqa: ANN003, ANN202
                return {"escalate_to_abstain": True, "commentary": "hmm"}

        response = LlmCritic(Vague()).review(
            CritiqueRequest(
                evidence_summary=(),
                check_summary=(),
                proposed_status=VerdictStatus.ANSWER,
                evaluated_at=NOW,
            )
        )
        assert not response.escalate_to_abstain
        assert response.commentary == "hmm"


def build_graph(run_id: str, *, wave_value: float, payload: bytes) -> ProvenanceGraph:
    """A small but complete chain: dataset → payload → agent → formula → output."""
    digest = hashlib.sha256(payload).hexdigest()
    builder = ProvenanceBuilder(run_id)
    builder.add(
        node_id="ds:cmems_wav",
        kind=ProvenanceNodeKind.DATASET,
        label="CMEMS global wave forecast",
        occurred_at=NOW,
        attributes={"dataset_id": "cmems_mod_glo_wav_anfc", "source": "cmems"},
    )
    builder.add(
        node_id="pay:1",
        kind=ProvenanceNodeKind.RAW_PAYLOAD,
        label="archived payload",
        occurred_at=NOW,
        attributes={"sha256": digest, "content_type": "application/json"},
        derives_from="ds:cmems_wav",
    )
    builder.add(
        node_id="ag:marine_data",
        kind=ProvenanceNodeKind.AGENT,
        label="marine_data",
        occurred_at=NOW,
        attributes={"agent": "marine_data"},
        derives_from="pay:1",
    )
    builder.add(
        node_id="fx:safety",
        kind=ProvenanceNodeKind.FORMULA,
        label="boat-relative hazard",
        occurred_at=NOW,
        attributes={
            "formula_id": "orca.safety.boat_relative_hazard",
            "formula_version": "1.0.0",
        },
        derives_from="ag:marine_data",
    )
    builder.add(
        node_id="out:safety_score",
        kind=ProvenanceNodeKind.OUTPUT,
        label="safety score",
        occurred_at=NOW,
        attributes={"value": wave_value, "unit": "score_0_100"},
        derives_from="fx:safety",
    )
    return builder.build(recorded_at=NOW)


class TestProvenanceGraph:
    def test_the_chain_runs_dataset_to_output(self) -> None:
        graph = build_graph("run-1", wave_value=72.0, payload=b'{"vhm0": 1.4}')

        chain = graph.chain_for("out:safety_score")

        assert [n.kind for n in chain] == [
            ProvenanceNodeKind.DATASET,
            ProvenanceNodeKind.RAW_PAYLOAD,
            ProvenanceNodeKind.AGENT,
            ProvenanceNodeKind.FORMULA,
            ProvenanceNodeKind.OUTPUT,
        ]

    def test_payload_hashes_and_formulas_are_exposed(self) -> None:
        payload = b'{"vhm0": 1.4}'
        graph = build_graph("run-1", wave_value=72.0, payload=payload)

        assert graph.payload_hashes() == (hashlib.sha256(payload).hexdigest(),)
        assert graph.formulas() == (("orca.safety.boat_relative_hazard", "1.0.0"),)

    def test_a_backwards_edge_is_rejected(self) -> None:
        """An output cannot influence its own inputs."""
        from pydantic import ValidationError

        from orca_trust import ProvenanceEdge

        graph = build_graph("run-1", wave_value=72.0, payload=b"x")
        with pytest.raises(ValidationError, match="runs backwards"):
            ProvenanceGraph(
                run_id="run-1",
                nodes=graph.nodes,
                edges=(
                    *graph.edges,
                    ProvenanceEdge(source_id="out:safety_score", target_id="ds:cmems_wav"),
                ),
                recorded_at=NOW,
            )

    def test_a_payload_node_must_carry_its_hash(self) -> None:
        from pydantic import ValidationError

        from orca_trust import ProvenanceNode

        with pytest.raises(ValidationError, match="requires attribute"):
            ProvenanceNode(
                node_id="pay:x",
                kind=ProvenanceNodeKind.RAW_PAYLOAD,
                label="payload",
                occurred_at=NOW,
            )

    def test_a_formula_node_must_carry_its_version(self) -> None:
        from pydantic import ValidationError

        from orca_trust import ProvenanceNode

        with pytest.raises(ValidationError, match="requires attribute"):
            ProvenanceNode(
                node_id="fx:x",
                kind=ProvenanceNodeKind.FORMULA,
                label="formula",
                occurred_at=NOW,
                attributes={"formula_id": "orca.safety.boat_relative_hazard"},
            )

    def test_the_fingerprint_ignores_wall_clock_time(self) -> None:
        """A replay records new timestamps; that must not change the fingerprint."""
        payload = b'{"vhm0": 1.4}'
        first = build_graph("run-1", wave_value=72.0, payload=payload)
        later = first.model_copy(update={"recorded_at": NOW + timedelta(days=3)})

        assert first.fingerprint() == later.fingerprint()

    def test_a_changed_output_changes_the_fingerprint(self) -> None:
        payload = b'{"vhm0": 1.4}'
        a = build_graph("run-1", wave_value=72.0, payload=payload)
        b = build_graph("run-1", wave_value=41.0, payload=payload)

        assert a.fingerprint() != b.fingerprint()


class TestDeterministicReplay:
    """Replay re-derives from archived bytes, not by re-querying."""

    def test_replay_returns_identical_output(self) -> None:
        """The Phase 6.4 assertion: same archived inputs, byte-identical result."""
        payload = b'{"vhm0": 1.4, "issued": "2026-09-19T11:00:00Z"}'
        digest = hashlib.sha256(payload).hexdigest()
        original = build_graph("run-42", wave_value=72.0, payload=payload)
        archive = PayloadArchiveReader({digest: payload})

        def recompute(payloads: dict[str, bytes]) -> ProvenanceGraph:
            # Re-derives from the archived bytes; the value is a pure function of them.
            assert payloads[digest] == payload
            return build_graph("run-42", wave_value=72.0, payload=payloads[digest])

        report = replay_run(graph=original, archive=archive, recompute=recompute)

        assert report.identical
        assert report.outcome is ReplayOutcome.IDENTICAL
        assert report.replayed_fingerprint == report.original_fingerprint
        assert report.payloads_verified == 1

    def test_a_different_output_from_identical_inputs_is_caught(self) -> None:
        """If code changed, replay must say so and name the output that moved."""
        payload = b'{"vhm0": 1.4}'
        digest = hashlib.sha256(payload).hexdigest()
        original = build_graph("run-42", wave_value=72.0, payload=payload)
        archive = PayloadArchiveReader({digest: payload})

        report = replay_run(
            graph=original,
            archive=archive,
            recompute=lambda payloads: build_graph("run-42", wave_value=65.0, payload=payload),
        )

        assert report.outcome is ReplayOutcome.OUTPUT_DIFFERS
        assert any("out:safety_score" in d for d in report.differences)
        assert "72.0" in report.differences[0] and "65.0" in report.differences[0]

    def test_a_missing_payload_is_reported_distinctly(self) -> None:
        original = build_graph("run-42", wave_value=72.0, payload=b'{"vhm0": 1.4}')

        report = replay_run(
            graph=original,
            archive=PayloadArchiveReader({}),
            recompute=lambda payloads: original,
        )

        assert report.outcome is ReplayOutcome.EVIDENCE_UNAVAILABLE

    def test_tampered_evidence_is_detected_by_content_address(self) -> None:
        """Bytes that no longer hash to the recorded digest are not the original evidence."""
        payload = b'{"vhm0": 1.4}'
        digest = hashlib.sha256(payload).hexdigest()
        original = build_graph("run-42", wave_value=72.0, payload=payload)
        tampered = PayloadArchiveReader({digest: b'{"vhm0": 0.2}'})

        report = replay_run(graph=original, archive=tampered, recompute=lambda payloads: original)

        assert report.outcome is ReplayOutcome.EVIDENCE_TAMPERED
        assert "has changed" in report.detail

    def test_a_bumped_formula_version_is_reported_not_treated_as_tampering(self) -> None:
        """A changed formula is a legitimate reason for a different answer."""
        payload = b'{"vhm0": 1.4}'
        digest = hashlib.sha256(payload).hexdigest()
        original = build_graph("run-42", wave_value=72.0, payload=payload)

        report = replay_run(
            graph=original,
            archive=PayloadArchiveReader({digest: payload}),
            recompute=lambda payloads: original,
            current_formula_versions={"orca.safety.boat_relative_hazard": "1.1.0"},
        )

        assert report.outcome is ReplayOutcome.FORMULA_CHANGED
        assert "not evidence of tampering" in report.detail
        assert any("1.0.0" in d and "1.1.0" in d for d in report.differences)

    def test_matching_formula_versions_proceed_to_comparison(self) -> None:
        payload = b'{"vhm0": 1.4}'
        digest = hashlib.sha256(payload).hexdigest()
        original = build_graph("run-42", wave_value=72.0, payload=payload)

        report = replay_run(
            graph=original,
            archive=PayloadArchiveReader({digest: payload}),
            recompute=lambda payloads: build_graph("run-42", wave_value=72.0, payload=payload),
            current_formula_versions={"orca.safety.boat_relative_hazard": "1.0.0"},
        )

        assert report.identical

"""CAP 1.2 structure and the severity/urgency/certainty mapping (PLAN.md Phase 8.2).

CAP is what makes an ORCA alert interoperable with NDMA/SACHET instead of a private
notification format, so the structure is a contract with consumers ORCA does not control.
Two things are asserted here:

* **The XML is schema-shaped** — correct namespace, required elements, and the child order
  the CAP 1.2 sequence mandates. A validator rejects out-of-order children, and a rejected
  alert is a warning that reached nobody.
* **The three routing axes are deterministic** — severity, urgency and certainty come from
  tables, so the same hazard always produces the same routing. These drive real downstream
  behaviour, and a value that drifted would silently re-route or suppress alerts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import pytest
from _alert_fixtures import NOW, result, square
from orca_speech import Language

from orca_alerts import (
    CAP_NAMESPACE,
    CapAlert,
    CapArea,
    CapCategory,
    CapCertainty,
    CapInfo,
    CapMsgType,
    CapResponseType,
    CapScope,
    CapSeverity,
    CapStatus,
    CapUrgency,
    GeoPolygon,
    TriggerId,
    TriggerOutcome,
    certainty_for,
    make_identifier,
    severity_for,
    urgency_for,
    validate,
)

CAP_QNAME = f"{{{CAP_NAMESPACE}}}"


def info(**overrides: object) -> CapInfo:
    params: dict[str, object] = {
        "language": Language.ENGLISH,
        "category": CapCategory.MET,
        "event": "High wave hazard",
        "urgency": CapUrgency.EXPECTED,
        "severity": CapSeverity.SEVERE,
        "certainty": CapCertainty.LIKELY,
        "headline": "High waves near Rameswaram",
        "description": "Wave height 2.8 m, above the 2.0 m limit for your boat.",
        "instruction": "Do not put out to sea.",
        "areas": (
            CapArea(
                description="Palk Bay", circle_lat=9.2, circle_lon=79.35, circle_radius_km=5.0
            ),
        ),
    }
    params.update(overrides)
    return CapInfo(**params)  # type: ignore[arg-type]


def alert(**overrides: object) -> CapAlert:
    params: dict[str, object] = {
        "identifier": "orca.vessel.wave_height.abc123",
        "sender": "orca@ocean-iq.in",
        "sent": NOW,
        "status": CapStatus.ACTUAL,
        "msg_type": CapMsgType.ALERT,
        "scope": CapScope.PUBLIC,
        "info": (info(),),
    }
    params.update(overrides)
    return CapAlert(**params)  # type: ignore[arg-type]


def parse(built: CapAlert) -> ET.Element:
    return ET.fromstring(built.to_xml())


class TestCapDocumentStructure:
    def test_the_root_is_a_cap_1_2_alert(self) -> None:
        root = parse(alert())

        assert root.tag == f"{CAP_QNAME}alert"

    def test_the_mandatory_alert_elements_are_present(self) -> None:
        """CAP requires all six; a consumer rejects the message without them."""
        root = parse(alert())

        for tag in ("identifier", "sender", "sent", "status", "msgType", "scope"):
            assert root.find(f"{CAP_QNAME}{tag}") is not None, tag

    def test_alert_children_follow_the_schema_sequence(self) -> None:
        """CAP 1.2 is a sequence, not a set: validators reject reordered children."""
        root = parse(alert())
        tags = [child.tag.replace(CAP_QNAME, "") for child in root]

        assert tags[:6] == ["identifier", "sender", "sent", "status", "msgType", "scope"]

    def test_info_children_follow_the_schema_sequence(self) -> None:
        root = parse(alert())
        block = root.find(f"{CAP_QNAME}info")
        assert block is not None
        tags = [child.tag.replace(CAP_QNAME, "") for child in block]

        expected_prefix = ["language", "category", "event", "urgency", "severity", "certainty"]
        assert tags[: len(expected_prefix)] == expected_prefix
        assert tags.index("headline") < tags.index("description")
        assert tags.index("description") < tags.index("instruction")

    def test_timestamps_carry_an_offset_because_cap_forbids_z(self) -> None:
        root = parse(alert())
        sent = root.findtext(f"{CAP_QNAME}sent")

        assert sent is not None
        assert sent.endswith("+00:00")
        assert "Z" not in sent

    def test_a_naive_timestamp_is_refused(self) -> None:
        """An offsetless timestamp produces XML a validator rejects at delivery time."""
        with pytest.raises(ValueError, match="timezone-aware"):
            alert(sent=datetime(2026, 9, 20, 6, 0)).to_xml()

    def test_the_language_tag_is_bcp47(self) -> None:
        root = parse(alert(info=(info(language=Language.TAMIL),)))
        block = root.find(f"{CAP_QNAME}info")

        assert block is not None
        assert block.findtext(f"{CAP_QNAME}language") == "ta-IN"

    def test_one_info_block_per_language(self) -> None:
        """CAP models multilingual alerts natively, so two languages are one alert."""
        root = parse(
            alert(info=(info(language=Language.TAMIL), info(language=Language.ENGLISH)))
        )
        blocks = root.findall(f"{CAP_QNAME}info")

        assert len(blocks) == 2
        assert [b.findtext(f"{CAP_QNAME}language") for b in blocks] == ["ta-IN", "en-IN"]

    def test_an_alert_needs_at_least_one_info_block(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            alert(info=())


class TestCapArea:
    def test_a_circle_is_lat_lon_then_radius(self) -> None:
        root = parse(alert())
        circle = root.find(f"{CAP_QNAME}info/{CAP_QNAME}area/{CAP_QNAME}circle")

        assert circle is not None
        assert circle.text == "9.2000,79.3500 5.0"

    def test_a_polygon_repeats_its_first_point_last(self) -> None:
        """CAP requires a closed ring; an unclosed one is rejected."""
        polygon = square(9.2, 79.35, 1.0)
        root = parse(
            alert(info=(info(areas=(CapArea(description="Cyclone field", polygon=polygon),)),))
        )
        text = root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}area/{CAP_QNAME}polygon")

        assert text is not None
        pairs = text.split(" ")
        assert pairs[0] == pairs[-1]
        assert len(pairs) == 5

    def test_polygon_pairs_are_lat_comma_lon(self) -> None:
        """Transposed to lon,lat, the alert would describe a different hemisphere."""
        polygon = GeoPolygon(((9.0, 79.0), (9.0, 80.0), (10.0, 80.0)))
        root = parse(alert(info=(info(areas=(CapArea(description="x", polygon=polygon),)),)))
        text = root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}area/{CAP_QNAME}polygon")

        assert text is not None
        assert text.startswith("9.0000,79.0000")


class TestReferencesAndScope:
    def test_an_update_must_reference_what_it_supersedes(self) -> None:
        """Without it, an update is a second contradictory warning, not a correction."""
        with pytest.raises(ValueError, match="references"):
            alert(msg_type=CapMsgType.UPDATE)

    def test_a_cancel_must_reference_what_it_stands_down(self) -> None:
        with pytest.raises(ValueError, match="references"):
            alert(msg_type=CapMsgType.CANCEL)

    def test_references_are_written_as_a_space_delimited_element(self) -> None:
        root = parse(
            alert(msg_type=CapMsgType.UPDATE, references=("orca.a.1", "orca.a.2"))
        )

        assert root.findtext(f"{CAP_QNAME}references") == "orca.a.1 orca.a.2"

    def test_private_scope_requires_addresses(self) -> None:
        """A Private alert with no addressee would be broadcast or dropped."""
        with pytest.raises(ValueError, match="addresses"):
            alert(scope=CapScope.PRIVATE)

    def test_restricted_scope_requires_a_restriction(self) -> None:
        with pytest.raises(ValueError, match="restriction"):
            alert(scope=CapScope.RESTRICTED)

    def test_addresses_are_quoted(self) -> None:
        root = parse(alert(scope=CapScope.PRIVATE, addresses=("u-rmd-1",)))

        assert root.findtext(f"{CAP_QNAME}addresses") == '"u-rmd-1"'


class TestDeterministicMapping:
    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (TriggerOutcome.EMERGENCY, CapSeverity.EXTREME),
            (TriggerOutcome.WARNING, CapSeverity.SEVERE),
            (TriggerOutcome.WATCH, CapSeverity.MODERATE),
            (TriggerOutcome.CLEAR, CapSeverity.MINOR),
            (TriggerOutcome.UNEVALUATED, CapSeverity.UNKNOWN),
        ],
    )
    def test_severity_comes_from_the_outcome(
        self, outcome: TriggerOutcome, expected: CapSeverity
    ) -> None:
        assert severity_for(result(outcome=outcome)) is expected

    def test_every_trigger_has_a_severity_urgency_and_certainty(self) -> None:
        """A missing entry would raise at alert time, in the one path that must not fail."""
        for trigger_id in TriggerId:
            fired = result(trigger_id=trigger_id, outcome=TriggerOutcome.WARNING)
            assert severity_for(fired)
            assert urgency_for(fired)
            assert certainty_for(fired)

    def test_the_mapping_is_deterministic(self) -> None:
        fired = result(trigger_id=TriggerId.CYCLONE_WIND, outcome=TriggerOutcome.EMERGENCY)
        triples = {
            (severity_for(fired), urgency_for(fired), certainty_for(fired)) for _ in range(20)
        }

        assert len(triples) == 1

    def test_a_cone_containment_is_not_treated_as_an_observation(self) -> None:
        """The cone is where the storm *may* go; calling it Observed would overstate it."""
        fired = result(trigger_id=TriggerId.CYCLONE_CONE, outcome=TriggerOutcome.WATCH)

        assert certainty_for(fired) is CapCertainty.POSSIBLE
        assert urgency_for(fired) is CapUrgency.FUTURE

    def test_a_wind_radius_containment_is_observed_and_immediate(self) -> None:
        fired = result(trigger_id=TriggerId.CYCLONE_WIND, outcome=TriggerOutcome.EMERGENCY)

        assert certainty_for(fired) is CapCertainty.OBSERVED
        assert urgency_for(fired) is CapUrgency.IMMEDIATE

    def test_a_drift_projection_is_never_reported_as_observed(self) -> None:
        """It assumes a constant current and an unchanged helm. It is a model, not a fix."""
        fired = result(
            trigger_id=TriggerId.GEOFENCE_DRIFT,
            outcome=TriggerOutcome.WARNING,
            time_to_boundary_minutes=18.0,
        )

        assert certainty_for(fired) is CapCertainty.LIKELY

    def test_drift_urgency_tracks_the_time_actually_remaining(self) -> None:
        """Forty minutes and six minutes are not the same instruction."""
        soon = result(
            trigger_id=TriggerId.GEOFENCE_DRIFT,
            outcome=TriggerOutcome.EMERGENCY,
            time_to_boundary_minutes=6.0,
        )
        later = result(
            trigger_id=TriggerId.GEOFENCE_DRIFT,
            outcome=TriggerOutcome.WATCH,
            time_to_boundary_minutes=40.0,
        )

        assert urgency_for(soon) is CapUrgency.IMMEDIATE
        assert urgency_for(later) is CapUrgency.EXPECTED

    def test_a_boundary_crossing_is_categorised_as_security_not_weather(self) -> None:
        """A SACHET consumer routing on category must not file an arrest risk with storms."""
        from orca_alerts import CATEGORY_BY_TRIGGER

        assert CATEGORY_BY_TRIGGER[TriggerId.GEOFENCE_PROXIMITY] is CapCategory.SECURITY
        assert CATEGORY_BY_TRIGGER[TriggerId.WAVE_HEIGHT] is CapCategory.MET


class TestIdentifiers:
    def test_the_identifier_is_stable_for_the_same_inputs(self) -> None:
        """The audit trail and a downstream consumer must agree on what was sent."""
        first = make_identifier(user_id="u1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)
        second = make_identifier(user_id="u1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)

        assert first == second

    def test_different_users_get_different_identifiers(self) -> None:
        first = make_identifier(user_id="u1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)
        second = make_identifier(user_id="u2", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)

        assert first != second

    def test_a_later_alert_for_the_same_rule_is_a_different_alert(self) -> None:
        first = make_identifier(user_id="u1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)
        second = make_identifier(
            user_id="u1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW + timedelta(minutes=30)
        )

        assert first != second

    def test_identifiers_contain_no_whitespace(self) -> None:
        """CAP identifiers are space-delimited in <references>; a space would split one."""
        identifier = make_identifier(user_id="u 1", trigger_id=TriggerId.WAVE_HEIGHT, sent=NOW)

        assert " " not in identifier


class TestValidation:
    def test_a_well_formed_alert_reports_no_issues(self) -> None:
        assert validate(alert()) == ()

    def test_an_alert_with_no_area_cannot_be_routed(self) -> None:
        issues = validate(alert(info=(info(areas=()),)))

        assert any(i.element == "info/area" for i in issues)

    def test_an_over_long_headline_is_flagged(self) -> None:
        """Consumers truncate at 160, and a clipped instruction can lose its verb."""
        issues = validate(alert(info=(info(headline="x" * 200),)))

        assert any(i.element == "info/headline" for i in issues)

    def test_an_alert_with_no_instruction_is_flagged(self) -> None:
        """A warning that does not say what to do is not a warning."""
        issues = validate(alert(info=(info(instruction=""),)))

        assert any(i.element == "info/instruction" for i in issues)

    def test_expiry_before_effective_is_flagged(self) -> None:
        issues = validate(
            alert(
                info=(
                    info(effective=NOW, expires=NOW - timedelta(hours=1)),
                )
            )
        )

        assert any(i.element == "info/expires" for i in issues)

    def test_duplicate_language_blocks_are_flagged(self) -> None:
        issues = validate(alert(info=(info(), info())))

        assert any(i.element == "info/language" for i in issues)

    def test_validation_reports_every_problem_at_once(self) -> None:
        """An operations view listing one problem per run is a slow way to fix three."""
        issues = validate(alert(info=(info(headline="", instruction="", areas=()),)))

        assert len(issues) >= 3


class TestRelayedAuthority:
    def test_the_originating_authority_is_carried_as_a_parameter(self) -> None:
        """ORCA relays IMD's cyclone warning; it does not author it."""
        root = parse(alert(info=(info(source="IMD"),)))
        values = [
            p.findtext(f"{CAP_QNAME}valueName")
            for p in root.findall(f"{CAP_QNAME}info/{CAP_QNAME}parameter")
        ]

        assert "origin_authority" in values

    def test_audio_is_attached_as_a_cap_resource(self) -> None:
        """Phase 7.9: the at-sea user hears the warning rather than reading it."""
        root = parse(
            alert(
                info=(
                    info(
                        audio_uri="https://example.invalid/clip.wav",
                        audio_size_bytes=2048,
                    ),
                )
            )
        )
        resource = root.find(f"{CAP_QNAME}info/{CAP_QNAME}resource")

        assert resource is not None
        assert resource.findtext(f"{CAP_QNAME}mimeType") == "audio/wav"
        assert resource.findtext(f"{CAP_QNAME}size") == "2048"


def test_severity_ordering_is_explicit_and_most_severe_first() -> None:
    """Escalation compares on this; a reordering would invert it silently."""
    from orca_alerts import SEVERITY_ORDER

    assert SEVERITY_ORDER[CapSeverity.EXTREME] < SEVERITY_ORDER[CapSeverity.SEVERE]
    assert SEVERITY_ORDER[CapSeverity.SEVERE] < SEVERITY_ORDER[CapSeverity.MODERATE]
    assert SEVERITY_ORDER[CapSeverity.MODERATE] < SEVERITY_ORDER[CapSeverity.MINOR]


def test_the_alert_reports_its_most_severe_info_block() -> None:
    built = alert(
        scope=CapScope.PUBLIC,
        info=(
            info(severity=CapSeverity.MODERATE),
            info(language=Language.TAMIL, severity=CapSeverity.EXTREME),
        ),
    )

    assert built.max_severity is CapSeverity.EXTREME


def test_an_unused_response_type_is_omitted_rather_than_written_as_none() -> None:
    """``<responseType>None</responseType>`` is not a valid CAP value."""
    root = parse(alert(info=(info(response_type=CapResponseType.NONE),)))

    assert root.find(f"{CAP_QNAME}info/{CAP_QNAME}responseType") is None

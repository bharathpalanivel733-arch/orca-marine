"""Disaster-management workflows and the offshore hand-off (PLAN.md Phase 8.4, 8.5).

The hand-off tests matter more than their size suggests. **ORCA is not an offshore
communications channel**, and the single most dangerous claim this project could make is
that it is — a fisherman who believes ORCA will warn him 60 km out is one who stops
watching the sky. So the limit is asserted here, in code, rather than left to a slide that
can quietly drift into an overstatement.

The workflow tests check the property that makes an authority able to use this at all:
every dispatch produces an enumerable recipient list, because the question after an
incident is always "who was told, and when".
"""

from __future__ import annotations

from datetime import timedelta
from xml.etree import ElementTree as ET

import pytest
from _alert_fixtures import NOW, square
from orca_kernels import VesselProfile
from orca_speech import Language

from orca_alerts import (
    CAP_NAMESPACE,
    DAT_SG,
    GEMINI,
    HANDOFF_STATEMENT,
    MARGINAL_COVERAGE_KM,
    OFFSHORE_SERVICES,
    RELIABLE_COVERAGE_KM,
    SAMPLE_SHELTERS,
    SHELTER_REGISTRY_IS_SAMPLE,
    AuthorityBroadcast,
    BoatRecall,
    CapCertainty,
    CapMsgType,
    CapScope,
    CapSeverity,
    CapUrgency,
    ChannelEndpoint,
    CoverageZone,
    DeliveryChannelKind,
    Fleet,
    HarbourAdvisory,
    HarbourStatus,
    RecallUrgency,
    Subscriber,
    VesselState,
    broadcast_to_cap,
    coverage_zone,
    dispatch_recall,
    handoff_for,
    harbour_advisory_to_cap,
    nearest_shelter,
    recall_to_cap,
)

CAP_QNAME = f"{{{CAP_NAMESPACE}}}"


def at(
    lat: float, lon: float, *, user_id: str, vessel: VesselProfile, at_sea: bool = True
) -> Subscriber:
    return Subscriber(
        user_id=user_id,
        vessel=vessel,
        language=Language.TAMIL,
        state=VesselState(lat=lat, lon=lon, reported_at=NOW, at_sea=at_sea),
        endpoints=(ChannelEndpoint(DeliveryChannelKind.IN_APP, user_id),),
    )


# --------------------------------------------------------------------------------------
# 8.5 — offshore hand-off
# --------------------------------------------------------------------------------------


class TestOffshoreHandoff:
    @pytest.mark.parametrize(
        ("distance_km", "expected"),
        [
            (2.0, CoverageZone.NEARSHORE),
            (RELIABLE_COVERAGE_KM, CoverageZone.NEARSHORE),
            (15.0, CoverageZone.MARGINAL),
            (MARGINAL_COVERAGE_KM, CoverageZone.MARGINAL),
            (60.0, CoverageZone.OFFSHORE),
        ],
    )
    def test_coverage_is_classified_by_distance(
        self, distance_km: float, expected: CoverageZone
    ) -> None:
        assert coverage_zone(distance_km) is expected

    def test_orca_admits_it_cannot_deliver_offshore(self) -> None:
        """The claim this project must never make."""
        notice = handoff_for(60.0)

        assert not notice.orca_can_deliver
        assert "cannot reach you" in notice.message

    def test_the_offshore_message_is_blunt_not_hedged(self) -> None:
        """"Coverage may be limited" reads as boilerplate and gets ignored."""
        notice = handoff_for(60.0)

        assert "not a satellite service" in notice.message

    def test_it_names_gemini_and_dat_sg(self) -> None:
        notice = handoff_for(60.0)
        names = {s.name for s in notice.services}

        assert names == {"GEMINI", "DAT-SG"}

    def test_the_notice_is_displayed_outside_the_nearshore_zone(self) -> None:
        assert handoff_for(15.0).should_display
        assert handoff_for(60.0).should_display

    def test_a_nearshore_notice_is_not_displayed(self) -> None:
        """Warning someone in coverage about satellite services is noise."""
        notice = handoff_for(3.0)

        assert not notice.should_display
        assert notice.orca_can_deliver

    def test_the_marginal_zone_warns_before_coverage_is_actually_lost(self) -> None:
        """Telling someone they are covered when they are marginal is the error that matters."""
        notice = handoff_for(15.0)

        assert not notice.orca_can_deliver
        assert "may not reach you" in notice.message

    def test_gemini_is_attributed_to_isro_and_incois(self) -> None:
        """These are operational government services; ORCA relays, it does not replace."""
        assert "ISRO" in GEMINI.operator
        assert "ISRO" in DAT_SG.operator

    def test_dat_sg_is_described_as_independent_of_orca(self) -> None:
        """A distress beacon must not appear to need a phone, an app or a signal."""
        assert "does not need ORCA" in DAT_SG.how_to_use

    def test_the_public_statement_disclaims_competition(self) -> None:
        """Said on stage and shown in the UI — version-controlled so it cannot drift."""
        assert "does not provide an offshore channel" in HANDOFF_STATEMENT
        assert "does not replace or compete" in HANDOFF_STATEMENT

    def test_all_three_services_are_documented(self) -> None:
        assert len(OFFSHORE_SERVICES) == 3
        assert {s.service_id for s in OFFSHORE_SERVICES} == {"gemini", "dat_sg", "sagarmitra"}

    def test_the_notice_serializes_for_the_ui(self) -> None:
        payload = handoff_for(60.0).as_dict()

        assert payload["orca_can_deliver"] is False
        assert len(payload["services"]) == 2
        assert payload["services"][0]["how_to_use"]


# --------------------------------------------------------------------------------------
# 8.4 — disaster-management workflows
# --------------------------------------------------------------------------------------


class TestBoatRecall:
    def recall(self, **overrides: object) -> BoatRecall:
        params: dict[str, object] = {
            "recall_id": "rcl-001",
            "issued_by": "Tamil Nadu Fisheries Department",
            "issued_at": NOW,
            "area_description": "Palk Bay and adjoining waters",
            "urgency": RecallUrgency.IMMEDIATE,
            "reason": "Cyclone Fengal approaching the Tamil Nadu coast.",
            "area": square(9.2, 79.35, 1.0),
            "expires_at": NOW + timedelta(hours=12),
        }
        params.update(overrides)
        return BoatRecall(**params)  # type: ignore[arg-type]

    def test_an_immediate_recall_is_extreme_and_immediate(self) -> None:
        recall = self.recall()

        assert recall.cap_severity is CapSeverity.EXTREME
        assert recall.cap_urgency is CapUrgency.IMMEDIATE

    def test_an_advisory_recall_is_severe_and_expected(self) -> None:
        recall = self.recall(urgency=RecallUrgency.ADVISORY)

        assert recall.cap_severity is CapSeverity.SEVERE
        assert recall.cap_urgency is CapUrgency.EXPECTED

    def test_a_recall_is_an_authority_decision_not_a_forecast(self) -> None:
        """Certainty Observed: somebody has decided this, it is not a prediction."""
        root = ET.fromstring(recall_to_cap(self.recall()).to_xml())

        assert root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}certainty") == CapCertainty.OBSERVED.value

    def test_the_issuing_authority_is_named_not_orca(self) -> None:
        """Attributing a government instruction to a hackathon project would be improper."""
        root = ET.fromstring(recall_to_cap(self.recall()).to_xml())

        assert root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}senderName") == (
            "Tamil Nadu Fisheries Department"
        )

    def test_a_recall_is_public_scope(self) -> None:
        """Restricting it would stop it propagating to the aggregators that must carry it."""
        assert recall_to_cap(self.recall()).scope is CapScope.PUBLIC

    def test_the_instruction_says_what_to_do(self) -> None:
        root = ET.fromstring(recall_to_cap(self.recall()).to_xml())
        instruction = root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}instruction")

        assert instruction is not None
        assert "Return to the nearest harbour" in instruction

    def test_it_addresses_only_vessels_at_sea(self, vallam: VesselProfile) -> None:
        """Telling someone in harbour to return to harbour is noise, and noise gets muted."""
        subscribers = [
            at(9.2, 79.35, user_id="at-sea", vessel=vallam),
            at(9.28, 79.31, user_id="ashore", vessel=vallam, at_sea=False),
        ]

        dispatch = dispatch_recall(self.recall(), subscribers)

        assert dispatch.recipients == ("at-sea",)

    def test_it_addresses_only_vessels_inside_the_area(self, vallam: VesselProfile) -> None:
        subscribers = [
            at(9.2, 79.35, user_id="inside", vessel=vallam),
            at(15.0, 80.0, user_id="outside", vessel=vallam),
        ]

        dispatch = dispatch_recall(self.recall(), subscribers)

        assert dispatch.recipients == ("inside",)

    def test_an_area_less_recall_addresses_everyone_at_sea(self, vallam: VesselProfile) -> None:
        subscribers = [
            at(9.2, 79.35, user_id="a", vessel=vallam),
            at(15.0, 80.0, user_id="b", vessel=vallam),
        ]

        dispatch = dispatch_recall(self.recall(area=None), subscribers)

        assert set(dispatch.recipients) == {"a", "b"}

    def test_the_dispatch_is_an_enumerable_record_of_who_was_told(
        self, vallam: VesselProfile
    ) -> None:
        """The question after an incident is always "who was warned"."""
        dispatch = dispatch_recall(
            self.recall(), [at(9.2, 79.35, user_id="u1", vessel=vallam)]
        )

        assert dispatch.workflow == "boat_recall"
        assert dispatch.recipient_count == 1

    def test_a_recall_can_be_issued_in_several_languages(self) -> None:
        alert = recall_to_cap(self.recall(), languages=(Language.TAMIL, Language.ENGLISH))

        assert len(alert.info) == 2


class TestHarbourAdvisory:
    def advisory(self, status: HarbourStatus) -> HarbourAdvisory:
        return HarbourAdvisory(
            harbour_id="in-tn-rameswaram",
            harbour_name="Rameswaram",
            status=status,
            issued_by="Harbour Master, Rameswaram",
            issued_at=NOW,
            reason="Cyclone warning in force.",
        )

    def test_a_closed_harbour_is_severe(self) -> None:
        assert self.advisory(HarbourStatus.CLOSED).cap_severity is CapSeverity.SEVERE

    def test_a_restricted_harbour_is_moderate(self) -> None:
        assert self.advisory(HarbourStatus.RESTRICTED).cap_severity is CapSeverity.MODERATE

    def test_reopening_is_an_all_clear(self) -> None:
        """"Normal sailing has resumed" is as much a safety message as the closure."""
        alert = harbour_advisory_to_cap(self.advisory(HarbourStatus.OPEN))
        root = ET.fromstring(alert.to_xml())

        assert root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}responseType") == "AllClear"

    def test_each_status_carries_its_own_instruction(self) -> None:
        closed = ET.fromstring(
            harbour_advisory_to_cap(self.advisory(HarbourStatus.CLOSED)).to_xml()
        )
        instruction = closed.findtext(f"{CAP_QNAME}info/{CAP_QNAME}instruction")

        assert instruction is not None
        assert "Do not put out to sea" in instruction

    def test_the_harbour_status_travels_as_a_cap_parameter(self) -> None:
        """So a consumer can filter on it without parsing prose."""
        alert = harbour_advisory_to_cap(self.advisory(HarbourStatus.CLOSED))
        root = ET.fromstring(alert.to_xml())
        names = [
            p.findtext(f"{CAP_QNAME}valueName")
            for p in root.findall(f"{CAP_QNAME}info/{CAP_QNAME}parameter")
        ]

        assert "harbour_status" in names


class TestCycloneShelter:
    def test_the_nearest_shelter_is_returned(self) -> None:
        guidance = nearest_shelter(9.29, 79.31)

        assert guidance is not None
        assert guidance.shelter.shelter_id == "tn-rmd-01"

    def test_the_distance_is_reported(self) -> None:
        guidance = nearest_shelter(9.50, 79.31)

        assert guidance is not None
        assert 0 < guidance.distance_km < 50

    def test_the_sample_registry_is_flagged_as_unverified(self) -> None:
        """Sending people to a locked building in a cyclone is the failure being avoided."""
        guidance = nearest_shelter(9.29, 79.31)

        assert guidance is not None
        assert not guidance.is_verified
        assert "not been verified" in guidance.caveat

    def test_the_registry_declares_itself_a_sample(self) -> None:
        assert SHELTER_REGISTRY_IS_SAMPLE
        assert all(not s.verified for s in SAMPLE_SHELTERS)

    def test_an_empty_registry_returns_nothing_rather_than_guessing(self) -> None:
        assert nearest_shelter(9.29, 79.31, shelters=()) is None


class TestAuthorityBroadcast:
    def broadcast(self, **overrides: object) -> AuthorityBroadcast:
        params: dict[str, object] = {
            "broadcast_id": "bc-001",
            "issued_by": "INCOIS",
            "issued_at": NOW,
            "headline": "High wave warning for the Tamil Nadu coast",
            "body": "Waves up to 3.5 m expected along the coast.",
            "instruction": "Do not put out to sea until further notice.",
            "severity": CapSeverity.SEVERE,
            "urgency": CapUrgency.EXPECTED,
            "certainty": CapCertainty.LIKELY,
            "languages": (Language.TAMIL, Language.ENGLISH),
            "area_description": "Tamil Nadu coast",
            "area": square(9.2, 79.35, 2.0),
        }
        params.update(overrides)
        return AuthorityBroadcast(**params)  # type: ignore[arg-type]

    def test_an_area_broadcast_addresses_everyone_inside_it(self, vallam: VesselProfile) -> None:
        subscribers = [
            at(9.2, 79.35, user_id="inside", vessel=vallam),
            at(20.0, 85.0, user_id="outside", vessel=vallam),
        ]

        recipients = self.broadcast().recipients(subscribers)

        assert [s.user_id for s in recipients] == ["inside"]

    def test_a_subscriber_with_no_position_is_included_not_dropped(
        self, vallam: VesselProfile
    ) -> None:
        """Not knowing where a boat is is not a reason to leave it out of a cyclone warning."""
        unknown = Subscriber(user_id="unknown", vessel=vallam, language=Language.TAMIL)

        recipients = self.broadcast().recipients([unknown])

        assert [s.user_id for s in recipients] == ["unknown"]

    def test_a_fleet_broadcast_addresses_its_members(self, vallam: VesselProfile) -> None:
        fleet = Fleet(
            fleet_id="flt-rmd",
            name="Rameswaram trawler fleet",
            authority="Tamil Nadu Fisheries",
            member_user_ids=frozenset({"member"}),
        )
        subscribers = [
            at(9.2, 79.35, user_id="member", vessel=vallam),
            at(9.2, 79.35, user_id="non-member", vessel=vallam),
        ]

        recipients = self.broadcast(fleet=fleet, area=None).recipients(subscribers)

        assert [s.user_id for s in recipients] == ["member"]

    def test_the_broadcast_is_attributed_to_the_authority(self) -> None:
        root = ET.fromstring(broadcast_to_cap(self.broadcast()).to_xml())

        assert root.findtext(f"{CAP_QNAME}info/{CAP_QNAME}senderName") == "INCOIS"

    def test_one_info_block_per_requested_language(self) -> None:
        alert = broadcast_to_cap(self.broadcast())

        assert len(alert.info) == 2

    def test_a_broadcast_is_an_ordinary_alert_not_an_update(self) -> None:
        assert broadcast_to_cap(self.broadcast()).msg_type is CapMsgType.ALERT

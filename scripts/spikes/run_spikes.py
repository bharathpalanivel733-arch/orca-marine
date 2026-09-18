"""Day-1 risk validation spikes (PLAN.md Phase 0.4, DEPLOYMENT.md §8).

Five assumptions decide how Phase 1 and Phase 2 get built. This harness answers each
one against the live internet, time-boxed, and writes a machine-readable record so the
answer is reproducible rather than remembered:

    (a) MOSDAC registration / latency        -> if blocked, rely on CMEMS + INCOIS
    (b) INCOIS wave / Ocean-State-Forecast   -> if absent, CMEMS VHM0 is the wave source
    (c) NIOT OMNI buoy history               -> if absent, reliability ships as a design
                                                with a synthetic hindcast
    (d) Bhashini quotas                      -> if blocked, self-host + pre-generate audio
    (e) India-Sri Lanka IMBL geometry        -> hard blocker for Phase 2; the agreed
                                                1974/76 boundary only, never a median

Design rules, all of which exist to stop this tool from lying:

* **Nothing is fabricated.** A spike that needs credentials the environment does not
  have returns ``BLOCKED`` and says which variable is missing. It never guesses what the
  answer would have been, and it never sends a credential it was not given.
* **A failed probe is not a finding.** Network failure returns ``ERROR`` (unknown, retry),
  which is a different thing from ``NO_GO`` (probed successfully, capability absent).
* **Time-boxed.** Each spike has a deadline; a hanging endpoint fails the spike rather
  than the run.
* **Reproducible.** Every HTTP probe is recorded with URL, status and elapsed time into
  a timestamped JSON artifact plus ``latest.json``.

Usage:
    python scripts/spikes/run_spikes.py                # all five
    python scripts/spikes/run_spikes.py --only b,e     # selected
    python scripts/spikes/run_spikes.py --timeout 45   # per-spike deadline

Exit code is 0 when every spike reached a verdict (GO / NO_GO / BLOCKED are all
verdicts), and 1 when any spike ended in ERROR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - dependency guidance, not logic
    sys.exit("httpx is required: pip install -r scripts/spikes/requirements.txt")

try:
    import truststore
except ImportError:  # pragma: no cover - optional, see build_ssl_context
    truststore = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "spikes"
DEFAULT_TIMEOUT_SECONDS = 30.0
USER_AGENT = "ORCA-SIH26176-day1-spike/0.1 (+https://github.com/bharathpalanivel733-arch/orca-marine)"


def build_ssl_context() -> tuple[ssl.SSLContext, str]:
    """Build a TLS context that can verify Indian government endpoints.

    Several of these hosts serve an **incomplete certificate chain** (the intermediate
    CA is missing). Windows ``curl`` succeeds because Schannel fetches the missing
    intermediate over AIA; Python/OpenSSL does not, and fails with
    "unable to get local issuer certificate" even with an up-to-date certifi bundle.

    ``truststore`` delegates chain building to the operating system, which does chase
    AIA, so verification stays fully enabled. Certificate verification is never
    disabled here: a spike that cannot verify a host reports ERROR, because silently
    trusting an unverified government endpoint would make every result it produced
    worthless as evidence.
    """
    if truststore is not None:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "os-truststore"
    return ssl.create_default_context(), "python-default"


class Outcome(StrEnum):
    """Verdict of one spike."""

    GO = "GO"
    NO_GO = "NO_GO"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class Probe:
    """One HTTP request made during a spike, recorded for reproducibility."""

    method: str
    url: str
    status: int | None = None
    elapsed_ms: float | None = None
    note: str | None = None
    error: str | None = None


@dataclass
class SpikeResult:
    """The verdict for one risk, plus the evidence behind it."""

    id: str
    title: str
    plan_ref: str
    outcome: Outcome
    detail: str
    action: str
    probes: list[Probe] = field(default_factory=list)
    missing_credentials: list[str] = field(default_factory=list)
    duration_s: float = 0.0


async def _probe(
    client: httpx.AsyncClient, url: str, *, method: str = "GET", note: str | None = None
) -> tuple[Probe, httpx.Response | None]:
    """Make one recorded request. Never raises for network problems."""
    started = time.perf_counter()
    try:
        response = await client.request(method, url)
    except httpx.HTTPError as exc:
        return (
            Probe(
                method=method,
                url=url,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                note=note,
                error=f"{type(exc).__name__}: {exc}",
            ),
            None,
        )
    return (
        Probe(
            method=method,
            url=url,
            status=response.status_code,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            note=note,
        ),
        response,
    )


def _missing_env(*names: str) -> list[str]:
    """Credential variables that are absent or blank."""
    return [n for n in names if not os.environ.get(n, "").strip()]


def _network_failed(probes: list[Probe]) -> bool:
    """True when every probe failed at the transport level."""
    return bool(probes) and all(p.error is not None for p in probes)


# --------------------------------------------------------------------------------------
# (a) MOSDAC — registration and latency
# --------------------------------------------------------------------------------------
async def spike_mosdac(client: httpx.AsyncClient) -> SpikeResult:
    probes: list[Probe] = []
    for url, note in (
        ("https://mosdac.gov.in/", "portal reachable"),
        ("https://mosdac.gov.in/catalog/satellite.php", "dataset catalog (datasetId lookup)"),
        ("https://mosdac.gov.in/software/mdapi.zip", "mdapi python client published"),
    ):
        probe, _ = await _probe(client, url, method="HEAD" if url.endswith(".zip") else "GET", note=note)
        probes.append(probe)

    missing = _missing_env("MOSDAC_USERNAME", "MOSDAC_PASSWORD")
    reachable = [p for p in probes if p.status is not None and p.status < 400]

    if _network_failed(probes):
        return SpikeResult(
            id="a",
            title="MOSDAC registration / latency",
            plan_ref="PLAN.md 0.4(a) / DEPLOYMENT.md §8.1",
            outcome=Outcome.ERROR,
            detail="No MOSDAC endpoint was reachable from this environment.",
            action="Re-run from a network that permits mosdac.gov.in before judging this risk.",
            probes=probes,
            missing_credentials=missing,
        )

    if missing:
        return SpikeResult(
            id="a",
            title="MOSDAC registration / latency",
            plan_ref="PLAN.md 0.4(a) / DEPLOYMENT.md §8.1",
            outcome=Outcome.BLOCKED,
            detail=(
                f"MOSDAC needs SSO registration; {', '.join(missing)} not set, so download "
                f"latency cannot be measured. {len(reachable)}/{len(probes)} public endpoints "
                "responded, so the service itself is up."
            ),
            action=(
                "Treat MOSDAC as a Phase 1.8 enhancement behind CMEMS + INCOIS. Register at "
                "mosdac.gov.in, set MOSDAC_USERNAME/PASSWORD, re-run. Claim no Oceansat-3 SST "
                "either way (SSTM non-operational)."
            ),
            probes=probes,
            missing_credentials=missing,
        )

    return SpikeResult(
        id="a",
        title="MOSDAC registration / latency",
        plan_ref="PLAN.md 0.4(a) / DEPLOYMENT.md §8.1",
        outcome=Outcome.BLOCKED,
        detail=(
            "Credentials are present, but this harness deliberately does not perform the "
            "mdapi login/download; measuring real L1 latency is a manual Phase 1.8 step."
        ),
        action="Run the mdapi client manually with config.json and record observed latency.",
        probes=probes,
    )


# --------------------------------------------------------------------------------------
# (b) INCOIS wave / Ocean-State-Forecast griddap id
# --------------------------------------------------------------------------------------
async def spike_incois_waves(client: httpx.AsyncClient) -> SpikeResult:
    """Is the INCOIS wave / Ocean-State-Forecast product exposed as a griddap dataset?

    ERDDAP answers a zero-match search with **HTTP 404** ("your query produced no
    matching results"), which is indistinguishable from a malformed URL by status code
    alone. So this spike first runs a *control* search for a term that must match
    (DEPLOYMENT.md §2 lists confirmed SST dataset ids). If the control fails, the
    verdict is ERROR — a tooling or endpoint problem — rather than a false NO_GO.
    """
    probes: list[Probe] = []
    base = "https://erddap.incois.gov.in/erddap"
    search = f"{base}/search/index.json?page=1&itemsPerPage=1000&searchFor="

    probe, response = await _probe(client, f"{base}/index.html", note="ERDDAP server reachable")
    probes.append(probe)

    def parse_rows(resp: httpx.Response | None) -> list[list[Any]] | None:
        if resp is None or resp.status_code != 200:
            return None
        try:
            return list(resp.json()["table"]["rows"])
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None

    # Control: proves the search endpoint and URL shape work.
    probe, response = await _probe(client, f"{search}sst", note="CONTROL search: sst (must match)")
    probes.append(probe)
    control_rows = parse_rows(response)
    if control_rows is None:
        probes[-1].note = "CONTROL search: sst -> FAILED (endpoint or URL shape is wrong)"
        return SpikeResult(
            id="b",
            title="INCOIS wave / OSF griddap dataset id",
            plan_ref="PLAN.md 0.4(b) / DEPLOYMENT.md §8.2",
            outcome=Outcome.ERROR,
            detail=(
                "The control search for 'sst' did not return results, so this harness cannot "
                "distinguish 'no wave dataset' from 'query is wrong'. No verdict is claimed."
            ),
            action="Fix the search URL or check ERDDAP availability, then re-run.",
            probes=probes,
        )
    probes[-1].note = f"CONTROL search: sst -> {len(control_rows)} dataset(s), endpoint works"

    matches: dict[str, str] = {}
    searched: list[str] = []
    for term in ("wave", "swell", "osf", "indofos"):
        probe, response = await _probe(client, f"{search}{term}", note=f"search: {term}")
        probes.append(probe)
        searched.append(term)
        rows = parse_rows(response)
        if rows is None:
            if probe.status == 404:
                probes[-1].note = f"search: {term} -> 404 = no matching datasets"
            continue
        try:
            columns = response.json()["table"]["columnNames"]  # type: ignore[union-attr]
            id_idx = columns.index("Dataset ID")
            title_idx = columns.index("Title")
            for row in rows:
                matches[str(row[id_idx])] = str(row[title_idx])
            probes[-1].note = f"search: {term} -> {len(rows)} dataset(s)"
        except (KeyError, ValueError, IndexError):
            probes[-1].note = f"search: {term} (unparseable response)"

    if _network_failed(probes):
        return SpikeResult(
            id="b",
            title="INCOIS wave / OSF griddap dataset id",
            plan_ref="PLAN.md 0.4(b) / DEPLOYMENT.md §8.2",
            outcome=Outcome.ERROR,
            detail="INCOIS ERDDAP was not reachable from this environment.",
            action="Re-run with access to erddap.incois.gov.in before deciding the wave source.",
            probes=probes,
        )

    if matches:
        listed = "; ".join(f"{k} ({v})" for k, v in sorted(matches.items())[:8])
        return SpikeResult(
            id="b",
            title="INCOIS wave / OSF griddap dataset id",
            plan_ref="PLAN.md 0.4(b) / DEPLOYMENT.md §8.2",
            outcome=Outcome.GO,
            detail=f"Matched {len(matches)} dataset(s) for {', '.join(searched)}: {listed}",
            action=(
                "Confirm dimension names/order via each dataset's .das before hardcoding, then "
                "wire in Phase 1.2. Keep CMEMS VHM0 as the declared fallback regardless."
            ),
            probes=probes,
        )

    return SpikeResult(
        id="b",
        title="INCOIS wave / OSF griddap dataset id",
        plan_ref="PLAN.md 0.4(b) / DEPLOYMENT.md §8.2",
        outcome=Outcome.NO_GO,
        detail=(
            f"ERDDAP is up and the control search returned {len(control_rows)} SST dataset(s), "
            f"but {', '.join(searched)} all returned 'no matching results'. The wave / Ocean-"
            "State-Forecast product is genuinely not published as a searchable ERDDAP dataset. "
            "This confirms the caveat in DEPLOYMENT.md §2."
        ),
        action=(
            "Use CMEMS cmems_mod_glo_wav_anfc_0.083deg_PT3H-i (VHM0) as the PRIMARY wave source "
            "in Phase 1.4, not a fallback. INCOIS OSF waves remain available only via bulletins "
            "/ WMS scraping (Phase 1.7), which is a separate, lower-priority path."
        ),
        probes=probes,
    )


# --------------------------------------------------------------------------------------
# (c) NIOT OMNI buoy history for the reliability hindcast
# --------------------------------------------------------------------------------------
async def spike_omni_buoys(client: httpx.AsyncClient) -> SpikeResult:
    probes: list[Probe] = []
    for url, note in (
        ("https://incois.gov.in/portal/datainfo/buoys.jsp", "INCOIS buoy data page"),
        ("https://www.niot.res.in/", "NIOT site reachable"),
        (
            "https://erddap.incois.gov.in/erddap/search/index.json?page=1&itemsPerPage=1000&searchFor=buoy",
            "ERDDAP catalog search: buoy",
        ),
    ):
        probe, response = await _probe(client, url, note=note)
        probes.append(probe)
        if "searchFor=buoy" in url and response is not None and response.status_code == 200:
            try:
                rows = response.json()["table"]["rows"]
                probes[-1].note = f"{note} -> {len(rows)} dataset(s)"
            except (KeyError, ValueError, json.JSONDecodeError):
                probes[-1].note = f"{note} (unparseable response)"

    if _network_failed(probes):
        return SpikeResult(
            id="c",
            title="NIOT OMNI buoy historical access",
            plan_ref="PLAN.md 0.4(c) / DEPLOYMENT.md §8.3",
            outcome=Outcome.ERROR,
            detail="Neither INCOIS nor NIOT was reachable from this environment.",
            action="Re-run with network access before deciding the reliability-layer data path.",
            probes=probes,
        )

    return SpikeResult(
        id="c",
        title="NIOT OMNI buoy historical access",
        plan_ref="PLAN.md 0.4(c) / DEPLOYMENT.md §8.3",
        outcome=Outcome.BLOCKED,
        detail=(
            "Reachability was probed, but paired forecast+observation history is served through "
            "the INCOIS-managed OMNI-RAMA portal, which needs an account. This harness cannot "
            "prove bulk historical access without one, and will not assume it."
        ),
        action=(
            "Request portal access now (long lead time). PLAN.md 10.1 trigger stands: if the "
            "hindcast is not assembled by Day 4, ship the Reliability Horizon as a rigorous "
            "design with a synthetic hindcast rather than dropping it."
        ),
        probes=probes,
    )


# --------------------------------------------------------------------------------------
# (d) Bhashini quotas
# --------------------------------------------------------------------------------------
async def spike_bhashini(client: httpx.AsyncClient) -> SpikeResult:
    probes: list[Probe] = []
    for url, note in (
        ("https://bhashini.gov.in/", "Bhashini portal reachable"),
        ("https://meity-auth.ulcacontrib.org/", "ULCA auth host reachable"),
    ):
        probe, _ = await _probe(client, url, note=note)
        probes.append(probe)

    missing = _missing_env("BHASHINI_USER_ID", "BHASHINI_ULCA_API_KEY")

    if _network_failed(probes):
        return SpikeResult(
            id="d",
            title="Bhashini ASR/TTS quota",
            plan_ref="PLAN.md 0.4(d) / DEPLOYMENT.md §8.4",
            outcome=Outcome.ERROR,
            detail="No Bhashini/ULCA host was reachable from this environment.",
            action="Re-run with network access before judging the speech path.",
            probes=probes,
            missing_credentials=missing,
        )

    if missing:
        return SpikeResult(
            id="d",
            title="Bhashini ASR/TTS quota",
            plan_ref="PLAN.md 0.4(d) / DEPLOYMENT.md §8.4",
            outcome=Outcome.BLOCKED,
            detail=(
                f"{', '.join(missing)} not set, so no ASR/TTS call was attempted and quota is "
                "unmeasured. Hosts responded, so the service is up."
            ),
            action=(
                "Register for ULCA credentials. Build Phase 7 against the documented fallback "
                "chain regardless (self-hosted IndicWhisper / Indic-Parler-TTS + pre-generated "
                "audio cache), which is what makes the demo quota-proof."
            ),
            probes=probes,
            missing_credentials=missing,
        )

    return SpikeResult(
        id="d",
        title="Bhashini ASR/TTS quota",
        plan_ref="PLAN.md 0.4(d) / DEPLOYMENT.md §8.4",
        outcome=Outcome.BLOCKED,
        detail=(
            "Credentials are present, but a real quota test means submitting audio, which "
            "belongs to Phase 7.2/7.6 rather than a Day-1 reachability spike."
        ),
        action="Measure quota during Phase 7 with a scripted ASR+TTS round trip.",
        probes=probes,
    )


# --------------------------------------------------------------------------------------
# (e) India-Sri Lanka IMBL geometry
# --------------------------------------------------------------------------------------
async def spike_imbl_geometry(client: httpx.AsyncClient) -> SpikeResult:
    probes: list[Probe] = []
    wfs = "https://geo.vliz.be/geoserver/MarineRegions/wfs"

    probe, response = await _probe(
        client,
        f"{wfs}?service=WFS&version=2.0.0&request=GetCapabilities",
        note="marineregions WFS capabilities",
    )
    probes.append(probe)

    layers: list[str] = []
    treaty_layers: list[str] = []
    if response is not None and response.status_code == 200:
        text = response.text
        # Layer names appear as <Name>MarineRegions:xxx</Name> in the capabilities document.
        for chunk in text.split("<Name>")[1:]:
            name = chunk.split("</Name>")[0].strip()
            if name and ":" in name:
                layers.append(name)
        treaty_layers = [
            layer
            for layer in layers
            if any(token in layer.lower() for token in ("treaty", "boundar", "iho", "eez"))
        ]
        probes[-1].note = f"marineregions WFS capabilities -> {len(layers)} layers"

    if _network_failed(probes):
        return SpikeResult(
            id="e",
            title="Agreed 1974/76 India-Sri Lanka IMBL geometry",
            plan_ref="PLAN.md 0.4(e) / METHODS.md §1 / DEPLOYMENT.md §8.5",
            outcome=Outcome.ERROR,
            detail="marineregions WFS was not reachable from this environment.",
            action="Re-run with network access; Phase 2 stays blocked until the geometry is sourced.",
            probes=probes,
        )

    found = ", ".join(sorted(treaty_layers)[:10]) or "none"
    return SpikeResult(
        id="e",
        title="Agreed 1974/76 India-Sri Lanka IMBL geometry",
        plan_ref="PLAN.md 0.4(e) / METHODS.md §1 / DEPLOYMENT.md §8.5",
        outcome=Outcome.NO_GO,
        detail=(
            f"Boundary-ish WFS layers discovered: {found}. None of these is the agreed 1974/76 "
            "India-Sri Lanka bilateral boundary: marineregions publishes EEZ and derived/median "
            "geometries, and METHODS.md §3 is explicit that a computed median line will "
            "mis-warn fishermen. Automated discovery cannot settle this."
        ),
        action=(
            "Source the treaty geometry from the 1974/76 agreement text (UN DOALOS / MEA "
            "treaty records) and load it into PostGIS as authoritative reference data with a "
            "regression test pinning known Palk Strait coordinates. Phase 2 stays blocked."
        ),
        probes=probes,
    )


SPIKES: dict[str, Callable[[httpx.AsyncClient], Awaitable[SpikeResult]]] = {
    "a": spike_mosdac,
    "b": spike_incois_waves,
    "c": spike_omni_buoys,
    "d": spike_bhashini,
    "e": spike_imbl_geometry,
}


async def run_spike(
    key: str, runner: Callable[[httpx.AsyncClient], Awaitable[SpikeResult]], timeout: float
) -> SpikeResult:
    """Run one spike under its own deadline."""
    started = time.perf_counter()
    limits = httpx.Timeout(timeout / 3, connect=min(10.0, timeout / 3))
    ssl_context, _ = build_ssl_context()
    try:
        async with httpx.AsyncClient(
            timeout=limits,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=ssl_context,
        ) as client:
            result = await asyncio.wait_for(runner(client), timeout=timeout)
    except TimeoutError:
        result = SpikeResult(
            id=key,
            title=f"spike {key}",
            plan_ref="PLAN.md 0.4",
            outcome=Outcome.ERROR,
            detail=f"Spike exceeded its {timeout:.0f}s deadline.",
            action="Re-run with --timeout raised, or from a faster network.",
        )
    except Exception as exc:  # noqa: BLE001 - a spike must never crash the run
        result = SpikeResult(
            id=key,
            title=f"spike {key}",
            plan_ref="PLAN.md 0.4",
            outcome=Outcome.ERROR,
            detail=f"Unhandled error: {type(exc).__name__}: {exc}",
            action="Investigate the harness or the endpoint.",
        )
    result.duration_s = round(time.perf_counter() - started, 2)
    return result


def render(results: list[SpikeResult]) -> str:
    """Human-readable summary."""
    lines = ["", "=" * 78, "ORCA Day-1 risk spikes (PLAN.md Phase 0.4)", "=" * 78]
    for r in results:
        lines.append(f"\n[{r.outcome}] ({r.id}) {r.title}   [{r.duration_s}s]  {r.plan_ref}")
        lines.append(f"  finding: {r.detail}")
        lines.append(f"  action : {r.action}")
        if r.missing_credentials:
            lines.append(f"  missing: {', '.join(r.missing_credentials)}")
        for p in r.probes:
            status = p.error or f"HTTP {p.status}"
            lines.append(f"    - {p.method} {p.url} -> {status} ({p.elapsed_ms} ms)")
    tally = {o: sum(1 for r in results if r.outcome is o) for o in Outcome}
    lines.append("\n" + "-" * 78)
    lines.append("  ".join(f"{o}: {n}" for o, n in tally.items() if n))
    lines.append("-" * 78)
    return "\n".join(lines)


async def main_async(selected: list[str], timeout: float, out_dir: Path) -> int:
    results = [await run_spike(key, SPIKES[key], timeout) for key in selected]

    started_at = datetime.now(UTC).isoformat()
    _, trust_mode = build_ssl_context()
    payload: dict[str, Any] = {
        "generated_at": started_at,
        "plan_ref": "PLAN.md Phase 0.4",
        "timeout_seconds": timeout,
        "tls_trust": trust_mode,
        "tls_verification": "enabled",
        "results": [asdict(r) for r in results],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started_at.replace(":", "").replace("-", "").split(".")[0]
    (out_dir / f"spikes-{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(render(results))
    print(f"\nartifacts: {out_dir}")
    return 1 if any(r.outcome is Outcome.ERROR for r in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--only", default="", help="comma-separated spike ids to run (default: all of a,b,c,d,e)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-spike deadline in seconds (default {DEFAULT_TIMEOUT_SECONDS:.0f})",
    )
    parser.add_argument("--out", type=Path, default=ARTIFACT_DIR, help="artifact directory")
    args = parser.parse_args()

    selected = [k.strip() for k in args.only.split(",") if k.strip()] or list(SPIKES)
    unknown = [k for k in selected if k not in SPIKES]
    if unknown:
        parser.error(f"unknown spike id(s): {', '.join(unknown)}; choose from {', '.join(SPIKES)}")

    return asyncio.run(main_async(selected, args.timeout, args.out))


if __name__ == "__main__":
    raise SystemExit(main())

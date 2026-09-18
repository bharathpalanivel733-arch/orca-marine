# Day-1 risk spikes

`PLAN.md` Phase 0.4 / `DEPLOYMENT.md` §8. Five assumptions decide how Phases 1 and 2 get
built. This harness answers each one against live endpoints and writes a reproducible
record instead of relying on memory.

## Run

```bash
pip install -r scripts/spikes/requirements.txt     # httpx + truststore
python scripts/spikes/run_spikes.py                # all five
python scripts/spikes/run_spikes.py --only b,e     # selected
python scripts/spikes/run_spikes.py --timeout 45   # per-spike deadline
```

Or `pnpm spikes` / `make spikes`.

## Verdicts

| Verdict | Meaning |
|---|---|
| `GO` | Probed successfully; the capability is there. |
| `NO_GO` | Probed successfully; the capability is **absent** — take the documented fallback. |
| `BLOCKED` | Cannot be determined without a credential or a manual step. **Never guessed.** |
| `ERROR` | The probe itself failed (network/TLS/timeout). Unknown, not a finding — re-run. |

Exit code is 0 when every spike reached a verdict, 1 if any ended in `ERROR`.

## Rules this harness follows

- **No fabrication.** A spike needing absent credentials returns `BLOCKED` and names the
  missing variable. It never sends a credential it was not given, and never invents what
  the answer might have been.
- **A broken probe is not a finding.** `ERROR` (unknown) is kept strictly separate from
  `NO_GO` (probed, absent).
- **Negative results are self-validating where they can be.** ERDDAP answers a zero-match
  search with HTTP 404, which looks identical to a malformed URL. Spike (b) therefore runs
  a control search first (`sst`, which must match); if the control fails, the verdict is
  `ERROR`, not a false `NO_GO`.
- **TLS verification is never disabled.** Several Indian government hosts serve an
  incomplete certificate chain: Windows `curl` succeeds because Schannel fetches the
  missing intermediate over AIA, while Python/OpenSSL fails with
  `unable to get local issuer certificate` even with a current certifi bundle.
  `truststore` delegates chain building to the OS, so verification stays on. Without it,
  every spike would fail with a TLS error that looks like the endpoint being down.

## Artifacts

`artifacts/spikes/spikes-<UTC timestamp>.json` plus `latest.json`, recording every probe
with URL, HTTP status and elapsed time. Gitignored — the durable record of each run's
verdicts lives in `docs/PROGRESS.md`.

## Credentials

The spikes read `MOSDAC_USERNAME` / `MOSDAC_PASSWORD` and `BHASHINI_USER_ID` /
`BHASHINI_ULCA_API_KEY` from the environment **only to decide whether a measurement is
possible**. See `.env.example`. With credentials present, these spikes still report
`BLOCKED`, because measuring MOSDAC latency and Bhashini quota means a real download and
a real ASR/TTS round trip — Phase 1.8 and Phase 7 work, not a Day-1 reachability check.

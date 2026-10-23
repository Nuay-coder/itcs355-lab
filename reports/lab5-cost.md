# Lab 5 — Cost report

Provider `gcp` · instance `1-512Mi` · 4.23 THB/hour

## 1. Estimate, made before running
0.75 THB

## 2. Actual, from billing filtered by tag
0.00 THB

## 3. The gap
-0.75 THB (-100.0%)

Checked 2026-10-23 in the GCP Billing Console (Cost breakdown, 2026-10-01 to
2026-10-23, project `itcs355-6688033`, label `lab`): no cost reported under `lab=5`
yet — only `lab=2/3/4` appear. Billing has a reporting lag (Lab 4's cost report noted
the same), and every Lab 5 GCP resource lived minutes, not hours. `0.00` is what the
Console shows today, not an estimate.

## 4. Breakdown by component
| Component | THB | Notes |
|---|---|---|
| Training | 0.00 | Task 1 only compiles the pipeline locally; nothing ran on Vertex |
| Storage | 0.00 | No new uploads on the GCP side |
| Serving | ~0 (not yet in Billing) | One throwaway Cloud Run service (Task 2), ~10 min total |
| Pipeline | 0.00 | Compile-only |
| Monitoring | 0.00 | `emit_metric` not exercised |
| Scheduler/Jobs | ~0 (not yet in Billing) | Task 3's Cloud Run Job ran 3×, each under a minute; Scheduler itself free |

## 5. Cost per 1,000 predictions
Measured throughput: 96.5 req/s

| Utilisation | THB per 1,000 |
|---|---|
| 5% | 0.2435 |
| 25% | 0.0487 |
| 80% | 0.0152 |

5% is the one I'd defend — same stance as Lab 3, this project has never served real
traffic. Batch breakeven (0.0012 req/s) unchanged from Lab 3, same instance/rate.

## 6. One optimisation applied

Scale-to-zero Cloud Run Job (Task 3) for the retraining trigger, instead of a
standing VM.

| | Before (hypothetical `e2-standard-4`, always on) | After (measured: Cloud Run Job) |
|---|---|---|
| THB/week | 6.4 × 24 × 7 = **1,075.20** | ~15s active × 4.23/hr ÷ 3600 ≈ **0.018** |
| Latency cost | None — always warm | Cold start adds seconds; irrelevant for a weekly trigger |

~60,000× cheaper, no real trade-off — a retrain trigger has no latency SLO to miss.

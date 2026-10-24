# Lab 5 — Cost report

Provider `gcp` · instance `1-512Mi` · 4.23 THB/hour

## 1. Estimate, made before running
0.75 THB

## 2. Actual, from billing filtered by tag
0.27 THB gross usage cost (THB 0.00 net — fully covered by free-trial credits)

## 3. The gap
-0.48 THB (-64.0%)

Smaller than estimated, not bigger: the estimate's two largest line items — the
train/ingest/validate/register pipeline steps (0.46 THB) and the LLM golden-set
calls (0.19 THB) — together account for 0.65 THB of the 0.75 THB estimate, and
neither shows up here. Training/pipeline is explained: Task 1 only compiled the
pipeline locally and never submitted it to Vertex, so that cost genuinely never
happened. The missing LLM cost is less certain — generative API usage may not
carry the same resource label this report filters on, or may be billed through a
path this view doesn't capture: stated as what's uncertain rather than guessed at.

## 4. Breakdown by component
| Component | THB | Notes |
|---|---|---|
| Training | 0.00 | Task 1 only compiles the pipeline locally; nothing ran on Vertex |
| Storage | 0.00 | No new uploads on the GCP side |
| Serving | ~0.14 | Cloud Run service time (Task 2's throwaway test deploys) |
| Pipeline | 0.00 | Compile-only |
| Monitoring | 0.00 | `emit_metric` not exercised |
| Scheduler/Jobs | 0.13 | Cloud Run Jobs CPU (Task 3's retrain trigger, 3 executions) |

All of it is Cloud Run — confirmed directly in Billing's own breakdown (`Group by
Service`), which attributed the full 0.27 THB to Cloud Run, with the `Jobs CPU` SKU
alone responsible for 0.13 THB of it.

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

# Lab 5 — Cost report

Provider `gcp` · instance `1-512Mi` · 4.23 THB/hour

## 1. Estimate, made before running
0.75 THB

## 2. Actual, from billing filtered by tag
0.00 THB

## 3. The gap
-0.75 THB (-100.0%)

**Checked 2026-10-23 in the GCP Billing Console** (Cost breakdown, charge period
2026-10-01 to 2026-10-23, scoped to project `itcs355-6688033`, filtered by label
`lab`): no cost at all is reported under `lab=5` — only `lab=2`, `3`, `4` appear as
available label values in that window. The gap is not "spend I didn't account for" in
the usual sense; it's that **GCP's billing data has a reporting lag** (Lab 4's own cost
report noted the same thing: "the current invoice period has not closed yet"), and
every Lab 5 resource this session created lived for minutes, not hours (a Cloud Run
test service up for one deploy-test-teardown cycle, a Cloud Run Job executed three
times total, a Scheduler job that existed only to fire it) — exactly the kind of
usage most likely to round to nothing or sit unprocessed at check time. This report
will be re-run once `lab=5` actually appears in Billing so the real figure replaces
this placeholder; `0.00` here is what the Console actually shows today, not an
estimate standing in for it.

## 4. Breakdown by component
| Component | THB | Notes |
|---|---|---|
| Training | 0.00 | No training job was submitted this lab — Task 1 only compiles the pipeline locally (`make pipeline`); nothing ran on Vertex. |
| Storage | 0.00 | No new uploads; the AWS swap attempt (Task 4B) never got past `CreateBucket`, and the GCP side touched only the existing registered model artifact (read, not written). |
| Serving | ~0 (not yet in Billing) | One throwaway Cloud Run service (Task 2), up for roughly 10 minutes across two deploy cycles, then deleted. |
| Pipeline | 0.00 | Compile-only; nothing submitted to Vertex AI Pipelines. |
| Monitoring | 0.00 | No new Cloud Monitoring resources created this lab; `emit_metric` not exercised. |
| Scheduler/Jobs | ~0 (not yet in Billing) | Task 3's Cloud Run Job ran 3 times (two real, one direct test), each well under a minute; the Scheduler job itself is free (within the standing free-tier job allowance confirmed in Lab 4). |

All non-training/storage/pipeline/monitoring activity this lab was Cloud Run
service/job time measured in minutes total, consistent with the near-zero actual
figure above.

## 5. Cost per 1,000 predictions
Measured throughput: 96.5 req/s

| Utilisation | THB per 1,000 |
|---|---|
| 5% | 0.2435 |
| 25% | 0.0487 |
| 80% | 0.0152 |

Utilisation is the most fragile number here, which is why three are reported rather than
one. Same stance as Lab 3's cost report: this project has never served real traffic, so
the **5% figure is the one I'd actually defend** — it matches a service that only ever
answers short test bursts, which is still exactly its real usage pattern as of Lab 5.

Below roughly **0.0012 req/s**, scheduled batch inference is cheaper than keeping
this endpoint warm — unchanged from Lab 3, since the serving instance type and rate
are the same. This project's real request rate (effectively zero, no production
traffic) is already below that line, which is why nothing in this project runs as a
standing warm endpoint outside of deliberate, torn-down test windows.

## 6. One optimisation you applied

**Scale-to-zero scheduled compute for retraining (Task 3), instead of a standing
instance.** The naive way to run a weekly retrain trigger is a small VM that sits
powered on so its cron job can fire; what Task 3 actually built is a Cloud Run Job
that exists only for the ~15–20 seconds it takes to run, triggered by Cloud Scheduler
(itself free within GCP's job allowance), then scales back to zero.

| | Before (hypothetical always-on `e2-standard-4`) | After (measured: Cloud Run Job, `1-512Mi`) |
|---|---|---|
| Configuration | On-demand VM, powered on continuously just to host a weekly cron | Cloud Run Job, scale-to-zero, triggered by Cloud Scheduler |
| THB per week | 6.4 THB/hr × 24 × 7 = **1,075.20 THB** | ~15s active × 4.23 THB/hr ÷ 3600 ≈ **0.018 THB** (run 3 times while testing; 1×/week in steady state) |
| Latency / freshness cost | None — always warm, trigger fires instantly | Cold start adds low-single-digit seconds before the job body runs; irrelevant for a weekly retrain, since nothing is waiting on the response the way a live `/predict` caller would be |

**~60,000× cheaper**, with no real cost: a retrain trigger has no latency SLO to miss
the way a prediction endpoint does — unlike Lab 3's serving cost, where right-sizing
had a genuine p95 trade-off to weigh, this optimisation is close to a pure win because
the workload itself tolerates a cold start.

# Lab 3 — Cost per 1,000 Predictions

## Method

`src/costs.cost_per_1k_predictions(hourly_thb, throughput_rps, utilisation)` — existing
function, no new script needed.

| Input | Value | Source |
|---|---|---|
| Instance hourly rate | 4.23 THB/hr | `reports/lab3-load.md`, 1 vCPU/512MiB Cloud Run active rate |
| Throughput | 96.5 req/s | `reports/lab3-load.md`, measured at VUS=10 — the concurrency level that met the stated p95≤200ms target |
| Utilisation assumption | **0.05 (5%)** | Not specified by the handout — our own call. This endpoint has no real production traffic (lab exercise only); 5% reflects intermittent test usage, not a continuously-loaded service. |

```
cost_per_1k_predictions(4.23, 96.5, 0.05) = 0.2435 THB per 1,000 predictions
```

## Result

**0.2435 THB per 1,000 predictions**, at the stated utilisation assumption of 5%.

## Batch breakeven (2 lines)

`src/costs.batch_breakeven_rps(endpoint_hourly_thb, batch_job_thb, batch_runs_per_day=1)`,
using the Lab 2 training job's real cost (0.1372 THB/run) as a stand-in for a batch
job's cost — the handout doesn't specify this either, and no real batch inference job
was run to measure directly:

Below **~0.0012 req/s (~101 requests/day)**, scheduled batch inference is cheaper than
keeping this endpoint warm continuously at 4.23 THB/hr — an order of magnitude lower
than what most people expect before checking.

## Teardown

`make teardown LAB=3` run and confirmed:

- Deleted: `cloud-run:itcs355-predict` (the only resource tagged `lab=3`)
- `gcloud run services list --region=asia-southeast1` → 0 items
- IAM: no `roles/iam.serviceAccountTokenCreator` or `roles/run.invoker` bindings remain,
  on the training service account or at project level
- Registered model versions confirmed intact (teardown is scoped to Cloud Run only):
  `itcs355-lab2-sensor-risk` (`437231793901404160@1`, alias `production`) and the canary
  model (`109031971056779264@1`, alias `staging`) both still present
- `make portability-audit`: passed
- `make test`: 37 passed
- `git status`: no `cloud.env` present

A real bug was found and fixed during this teardown: `GcpAdapter.teardown()`'s Cloud Run
list filter used `labels.<key>=<value>`, which silently matches nothing (`gcloud run
services list` needs `metadata.labels.<key>=<value>` instead) — the mismatch surfaced
because `stdout`/`stderr` were merged, so the resulting gcloud warning got parsed as if
it were a service name and handed to `delete`. Fixed in `cloudlayer/gcp.py`.

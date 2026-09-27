# Lab 3 — Cost per 1,000 Predictions

## Method

Computed with `src/costs.cost_per_1k_predictions(hourly_thb, throughput_rps, utilisation)`:

```
cost per 1,000 = hourly rate × (1,000 ÷ (throughput × utilisation)) ÷ 3,600
```

| Input | Value | Source |
|---|---|---|
| Instance hourly rate | 4.23 THB/hr | 1 vCPU / 512 MiB Cloud Run, active rate (`reports/lab3-load.md`) |
| Throughput | 96.5 req/s | Measured at concurrency 10, the level that met my p95 ≤ 200 ms target |
| Utilisation | **5%** | My own assumption (see below) |

```
4.23 × (1,000 ÷ (96.5 × 0.05)) ÷ 3,600 = 0.2435 THB
```

**Result: 0.2435 THB per 1,000 predictions.**

## Why 5% utilisation

The handout doesn't set a value, so this is my choice. This endpoint has never served real
users. Throughout the lab it only handled short bursts of test traffic (smoke tests, load
tests, the canary run) and sat idle the rest of the time. 5% means the endpoint does useful
work for about 1.2 hours' worth of its full measured capacity each day, which matches that
pattern better than a production-style figure like 25% or 80%.

This is the most fragile input in the calculation: cost per 1,000 scales inversely with
utilisation, so at 100% it would be 20 times cheaper (about 0.012 THB).

## When is batch inference cheaper than keeping the endpoint warm?

Below about 100 requests per day (~0.0012 req/s), a scheduled batch job is cheaper than keeping this endpoint warm.
Staying warm costs about 101.5 THB a day regardless of traffic, while one daily batch run costs about 0.14 THB (estimated from the Lab 2 training job, since no batch scoring job was built in this lab).

## Teardown

`make teardown LAB=3` run and confirmed:

- `gcloud run services list --region=asia-southeast1` → 0 items
- IAM: no `roles/iam.serviceAccountTokenCreator` or `roles/run.invoker` bindings remain,
  on the training service account or at project level
- Registered model versions confirmed intact: `itcs355-lab2-sensor-risk`
  (`437231793901404160@1`, alias `production`) and the canary model
  (`109031971056779264@1`, alias `staging`)
- `make portability-audit`: passed
- `make test`: 37 passed
- `git status`: no `cloud.env` present

A bug was found and fixed during teardown: `GcpAdapter.teardown()`'s Cloud Run list filter
used `labels.<key>=<value>`, which silently matches nothing — `gcloud run services list`
needs `metadata.labels.<key>=<value>` instead. Fixed in `cloudlayer/gcp.py`.

# Lab 3 — Serving, Load Testing, and Rollback

---

## What this lab delivered

- FastAPI service: `/predict`, `/predict/batch`, `/health`, `/ready`, schema validation,
  structured logs, model version in every response
- `deploy()` and `invoke()` on Cloud Run (GCP adapter), plus smoke test
- Load test at 1 / 10 / 50 concurrency, cold start, breaking point, batch / payload / instance-size
- Canary at 90/10 with metric-only detection, rollback, timestamped evidence
- Cost per 1,000 predictions with stated utilisation assumption, and teardown evidence

---

## Reports

What each report in `reports/` covers:

- [`reports/lab3-load.md`](../../reports/lab3-load.md) — load test results and the latency target.
- [`reports/lab3-canary.md`](../../reports/lab3-canary.md) — canary detection design and outcome.
- [`reports/lab3-canary-evidence.md`](../../reports/lab3-canary-evidence.md) — timestamped rollback evidence.
- [`reports/lab3-cost.md`](../../reports/lab3-cost.md) — cost per 1,000 predictions and the batch-vs-warm breakeven.

---




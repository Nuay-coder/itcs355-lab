# Lab 3 — Canary Deployment and Rollback

## Canary configuration

| | Stable | Canary |
|---|---|---|
| Revision | `itcs355-predict-stable` | `itcs355-predict-canary-v2` |
| Hyperparameters | `max_depth=4, min_samples_leaf=5` | `max_depth=2, min_samples_leaf=30` |
| Test ROC AUC | 0.8533 | 0.8481 |
| Traffic | 90% → 100% after rollback | 10% → 0% after rollback |

Both revisions run on Cloud Run (`itcs355-predict`, 1 vCPU / 512 MiB). The split was
applied through `GcpAdapter.split_traffic()`.

## Detection

**Method:** cumulative average log loss over the combined traffic, compared against the stable model's baseline of 0.27070. Alert when `z > 1.0`. Requests were sent at 60 req/s and were never grouped by model
version before an alert.

| Time (UTC) | Requests since start | z |
|---|---|---|
| 16:53:28 | 10,854 | +0.22 |
| 16:58:09 | 27,730 | +0.69 |
| 17:02:22 | 42,884 | −0.27 |
| 17:07:47 | 62,411 | +0.24 |
| 17:13:00 | 81,000 | **+0.71** |

**Result:** no alert within the 22.5-minute budget set before the run.

## Rollback

Canary started at 16:50:27 UTC. Traffic moved back to 100% stable at **17:28:00 UTC**.
Timestamped evidence: `reports/lab3-canary-evidence.md`.

---

## Five lines answering

1. **Metric:** cumulative log loss on the combined traffic stream, compared with the stable model's baseline (0.27070).
2. **Detection time:** it didn't trigger. After 22.5 minutes (81,000 requests at 90/10), z reached only 0.71 against my 1.0 threshold, and I kept that as the result instead of lowering the threshold.
3. **What would make it faster:** a larger canary share. More requests per second only reaches the same sample size sooner but a larger share makes each request more informative.
4. **At 50/50:** with more canary traffic in the mix, the canary's worse predictions make up a bigger share of the aggregate metric, so the same gap between the two models stands out against the noise sooner. Detection would likely happen well before the 22.5-minute budget ran out.
5. **Trade-off:** 50/50 detects faster, but it sends half of real traffic to a worse model. That's why canaries usually start small.

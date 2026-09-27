# Lab 3 — Load Test Report

## Latency target (stated before measuring)

> **p95 ≤ 200 ms at concurrency 10, error rate < 1%**

Stated on 2026-09-26 and committed on its own (`ccae981`) before any load test was run.

**Result: met.** p95 = 148.43 ms at concurrency 10, 0% errors.

**Configuration that meets it:** Cloud Run, `asia-southeast1`, 1 vCPU / 512 MiB,
container concurrency 80, `min-instances=0`, `max-instances=2`.

**Breaking concurrency:** about 13 concurrent users.

Load-test scripts: `loadtest/k6.js`, `loadtest/k6-breaking-point.js`. Raw k6 output: `reports/raw/`.

---

## Three concurrency levels

| Concurrency | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error rate |
|---|---|---|---|---|---|
| 1  | 15.7 | 62.16  | 69.92      | 87.83    | 0% |
| 10 | 96.5 | 101.87 | **148.43** | 165.12   | 0% |
| 50 | 94.5 | 227.40 | 1,098.71   | 1,194.98 | 0% |

## Cold-start latency (scale-to-zero)

Measured separately: the first request after the service had been idle long enough to
scale to zero (about 16 minutes each time). Three samples.

| Sample | Latency |
|---|---|
| 1 | 18,488.74 ms |
| 2 | 10,434.28 ms |
| 3 | 10,370.27 ms |
| **Median** | **10,434.28 ms** |

## Breaking point

Ramping load, 20 seconds per stage:

| Concurrency | p95 (ms) | Error rate |
|---|---|---|
| 10  | 163.28   | 0% |
| 20  | 281.00   | 0% |
| 30  | 568.16   | 0% |
| 40  | 844.58   | 0% |
| 60  | 1,329.52 | 0% |
| 80  | 1,706.12 | 0% |
| 100 | 2,318.99 | 0% |

p95 crosses 200 ms at about **13 concurrent users**, interpolated between the 10 and 20
stages: `10 + (200 − 163.28) / (281.00 − 163.28) × 10 ≈ 13.1`. No errors appeared at any
level. k6 reported no dropped iterations, so the client was not the bottleneck.

---

## Batch size

Does one `/predict/batch` call with N rows beat N calls to `/predict`? (median of 5 runs)

| Rows | N × `/predict` (ms) | 1 × `/predict/batch` (ms) | Batch is faster by |
|---|---|---|---|
| 10  | 1,386.7  | 150.3 | 9.2× |
| 50  | 6,994.0  | 142.4 | 49.1× |
| 100 | 13,842.1 | 148.6 | 93.1× |

**Yes.** With 100 rows, the batch call is 93 times faster, and batch latency stays around
140–150 ms regardless of row count. Cost follows the same pattern: one request fee and
one billed interval instead of N.

## Payload size

Payload was increased by adding rows to `/predict/batch` (the single-row schema rejects
extra fields). Server-side timings:

| Rows | Request size | parse_ms | predict_ms | serialize_ms | Serialization share |
|---|---|---|---|---|---|
| 1   | 160 B    | 1.243 | 13.970 | 0.087 | 0.6% |
| 5   | 762 B    | 1.132 | 23.185 | 0.128 | 0.5% |
| 10  | 1,514 B  | 1.416 | 14.912 | 0.146 | 0.9% |
| 25  | 3,764 B  | 1.303 | 15.661 | 0.223 | 1.3% |
| 50  | 7,519 B  | 1.303 | 23.881 | 0.328 | 1.3% |
| 100 | 15,019 B | 1.722 | 14.635 | 0.576 | 3.4% |

**Serialization does not start to dominate within the allowed range.** At the 100-row
maximum it is still only 3.4% of server time. Payload size has almost no effect on cost
within this range.

## Instance size (one step up)

Same image, concurrency 10:

| Instance | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (req/s) | Cost while active |
|---|---|---|---|---|---|
| 1 vCPU / 512 MiB | 101.87 | 148.43 | 165.12 | 96.5 | ~4.23 THB/hr |
| 2 vCPU / 1 GiB   | 213.21 | 263.24 | 331.22 | 43.6 | ~8.45 THB/hr |

**Latency change:** p95 got worse, 148.43 → 263.24 ms (+77%).
**Cost change:** the hourly price doubled, 4.23 → 8.45 THB/hr.

The larger instance was both slower and more expensive. The likely cause is that the model
was trained with `RandomForestClassifier(n_jobs=-1)`, which splits each small prediction
across both cores and adds overhead. The service was reverted to 1 vCPU / 512 MiB afterwards.

Prices: Cloud Run Tier 2 rates for `asia-southeast1` at about 33.2 THB/USD.

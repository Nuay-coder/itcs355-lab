# Lab 5 — Cloud MLOps and Cost

---

## Task 5 — Cost estimate, committed before Task 1

Committed **2026-10-23**, before any Lab 5 pipeline/compute step has run.

| Component | Basis | THB |
|---|---|---|
| Train step | Lab 2 actual training cost, same instance/rate (`reports/lab2-comparison.md`) | 0.14 |
| Ingest/validate/evaluate/register steps | `src/costs.py` e2-standard-4 rate, ~45s each × 4 | 0.32 |
| Deploy to staging + smoke test | Cloud Run rate as Lab 3/4 | 0.05 |
| IAM service accounts, Cloud Scheduler | No usage charge / within free tier | 0.00 |
| AWS portability swap (S3 + on-demand Bedrock invoke, no persistent endpoint) | sub-satang per call | 0.05 |
| LLM golden-set calls (~25 `generate()` calls) | `src/llmcost.py` `gcp.small` rate, ~400 in/120 out tokens/call | 0.19 |

**Total estimate: ~0.75 THB.**

Cumulative confirmed actual spend before Lab 5: 1.92 THB (Lab 2 + Lab 4). This estimate
brings the running total to ~2.7 THB, well inside the 800 THB term budget.

**Likely source of gap:** the pipeline-step and LLM-call counts both assume no debugging
re-runs. The lab also requires forcing the gate to fail at least once, which adds runs
not counted here. Gap explained in `reports/lab5-cost.md` once actual billing is in.

---

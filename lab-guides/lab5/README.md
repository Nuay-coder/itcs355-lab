# Lab 5 — Cloud MLOps and Cost

---

## Task 1 — Managed pipeline

Implemented `compile_gcp()` in `cloudlayer/pipelines.py`: builds a real Kubeflow
Pipelines v2 spec from `pipeline/pipeline.yaml` (one container component per step,
`depends_on` → `.after()`) and compiles it to `pipeline/compiled/gcp_pipeline.json`
(`make pipeline`, local and free — no cloud resource touched).

**The gate is real, not decorative.** `evaluate`'s step now writes a
`--gate-decision-out` file, which becomes a genuine KFP output; `register` and
`deploy_staging` (which depends on it) are only emitted inside a `dsl.If` on that
output. Confirmed by inspecting the compiled JSON directly — the condition compiles to
`inputs.parameter_values['...-gate_decision'] == 'pass'`, not a static flag.

**Gate thresholds, defended** (`scripts/evaluation_gate.py`):
- `MIN_ABSOLUTE = 0.75` — a floor well below this model's actual production range
  (0.8426–0.8545, `reports/lab2-comparison.md`); catches a broken candidate only.
- `MIN_IMPROVEMENT = 0.006` — raised from the scaffold's placeholder `0.002`. Lab 2's
  seed-variance check measured a per-config std of 0.0056–0.0060 across 4 seeds; a
  margin of `0.002` is *smaller* than that noise and would promote noise as if it were
  a real improvement. `0.006` is the minimum bar above the measured noise floor.
- Incumbent now reads from the Vertex Model Registry (`describe_model`) when
  `--incumbent` is omitted, instead of assuming "no incumbent" unconditionally.

**Verified locally** (no cloud calls): forced all three branches with fixture
metrics — below-floor candidate (exit 1), within-noise candidate (delta 0.001 <
0.006, exit 1), and a real improvement (delta 0.01, exit 0) — each writing the
correct `pass`/`fail` to `--gate-decision-out`. `make test` (46 passed) and
`make portability-audit` both still pass.

Added `kfp==2.17.3` to `requirements.in`/`requirements.txt` (regenerated with
`uv pip compile`, matching the Lab 4 convention) — needed to compile a KFP pipeline at
all, and not installed before this lab.

---

## Task 2 — Least privilege, properly

**Audit first.** Before changing anything: `itcs355-lab2-training@...` (training,
Lab 2) held project-wide `roles/storage.objectAdmin`, and — found while auditing —
`cloudlayer/gcp.py::_service_manifest` hardcoded `serviceAccountName` to that same
training identity for every Cloud Run deploy. **Serving was running as training**, with
write access to the whole bucket, since Lab 3. No standing `serviceAccountTokenCreator`
grant existed on it despite that being my first guess — Lab 3's teardown already
removed it, and `scripts/run_loadtest.py` only grants/revokes it transiently around a
load test — so there was nothing to trim there.

**Three identities:**

| Identity | Account | Can do | Why |
|---|---|---|---|
| Training | `itcs355-lab2-training@...` (existing, Lab 2) | `storage.objectAdmin` (project-wide) | Unchanged — reads raw data, writes model artifacts + metrics. Broader than ideal (whole project, not a prefix) but out of scope for this pass; not touched to avoid disturbing Lab 2/3 evidence. |
| **Serving (new)** | `itcs355-lab5-serving@...` | `storage.objectViewer` **scoped to the `itcs355/models/` prefix only** (IAM condition on the bucket), `logging.logWriter`, `monitoring.metricWriter` | Cloud Run now deploys with `--service-account` set to this identity (`cloudlayer/gcp.py::deploy`'s new `service_account` param, defaults to the old training-SA behaviour only when omitted). Can read exactly the model path it was deployed with, nothing else in the bucket, and cannot touch Vertex AI at all. |
| CI | `itcs355-lab4-deploy@...` (existing, Lab 4) | `artifactregistry.writer`, `run.admin`, `aiplatform.viewer`, `monitoring.metricWriter`, plus (new) `iam.serviceAccountUser` on the serving SA | Unchanged role set from Lab 4, plus the one new grant needed to deploy *as* the serving identity. |

**The assessed part: removed a permission, confirmed what broke.**

1. Deployed a throwaway Cloud Run service (`itcs355-lab5-serving-test`, `lab=5`) with
   the new serving SA, pointed at the real production model
   (`...models/437231793901404160@production`). `/predict` worked:
   `{'prediction': 0, 'probability': 0.0130, 'model_version': '437231793901404160-v1', ...}`.
2. Revoked the serving SA's `storage.objectViewer` binding, then deployed a new
   revision (forces a cold start, so the container must re-download the model).
   **It failed.** `gcloud run services replace` returned "container failed the
   configured startup probe checks" — Cloud Run logs show the real cause directly:
   ```
   403 GET https://storage.googleapis.com/download/storage/v1/b/itcs355-6688033/o/itcs355%2Fmodels%2Flab2%2F2017ed98238142c89798d0db9b046bee%2Fmodel.joblib?alt=media:
   itcs355-lab5-serving@itcs355-6688033.iam.gserviceaccount.com does not have
   storage.objects.get access to the Google Cloud Storage object.
   ```
   `service/app.py`'s `_load_model()` runs inside the FastAPI lifespan, so the missing
   permission surfaces as a failed readiness probe, not a crash — `/ready` returned
   `{"status": "not_ready", "reason": "model not loaded"}` for 30 straight checks before
   Cloud Run gave up on the revision.
3. Restored the binding, redeployed, confirmed `/predict` works again with the same
   response as step 1.
4. Deleted the throwaway service (`make`-equivalent `adapter.teardown(cfg.tags(5))`),
   confirmed `gcloud run services list` returns zero.

**What this proves:** the scoping is real, not cosmetic — take away the one grant this
identity actually needs and the service cannot start, with the exact missing
permission visible in the failure, not a generic 500.

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

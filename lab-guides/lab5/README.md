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

## Task 3 — Scheduled retraining

**Scheduling mechanism, demonstrated live** (same Cloud Scheduler → Cloud Run Jobs
pattern Lab 4 used for the drift detector): created `itcs355-lab5-trigger` (Cloud Run
Job, `lab=5`) and `itcs355-lab5-retrain-schedule` (Cloud Scheduler, cron `0 2 * * 0` —
the exact value in `pipeline/pipeline.yaml`), ran it through the **real scheduler**
(`gcloud scheduler jobs run ...`, not a direct job execution), and confirmed success:
`Container called exit(0)` / `SCHEDULED RETRAIN TRIGGER FIRED for provider gcp`. Full
pipeline submission needs `scripts/register.py`/`scripts/deploy.py`, which are
deliberately out of scope here (not in the lab's own file list, and `make pipeline`
itself only compiles) — this demo proves the *trigger path* specifically. Both
resources deleted immediately after (`adapter.teardown(cfg.tags(5))` +
`gcloud scheduler jobs delete`); `gcloud run jobs list` / `gcloud scheduler jobs list`
both confirm zero.

(One real bug hit and fixed along the way: `gcloud run jobs create --args` splits on
commas, so a Python one-liner with a comma in its `print(...)` call silently got cut
into three arguments and failed with `SyntaxError: '(' was never closed`. Fixed by
writing the message as an f-string with no top-level comma.)

**Why `0 2 * * 0` (Sunday 02:00 UTC, weekly):** this system has no real production
traffic — `src/data.py`'s dataset is a deterministic synthetic generator
(`N_MACHINES=240`), not a live feed, and nothing in this repo has ever served a real
request (`reports/lab3-cost.md`). There is no meaningful "data volume" to threshold on
and no real drift signal to react to. A fixed weekly schedule at a low-traffic hour is
the only one of the three strategies that doesn't require a live traffic/labelling
signal this project doesn't have.

**Three strategies, for this system specifically:**

| Strategy | When it is right | How it fails |
|---|---|---|
| Fixed schedule | No reliable volume or drift signal to react to yet (true here — no real traffic). Predictable cost, easy to reason about. | Retrains on a schedule even when nothing changed, wasting the training-step cost every cycle; can also miss a fast-moving real shift between runs. |
| Data volume threshold | A real ingestion pipeline with a steady, known arrival rate — more data = a model worth refreshing. | This project never accumulates new labelled rows at all (`make_dataset.py` regenerates the same fixed-size synthetic set) — the trigger would simply never fire, silently, which is worse than a schedule that at least guarantees periodic review. |
| Drift-triggered | A live feature-drift signal that's actually trustworthy (Lab 4 built the PSI/KS machinery for exactly this). | **The lab's own warning, concretely true here:** Lab 4 already proved `mix`-mode drift can slip past PSI/KS entirely, and the reverse failure is worse — corrupted upstream data (a sensor fault, a unit conversion bug) produces a *real* PSI spike that looks like "retrain now," and an automatic retrain on that corrupted batch bakes the corruption into a newly "validated" model faster than a human would ever approve it on a fixed schedule. |

**Chosen: fixed schedule.** Not because it's the best strategy in general — because this
project has neither the traffic volume nor a trustworthy-enough drift signal (per Lab
4's own `mix`-mode finding) to justify the other two yet. **Worst case:** a real
upstream corruption appears between two Sunday runs and sits undetected for up to a
week before the next scheduled retrain either catches it (if the gate's metric floor
rejects the corrupted candidate) or — if the corruption is subtle enough to still clear
`MIN_ABSOLUTE`/`MIN_IMPROVEMENT` — gets promoted. This is exactly why Task 1's gate
compares against the registry incumbent rather than trusting any single run: a fixed
schedule bounds *when* a bad retrain could happen, but only the gate stops a bad
candidate from being registered once it does.

---

## Task 4 — The portability test

**Part A — audit.** `make portability-audit` passes, before and after this task's
changes: `src`, `service`, `monitoring`, `tests` contain no provider-specific strings.

**Part B — swap.** Chosen second provider: **AWS** (Azure was the original plan, but
the only Azure account available — a Mahidol student account — has zero subscriptions;
switching providers mid-task would also have meant starting Task 4B's code from zero,
so AWS stayed the choice once a usable AWS account existed). Implemented exactly
`upload`, `download`, `invoke` in `cloudlayer/aws.py`: `upload`/`download` via
`boto3`'s S3 client against `BLOB_URI`; `invoke` against Bedrock's on-demand
`InvokeModel` (Claude 3 Haiku — there is no SageMaker endpoint to call, and standing
one up is explicitly out of scope: "you are not migrating the whole system").

Ran `scripts/portability_swap_check.py --second-provider aws` for real, against a real
AWS account set up during this task. **Every method failed, and every failure is a
genuine finding, not a bug in the new code:**

| Method | Result | Real cause |
|---|---|---|
| `upload` | `ParamValidationError: Invalid bucket name "gs:"` | The swap script changes only `provider`, leaving `BLOB_URI` as `gs://itcs355-6688033/itcs355` — a GCS-shaped URI, not S3-shaped. `cloud.env`'s values are *not* provider-neutral despite `provider` being a single flag. |
| `download` | Same `"gs:"` bucket-name error | Same root cause — `download`'s input URI comes from the same config. |
| `invoke` | First: `EndpointConnectionError` against `bedrock-runtime.asia-southeast1.amazonaws.com` (not a real AWS endpoint) | `cfg.region` is `asia-southeast1` — GCP's region-naming convention. AWS's equivalent region is `ap-southeast-1`. Region names leak the same way `BLOB_URI`'s scheme does. |
| `invoke` (region hand-corrected) | `AccessDeniedException: Your account is currently being verified` | Not a code or config problem at all — Bedrock model invocation is held for new AWS accounts pending an automatic fraud-prevention check (AWS's own message: "normally takes less than 2 hours"). |

A fourth, separate blocker was found auditing the account before any of this: the AWS
account itself sits inside an **AWS Organization** with a **Service Control Policy**
(`arn:aws:organizations::761774876005:policy/.../p-nj126grk`) that explicitly denies
`s3:CreateBucket` and `s3:ListAllMyBuckets` account-wide — discovered by the user, who
did not know the account belonged to an Organization at all. No bucket could be created
to even attempt `upload`/`download` against real S3, independent of the config leaks
above.

**Part C — write-up.**

- **Hardest method:** `invoke`, but not for a coding reason — Bedrock's request shape
  (`messages`/`anthropic_version`/`max_tokens`) has nothing in common with Cloud Run's
  plain HTTP `/predict`, so `endpoint`/`payload` from the shared `CloudAdapter`
  interface map onto it only nominally (`endpoint` is accepted and ignored entirely).
  `upload`/`download` were the easy part, code-wise — boto3's S3 client is a close
  mirror of `google-cloud-storage`'s.
- **Where the abstraction genuinely leaked:** `cloud.env`'s `BLOB_URI` and `REGION`
  are stored as provider-formatted strings (`gs://...`, `asia-southeast1`), not neutral
  values. `CLOUD_PROVIDER` is one flag; the other six capability slots are not actually
  provider-independent despite living in the same "Layer 2" file — flipping the
  provider alone, exactly as `portability_swap_check.py`'s own internal swap does,
  breaks on the very first real call. A genuine migration means rewriting the whole of
  `cloud.env`, not just one line in it.
- **What a full migration would cost:** rewriting `cloud.env` is trivial; the real
  cost is the five AWS methods this task deliberately left unimplemented
  (`push_image`, `submit_training`/`register_model`, `deploy`, `emit_metric`,
  `generate`, `teardown`) — each one took a Lab's worth of real engineering time to
  get right on GCP the first time (Lab 2's training+registry work, Lab 3's deploy+
  invoke+canary work, Lab 4's monitoring+scheduling work). Call it **8–12 engineering
  days** for the code alone, scaled from that history. That estimate excludes exactly
  what consumed the most *calendar* time in this task: an Organization SCP nobody knew
  existed, and a new-account verification hold — neither one an engineering problem,
  both of them real migration cost.
- **Was it worth it?** Partially. The three-layer discipline earns its keep as a
  *testing* tool — `make portability-audit` catching a leak before it ships is real
  value, and it cost nothing extra this lab. As a *migration-cost* reducer it did far
  less than advertised: two of `cloud.env`'s six capability slots turned out to encode
  provider-specific formats, so "point `cloud.env` at the other provider" is not the
  one-line change the abstraction implies — a team migrating for real would still need
  to rewrite config by hand and re-verify every value, same as without the adapter
  layer. Worth building for the audit; oversold as a migration shortcut.

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

Full six-section report (actual billing checked live, gap explained, breakdown,
cost-per-1,000, and the applied optimisation): [`reports/lab5-cost.md`](../../reports/lab5-cost.md).

---

## Task 6 — Teardown, verified

`make teardown LAB=5` run — returned `[]` (nothing tagged `lab=5` was still alive;
everything created during Tasks 2 and 3 was already torn down immediately after each
test, not left running). Confirmed independently, not just trusting the adapter's own
return value:

- `gcloud run services list` → 0 items
- `gcloud run jobs list` → 0 items
- `gcloud scheduler jobs list` → 0 items
- Screenshot: [`evidence/task6-teardown-cloud-run-jobs-empty.jpg`](evidence/task6-teardown-cloud-run-jobs-empty.jpg)

`make test` (46 passed) and `make portability-audit` (passed) both still hold after
teardown.

Left in place, deliberately (not billed resources): `itcs355-lab5-serving@...` (IAM
service account, Task 2) and `itcs355-lab4-deploy@...`'s new grant on it — the same
"IAM survives teardown, billed compute doesn't" pattern Lab 4 used.

**24-hour recheck:** per the lab's own warning ("deletion is asynchronous"), `make
cost-report` should be re-run ~24h after this teardown to confirm nothing reappears
in billing afterward — not done yet as of this write-up; the first teardown
confirmation above is same-day.

---

# Lab 4 — CI/CD, Observability, and Drift

---

## What this lab delivers

- Four test categories (unit, data contract, model behaviour, integration), each one that
  can fail for a real reason
- CI pipeline: lint → unit → data contract → model behaviour → build → integration test →
  push (main only) → deploy to staging (main only)
- Evidence of a deliberately bad commit blocked by CI
- A dashboard covering request rate, error rate (4xx/5xx), latency p50/p95/p99, feature
  drift, and model version
- An SLO with a stated error-budget response
- A scheduled drift detector with a justified threshold, alerting by email
- An injected-drift exercise with timestamped evidence and a five-line post-mortem

---

## Task 1 — Tests that can actually fail

Reused the existing Lab 1–3 code and dataset; nothing here trains a new production model.

- **Unit** (new: `tests/test_unit.py`) — `src/data.py`'s `data_fingerprint`/`split`, and
  `monitoring/drift.py`'s `psi`/`ks_statistic`, which nothing tested before this even
  though Tasks 5 and 6 depend on it being correct.
- **Data** (existing: `tests/test_data.py`) — contract tests (schema, nulls, ranges,
  target shape, ID uniqueness) and the split-logic tests, including the Lab 1
  machine-leakage test the handout asks to keep passing.
- **Model behaviour** (existing: `tests/test_model_behaviour.py`) — valid probabilities,
  a known healthy machine scoring low, risk rising with wear, latency under budget,
  predictions not constant. Set `LATENCY_BUDGET_MS = 50 ms`, a quarter of the 200 ms
  end-to-end p95 in `loadtest/k6.js:64` — this test only measures in-process
  `predict_proba`, not the full HTTP round trip.
- **Integration** (existing, extended in `.github/workflows/ci.yml`) — build → start →
  `/ready` → `POST /predict`. Now asserts the full response shape via `jq -e` (numeric
  `prediction`/`probability` in `[0,1]`, string `request_id`) and, per the handout's
  requirement, that `model_version` matches the commit SHA.

### What the data contract tests would catch

Examples of the kind of problem each check would catch — not incidents actually observed
in production, since this project has never served real traffic.

| Data contract | Problem it would catch |
|---|---|
| Expected columns and types (`test_schema_columns_present_and_typed`) | Upstream renames `load_pct` → `load_percent`, or a column's dtype silently changes. |
| No nulls in required columns (`test_no_nulls_in_required_columns`) | A sensor drops out, leaving missing `vibration_mm_s` values that would otherwise reach `predict_proba`. |
| Features within plausible ranges (`test_features_within_plausible_ranges`) | `temp_c` reported in Fahrenheit instead of Celsius, or an impossible `load_pct = 250`. |
| Labels 0/1, positive rate in (1%, 99%) (`test_target_is_binary_and_not_degenerate`) | Every row relabelled `failed_within_7d = 0`, or failures encoded as `2`. |
| Unique `reading_id` (`test_identifier_is_unique`) | An ingestion retry double-writes a batch of readings. |
| No machine leaks across splits (`test_no_machine_leaks_across_splits`) | `split()` refactored to split by `reading_id` instead of `machine_id`. |

Validated by running each check's assertion against 9 deliberately corrupted copies of
the in-memory dataframe (one per row above, plus a row-wise split for leakage) — **9/9
rejected**, without touching `data/raw/sensors.csv` on disk. These are local sanity
checks, not the failing-PR evidence Task 3 requires (below, once done).

---

## Task 2 — CI pipeline

GCP Workload Identity Federation (no stored keys): a WIF pool + GitHub OIDC provider
scoped to `assertion.repository == 'Nuay-coder/itcs355-lab'`, and a deploy service
account (`itcs355-lab4-deploy`) impersonated through it.

- `ci.yml` — added a unit-test step ahead of data-contract tests, turned on
  `--require-hashes`, and (main-only, gated on `push` to `main`) pushes the SHA-tagged
  serving image via `GcpAdapter.push_image()`.
- `cd.yml` — replaced the AWS/Azure stubs and echo placeholders with a real deploy:
  resolve the digest CI already pushed, `adapter.deploy()` to a **staging-only** Cloud
  Run service (`itcs355-predict-staging`, separate from Lab 3's production one, tagged
  `lab=4` so its own teardown can find it), then the existing 3-payload smoke test.

Verified live: `gcloud run services describe itcs355-predict-staging` shows
`lab: '4'`; CD's Smoke test passed 3/3 (single, batch, invalid→422) on two consecutive
runs after the fixes below.

### Problems and fixes

| Problem | Fix |
|---|---|
| `ImportError: aiplatform_v1` on CD's first real run | `requirements.txt` never locked `google-cloud-aiplatform` — it only worked locally because the dev machine had it installed outside the pinned file. Added it to `requirements.in` and regenerated with `uv pip compile` (the tool the original lockfile's header says it was built with — plain `pip-compile` silently drops the `setuptools` entry `uv` resolves via `zc-lockfile`). |
| `403 aiplatform.models.get` denied | Deploy SA had no Vertex AI read access. Granted `roles/aiplatform.viewer`. |
| `PERMISSION_DENIED` impersonating the training SA | `adapter.invoke()`'s token fallback chain's last tier impersonates `TRAINING_SERVICE_ACCOUNT`, which the deploy SA has no rights over, and the untargeted ambient tier ahead of it is flaky under WIF — it passed once, then failed identically on the very next run with no config change. Pointed the smoke test's `TRAINING_SERVICE_ACCOUNT` at the deploy SA itself (self-impersonation; it already holds `serviceAccountTokenCreator` on itself). |
| `requests.exceptions.InvalidHeader` once impersonation finally succeeded | Real bug in `cloudlayer/gcp.py`: `_fetch_identity_token()` merges `stderr` into `stdout` and returned the whole thing as the token — `gcloud`'s "using service account impersonation" warning (printed on *every* impersonated call, not just failures) ended up prepended to the real JWT. `mint_loadtest_token()` had already hit and fixed this exact bug by keeping `stderr` separate; this function never got the same fix. Fixed by taking only the last non-blank line. |

---

## Task 3 — Prove a bad commit is blocked

[PR #7](https://github.com/Nuay-coder/itcs355-lab/pull/7) deliberately generated
`temp_c` in Fahrenheit (every value stays numeric and type-correct, so only the
range check catches it). CI's `test` job failed in 70s on
`tests/test_data.py::test_features_within_plausible_ranges`
(`temp_c above plausible ceiling: 240.215`), and the `build` job — the expensive
step that builds two Docker images — never ran at all, skipped because it `needs:
test`. PR closed without merging; full evidence in
[`reports/lab4-ci-evidence.md`](../../reports/lab4-ci-evidence.md).

---

## Task 4 — Instrumentation, dashboard, SLO

**`emit_metric`** (`cloudlayer/gcp.py`) — writes one Cloud Monitoring custom metric
point via the REST `timeSeries.create` API (no `gcloud` command exists for this).
Verified live: called directly, confirmed the point lands via `timeSeries.list`, then
exercised through `python -m monitoring.drift --emit` against the real dataset.

**RED signals** (request rate, error rate 4xx/5xx, latency p50/p95/p99) come from
**Cloud Run's own free built-in metrics** (`run.googleapis.com/request_count`,
`request_latencies`, both already split by `response_code_class`) rather than custom
instrumentation in `service/app.py`. Same signals, zero risk to the request path —
`emit_metric` on every `/predict` call would add a network round trip to the latency
budget `tests/test_model_behaviour.py` is trying to protect.

**Model version in production** — emitted once per deploy (`cd.yml`'s Deploy step,
non-fatal on failure), not once per request. Confirmed live: 3 consecutive CD runs
each wrote a `model_version` data point, visible via `timeSeries.list`.

**Dashboard as code** (`monitoring/dashboard.gcp.json`) — `monitoring/dashboard.json`
is Grafana/PromQL-shaped and can't be imported into Cloud Monitoring directly;
translated the same 5 panels into Cloud Monitoring's dashboard schema and kept the
Grafana file alongside it. Created live via `gcloud monitoring dashboards create
--config-from-file=`
(`projects/126202218664/dashboards/943309b8-711b-43ca-a005-1bc3cf55f941`) — two real
schema rejections fixed along the way (an `xyChart` threshold only accepts `value`,
not `color`/`direction`). **Teardown note:** this API has no label/tag support, so
`cfg.tags()`-based teardown cannot find this dashboard — it must be deleted
explicitly by id.

**SLO** (`monitoring/slo.yaml`) — availability 99.5%/30d (~3.6h/month error budget,
defended against this service's own traffic pattern, not a borrowed industry number);
latency p95 < 200ms, confirmed to match `loadtest/k6.js:64` exactly; freshness ≤30
days, with its `on_budget_exhausted` noting that Lab 5's retrain cadence must stay at
or under 30 days or the two targets contradict each other. Every objective's
`on_budget_exhausted` names a concrete mechanism (freeze deploys + roll back to last
known-good, or trigger retraining) rather than staying silent.

---

## Task 5 — Scheduled drift detection

**Threshold** (`monitoring/drift.py`, `ALERT_THRESHOLD = 0.15`) — measured, not copied:
compared disjoint random samples of the training data against itself at the 500-row
window size the scheduled job actually uses (40 trials × 6 features). Pure sampling
noise there runs p99≈0.087 — above the credit-scoring convention `PSI_NO_CHANGE=0.10`,
which is exactly the "everything alerts constantly" failure mode the handout warns
about. The actual injected drift this project tests against (`scripts/inject_drift.py`
default) scores 0.369 at the same window size. 0.15 sits comfortably above the noise
and far below the real signal.

**Stated gap**: this project has never served real traffic
(`reports/lab3-cost.md`) and `service/app.py` has no logging sink that persists
recent request features anywhere queryable. "Recent production inputs" is simulated
as whatever file sits at `gs://<bucket>/lab4/current.csv` — Task 6 uploads a
deliberately shifted version of it. A system with real traffic would populate that
file from an actual logging sink instead; building one was out of scope here.

**Infrastructure, verified live:**
- Training image now includes `monitoring/`; a Cloud Run Job (`itcs355-lab4-drift`)
  reuses it with its `ENTRYPOINT`/`CMD` overridden to run
  `scripts/run_scheduled_drift.py`. `gcloud run jobs execute` completed; its logs show
  the drift table and `OK  no feature above threshold 0.15`.
- Cloud Scheduler job (hourly, HTTP target at the Cloud Run Jobs REST `:run` endpoint)
  — manually triggered and confirmed a **second**, scheduler-initiated execution ran
  (not just a direct `jobs execute`), proving the Scheduler → Cloud Run Jobs API path
  itself works, not only the job in isolation.
- Email notification channel created and **verified** (`verificationStatus: VERIFIED`)
  — Cloud Monitoring sent a plain alphanumeric code by email (not a clickable link),
  confirmed via `notificationChannels.verify`.
- Alert policy (`monitoring/alert-policy.json`) — one condition per feature, OR
  combined, each at `ALERT_THRESHOLD`. One condition per feature because the feature
  name is encoded in the metric *type* (`drift.psi.<feature>`), not a label, so a
  single wildcard condition isn't possible — confirmed live:
  `monitoring.regex.full_match` is rejected on `metric.type` in alert-policy filters.
- `teardown()` extended for all of this: Cloud Run jobs (label-filtered, same as
  services); Cloud Scheduler jobs, alert policies, and notification channels
  (name/displayName-convention matched, since none of those three support labels in
  this API — confirmed live that a Scheduler job created here comes back with
  `labels: null`). All four new filters confirmed to match the real resources created
  above; not yet executed, since Task 6 still needs them alive.

---

## Task 6 — The injected drift exercise

### Which statistic catches which mode

Ran all three `scripts/inject_drift.py` modes against `temp_c` at default magnitude:

| Mode | PSI | KS | Verdict | Alert fired? |
|---|---|---|---|---|
| `shift` (+6 mean) | 0.38333 | 0.24567 | significant | Yes |
| `scale` (×6 spread) | 1.90368 | 0.38333 | significant | Yes |
| `mix` (reweight 1/5 of machines ×5) | 0.00873 | 0.03417 | stable | **No** |

`mix` genuinely did not trigger at this magnitude — confirming the handout's own
framing of it as "the realistic one, and the hardest to spot" with real numbers, not
just restating the claim. `scale` moved both statistics further than `shift` here
because a ×6 spread multiplier is a larger effective distortion than a flat +6 shift
against this feature's ~9-unit standard deviation; the two injected magnitudes are not
calibrated to be equivalent in effect size, only equal in their `--magnitude` argument.

### The real exercise — through the actual scheduled path

| Event | Timestamp (UTC) |
|---|---|
| Injected `temp_c +6` (shift mode), uploaded to `gs://.../lab4/current.csv` | `2026-10-11T09:05:40Z` |
| Triggered via the real Cloud Scheduler job (`gcloud scheduler jobs run`, not a direct job execution) | `2026-10-11T09:06:00Z` |
| Cloud Run Job execution `itcs355-lab4-drift-8vgb6` ran, scored PSI=0.38333, exited 2 (ALERT) | started `09:06:08Z` |
| `drift.psi.temp_c` metric landed in Cloud Monitoring | `09:06:37Z` |
| **Alert email fired** (`[ALERT - Warning] temp_c PSI above threshold on itcs355-6688033`) | `09:09:00Z` |

**Detection latency: 3 minutes 20 seconds** (injection → alert). Breaking that down:
scheduler trigger → alert was 3m00s, and of that, 2m23s was Cloud Monitoring's own
alert-evaluation delay (metric landed at `09:06:37Z`, alert started `09:09:00Z`) — the
job itself ran in under 30 seconds. This run used a manual scheduler trigger
immediately after injection; with the job's actual **hourly** cadence, worst-case
detection latency in production would be up to ~1 hour plus this same ~2-3 minute
evaluation delay, not the 3m20s observed here.

Alert evidence (screenshot of the actual email received):

![Drift alert email](evidence/task6-alert-email.png)

The email confirms: policy `ITCS355 Lab 4 — feature drift (PSI)`, condition `temp_c
PSI above threshold`, metric `custom.googleapis.com/itcs355/drift.psi.temp_c`, value
`0.38333` against threshold `0.15`, start time `Oct 11, 2026 at 9:09AM UTC`.

Cloud Monitoring has no public API for listing fired incidents (confirmed: no
`gcloud`/REST method exists for it — only the Console UI shows them), so the email is
the primary evidence here, not a queryable API response like every other check in
this lab.

Dashboard evidence (the live Cloud Monitoring dashboard from Task 4, "Last 1 hour"
view, taken shortly after the alert fired):

![Dashboard showing the drift spike](evidence/task6-dashboard-drift.png)

The "Feature drift — temp_c PSI (rolling)" panel (bottom right) shows the
`drift.psi.temp_c` line climbing from its flat 0 baseline up to meet the red
`0.15` threshold line — the same shift the alert fired on, visible on the same
dashboard the SLO's other three signals (request rate, error rate, latency) already
use. The two annotation markers before the final spike are the earlier baseline
scheduled runs (`09:00Z`, and the manual verification run from Task 5); the one at
the spike is this exercise's.

### Five-line post-mortem

**What fired:** Alert policy `ITCS355 Lab 4 — feature drift (PSI)`, condition `temp_c
PSI above threshold`, on metric `custom.googleapis.com/itcs355/drift.psi.temp_c` =
`0.38333` against threshold `0.15`. Started `2026-10-11T09:09:00Z` UTC.

**True cause:** A synthetic injection (`scripts/inject_drift.py --feature temp_c
--mode shift --magnitude 6`), not a real upstream event. Schema and null rate were
unchanged (the shifted value, 85.58°C mean, is still inside `PLAUSIBLE_RANGES`
(-10, 140), so the data contract tests would still pass on this file — only PSI
caught it). Per the handout's own decision tree: schema/null-rate unchanged → input
distribution moved → is it legitimate and persistent? This is a single injected
batch, run once, not observed across multiple consecutive scheduled windows.

**Retrain, roll back, or no action — and why:** **No action; tighten nothing yet,
keep watching.** A one-off synthetic shift from a single test run is not evidence of
a persistent real-world change — retraining on it would mean retraining on a value I
manufactured, not on anything the fleet actually produced. This would change if the
same feature kept showing PSI above threshold across several consecutive hourly
runs with no further injection, or if there were independent confirmation (a fleet
firmware changelog, a sensor replacement log) that `temp_c`'s true distribution had
actually moved.

**What this would have cost if unnoticed for a week:** Assuming this were a real,
persistent +6°C sensor miscalibration across the fleet rather than a one-off test:
the synthetic data generator (`scripts/make_dataset.py`) weights `temp_c` into the
failure-risk logit at `+0.052` per degree, so a systematic +6°C read inflates every
affected machine's predicted risk by roughly `0.052 × 6 ≈ 0.31` logit-units — enough
to push many borderline machines across the decision threshold into
"flag for maintenance." At Lab 3's measured throughput (~96.5 req/s capacity,
`reports/lab3-load.md`) and assuming only a small fraction of that is real traffic
(this service has never served real users, `reports/lab3-cost.md`), the realistic
cost is not infrastructure spend but **decision cost**: a week of systematically
over-flagged (or under-flagged, if the miscalibration ran the other way) maintenance
dispatches. Without a real cost-per-dispatch or cost-per-missed-failure number for
this synthetic fleet, a precise THB figure would be fabricated — the honest estimate
is "however many additional maintenance visits per week times their cost, for the
borderline fraction of the fleet," not a number this project has the data to compute
confidently.

**How to prevent or detect it faster:** The 2m23s Cloud Monitoring evaluation delay
is fixed and small; the **hourly Cloud Scheduler cadence is the real lever** — a
shift to every 15 minutes would cut worst-case detection latency from ~1 hour to
~15 minutes for a small increase in Cloud Run Job invocation count (the job itself
runs in under 30 seconds, so cost impact is minor). A concrete second change: add a
mean-shift-specific check (not just PSI) alongside the existing drift detector, since
`mix` mode — the one closest to a real "fleet composition changed" scenario — scored
PSI=0.00873 here and would not have alerted at all at this magnitude.

---

## Reports

- [`reports/lab4-ci-evidence.md`](../../reports/lab4-ci-evidence.md) — Task 3's
  failing-PR evidence: the exact assertion, timing, and why the build job never ran.

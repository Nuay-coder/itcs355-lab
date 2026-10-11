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

## Reports

- [`reports/lab4-ci-evidence.md`](../../reports/lab4-ci-evidence.md) — Task 3's
  failing-PR evidence: the exact assertion, timing, and why the build job never ran.

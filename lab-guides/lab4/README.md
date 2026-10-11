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

## Reports

Task 3's failing-PR evidence (`reports/lab4-ci-evidence.md`) is not written yet — a
link here would be broken (the repo's `links` check already caught this once). Linked
once that report exists.

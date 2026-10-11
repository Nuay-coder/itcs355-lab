# Lab 4 Task 3 — evidence of a blocked bad commit

## The deliberate break

`scripts/make_dataset.py` — `temp_c` generated in Fahrenheit instead of Celsius
(one-line change, [commit a657647](https://github.com/Nuay-coder/itcs355-lab/commit/a657647)):

```diff
-                "temp_c": round(float(temp), 3),
+                "temp_c": round(float(temp * 9 / 5 + 32), 3),  # BUG(Task 3): accidental Fahrenheit conversion
```

Every value stays numeric and type-correct, so the schema test cannot catch it — only
the plausible-range contract test can. This is the exact incident named in
[`lab-guides/lab4/README.md`](../lab-guides/lab4/README.md)'s contract-test table for
`test_features_within_plausible_ranges`: a firmware update that changes units without
anyone noticing.

## PR and failing run

PR: [#7](https://github.com/Nuay-coder/itcs355-lab/pull/7) — **not merged, closed deliberately**.

Failing run: [actions/runs/38121259220](https://github.com/Nuay-coder/itcs355-lab/actions/runs/38121259220)

| | |
|---|---|
| Job | `test` — **failed** in 70s (`07:24:31` → `07:25:41` UTC) |
| Job | `build` — **skipped** (`needs: test`, never started — no image was built for a commit that never passed its cheap checks) |
| Step that caught it | `Data contract tests` (`pytest -q tests/test_data.py`) |
| Test | `tests/test_data.py::test_features_within_plausible_ranges` |

## The exact failure

```
..F.......                                                               [100%]
=================================== FAILURES ===================================
____________________ test_features_within_plausible_ranges _____________________

    def test_features_within_plausible_ranges(df):
        for col, (lo, hi) in data.PLAUSIBLE_RANGES.items():
            assert df[col].min() >= lo, f"{col} below plausible floor: {df[col].min()}"
>           assert df[col].max() <= hi, f"{col} above plausible ceiling: {df[col].max()}"
E           AssertionError: temp_c above plausible ceiling: 240.215
E           assert np.float64(240.215) <= 140.0

tests/test_data.py:51: AssertionError
FAILED tests/test_data.py::test_features_within_plausible_ranges - AssertionError: temp_c above plausible ceiling: 240.215
1 failed, 9 passed in 0.45s
```

## What this proves

A green pipeline proves nothing about whether the tests work — this failing run does.
The cheap-first ordering in `ci.yml` (lint → unit → data contract → model behaviour →
build → integration test) paid off exactly as designed: the bad commit was caught in
the `test` job (70 seconds total, including lint, portability audit, dataset
generation, and the unit tests that ran before it) and the `build` job — the
expensive step that builds two Docker images and runs an integration test — never ran
at all, because GitHub Actions skips a job whose `needs:` dependency failed.

## Teardown

PR #7 was closed without merging. `scripts/make_dataset.py` on `main` is unaffected —
the break lived only on the `break-data-contract` branch, which was deleted with the PR.

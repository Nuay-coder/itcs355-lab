"""Lab 3 Task 2 — smoke test: adapter.invoke() against a live Cloud Run endpoint with
three known payloads (single valid, batch valid, deliberately invalid), asserting
model_version is present on every successful response.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from cloudlayer.gcp import InvokeError
from src import config

VALID = {
    "temp_c": 78.4, "vibration_mm_s": 3.1, "pressure_kpa": 315.2,
    "hours_since_service": 4200.0, "load_pct": 68.0, "ambient_humidity": 55.0,
}
BATCH = {"rows": [VALID, {**VALID, "temp_c": 92.0}]}
INVALID = {**VALID, "load_pct": 250.0}  # outside PLAUSIBLE_RANGES -> expect 422


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint-name", required=True, help="Cloud Run service name")
    args = ap.parse_args()

    cfg = config.load()
    adapter = get_adapter(cfg)
    name = args.endpoint_name
    print(f"endpoint: {name} -> {adapter.service_url(name)}")
    failures = 0

    print("\n[1/3] single valid payload -> /predict")
    result = adapter.invoke(name, VALID, route="/predict")
    print(f"  prediction={result['prediction']} probability={result['probability']:.4f} "
          f"model_version={result['model_version']}")
    if not result.get("model_version"):
        print("  FAIL: model_version missing")
        failures += 1

    print("\n[2/3] batch valid payload (2 rows) -> /predict/batch")
    result = adapter.invoke(name, BATCH, route="/predict/batch")
    print(f"  n={result['n']} model_version={result['model_version']}")
    if result.get("n") != len(BATCH["rows"]) or not result.get("model_version"):
        print("  FAIL: unexpected batch result")
        failures += 1

    print("\n[3/3] invalid payload (load_pct=250) -> expect 422")
    try:
        adapter.invoke(name, INVALID, route="/predict")
        print("  FAIL: expected InvokeError(422), got a 2xx response")
        failures += 1
    except InvokeError as exc:
        if exc.status_code == 422:
            print(f"  OK: got 422 as expected ({exc})")
        else:
            print(f"  FAIL: expected 422, got {exc.status_code}: {exc}")
            failures += 1

    if failures:
        print(f"\n{failures} smoke check(s) FAILED")
        return 1
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

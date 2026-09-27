"""Lab 3 Task 3, Step 2 — one cold-start sample.

Run this only after confirming the service has been genuinely idle (no forced
scale-to-zero API exists for Cloud Run — see reports/lab3-load.md for how long we
waited and why). Grants + mints a token, then retries the SAME timed /predict call
until it returns 200 — any 401/403 along the way is IAM-edge auth propagation, which
never reaches the container and so never triggers a cold start; only the first 200's
latency is the real sample. Appends one line to --out (default reports/raw/lab3-cold-start.jsonl).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

from cloudlayer.factory import get_adapter
from run_loadtest import mint_with_retry
from src import config

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE_ROW = {
    "temp_c": 78.4, "vibration_mm_s": 3.1, "pressure_kpa": 315.2,
    "hours_since_service": 4200.0, "load_pct": 68.0, "ambient_humidity": 55.0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "raw" / "lab3-cold-start.jsonl")
    ap.add_argument("--timeout-s", type=int, default=180)
    args = ap.parse_args()

    cfg = config.load()
    adapter = get_adapter(cfg)

    print(f"granting token-creator + run.invoker for {args.endpoint} ...")
    adapter.grant_token_creator(endpoint=args.endpoint)
    sample = None
    try:
        token = mint_with_retry(adapter, args.endpoint)
        url = adapter.service_url(args.endpoint).rstrip("/") + "/predict"
        body = json.dumps(_PROBE_ROW)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

        deadline = time.monotonic() + args.timeout_s
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            t0 = time.perf_counter()
            resp = requests.post(url, data=body, headers=headers, timeout=60)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code == 200:
                sample = {
                    "ts": datetime.now(UTC).isoformat(),
                    "latency_ms": round(elapsed_ms, 2),
                    "auth_attempts_before_success": attempts - 1,
                    "model_version": resp.json().get("model_version"),
                }
                print(f"cold-start sample: {sample['latency_ms']}ms "
                      f"(after {attempts - 1} auth-propagation retries that never reached the container)")
                break
            time.sleep(3)
        else:
            print(f"never got a 200 within {args.timeout_s}s")
            return 1
    finally:
        adapter.revoke_token_creator(endpoint=args.endpoint)

    if sample:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as fh:
            fh.write(json.dumps(sample) + "\n")
        print(f"appended to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Lab 3 Task 3, Step 4a — does /predict/batch with N rows beat N calls to /predict?

For each N in --sizes: times N sequential /predict calls back to back, and times one
/predict/batch call carrying the same N real held-out rows. Repeated --reps times per N
so the reported numbers are a median, not a single noisy sample.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

from cloudlayer.factory import get_adapter
from run_loadtest import mint_with_retry, wait_until_authenticated
from src import config

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Retry403(Exception):
    """Signals a transient edge-level 403 mid-loop — caught to restart the whole timed
    operation from a fresh t0, never counted as part of a measured latency."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 100])
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--rows-file", type=Path, default=REPO_ROOT / "loadtest" / "holdout_rows.json")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "raw" / "lab3-batch-vs-single.json")
    args = ap.parse_args()

    rows = json.loads(args.rows_file.read_text())

    cfg = config.load()
    adapter = get_adapter(cfg)
    print(f"granting + minting token for {args.endpoint} ...")
    adapter.grant_token_creator(endpoint=args.endpoint)
    results = {}
    try:
        token = mint_with_retry(adapter, args.endpoint)
        base = adapter.service_url(args.endpoint).rstrip("/")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        wait_until_authenticated(base + "/predict", token)
        print("verified. running comparison ...\n")

        for n in args.sizes:
            sample_rows = [rows[i % len(rows)] for i in range(n)]
            single_totals, batch_totals = [], []

            for _ in range(args.reps):
                # A stray edge-level 403 (see run_loadtest.wait_until_authenticated's
                # docstring) must never leak retry/backoff time into a latency number —
                # so on a 403, the WHOLE timed operation restarts from a fresh t0 rather
                # than retrying the one failed request mid-measurement.
                while True:
                    t0 = time.perf_counter()
                    try:
                        for row in sample_rows:
                            r = requests.post(base + "/predict", json=row, headers=headers, timeout=30)
                            if r.status_code == 403:
                                raise _Retry403
                            r.raise_for_status()
                        single_totals.append((time.perf_counter() - t0) * 1000)
                        break
                    except _Retry403:
                        time.sleep(3)

                while True:
                    t0 = time.perf_counter()
                    r = requests.post(base + "/predict/batch", json={"rows": sample_rows},
                                       headers=headers, timeout=30)
                    if r.status_code == 403:
                        time.sleep(3)
                        continue
                    r.raise_for_status()
                    batch_totals.append((time.perf_counter() - t0) * 1000)
                    break

            single_med = statistics.median(single_totals)
            batch_med = statistics.median(batch_totals)
            results[n] = {"single_ms": single_med, "batch_ms": batch_med,
                           "speedup_x": single_med / batch_med if batch_med else None}
            print(f"n={n:>4}  {n} singles: {single_med:8.1f}ms (median)   "
                  f"1 batch: {batch_med:8.1f}ms (median)   "
                  f"speedup: {single_med / batch_med:.2f}x")
    finally:
        adapter.revoke_token_creator(endpoint=args.endpoint)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

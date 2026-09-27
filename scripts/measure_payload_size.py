"""Lab 3 Task 3, Step 4b — where does response serialization start to dominate?

Payload size for this service can only really scale via /predict/batch's row count —
PredictRequest has extra="forbid" and a fixed schema, so a single /predict call cannot
be inflated with junk fields (that would just be a 422). Sends one batch request at each
row count in --sizes, then reads back the server's OWN structured log line for that
request_id (parse_ms/predict_ms/serialize_ms, logged by service/app.py) — real
server-side timing, not a client-side estimate that also bundles in network transfer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
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


def fetch_server_timing(project_id: str, service_name: str, request_id: str,
                         timeout_s: int = 30, interval_s: int = 3) -> dict | None:
    """Cloud Logging ingestion lags a few seconds behind the request — poll for it."""
    filter_expr = (
        f'resource.type="cloud_run_revision" AND '
        f'resource.labels.service_name="{service_name}" AND '
        f'jsonPayload.request_id="{request_id}"'
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["gcloud", "logging", "read", filter_expr, "--project", project_id,
             "--limit", "1", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            entries = json.loads(result.stdout)
        except json.JSONDecodeError:
            entries = []
        if entries:
            return entries[0]["jsonPayload"]
        time.sleep(interval_s)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 5, 10, 25, 50, 100])
    ap.add_argument("--rows-file", type=Path, default=REPO_ROOT / "loadtest" / "holdout_rows.json")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "raw" / "lab3-payload-size.json")
    args = ap.parse_args()

    rows = json.loads(args.rows_file.read_text())
    cfg = config.load()
    adapter = get_adapter(cfg)

    print(f"granting + minting token for {args.endpoint} ...")
    adapter.grant_token_creator(endpoint=args.endpoint)
    results = []
    try:
        token = mint_with_retry(adapter, args.endpoint)
        base = adapter.service_url(args.endpoint).rstrip("/")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        wait_until_authenticated(base + "/predict", token)
        print("verified. measuring ...\n")

        for n in args.sizes:
            sample_rows = [rows[i % len(rows)] for i in range(n)]
            body = json.dumps({"rows": sample_rows})
            # A stray edge-level 403 doesn't taint anything measured here (server-side
            # log timing, not client wall time) — just retry it directly.
            while True:
                resp = requests.post(base + "/predict/batch", data=body, headers=headers, timeout=30)
                if resp.status_code != 403:
                    break
                time.sleep(3)
            resp.raise_for_status()
            request_id = resp.json()["request_id"]
            request_bytes = len(body.encode())
            response_bytes = len(resp.content)

            timing = fetch_server_timing(cfg.project_id, args.endpoint, request_id)
            if timing is None:
                print(f"n={n:>4}  WARNING: server log for request_id={request_id} never appeared")
                continue

            total = timing["parse_ms"] + timing["predict_ms"] + timing["serialize_ms"]
            serialize_share = timing["serialize_ms"] / total if total else 0.0
            results.append({
                "n_rows": n, "request_bytes": request_bytes, "response_bytes": response_bytes,
                **timing, "serialize_share": serialize_share,
            })
            print(f"n={n:>4}  req={request_bytes:>6}B  resp={response_bytes:>6}B  "
                  f"parse_ms={timing['parse_ms']:.3f}  predict_ms={timing['predict_ms']:.3f}  "
                  f"serialize_ms={timing['serialize_ms']:.3f}  "
                  f"serialize_share={serialize_share:.1%}")
    finally:
        adapter.revoke_token_creator(endpoint=args.endpoint)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

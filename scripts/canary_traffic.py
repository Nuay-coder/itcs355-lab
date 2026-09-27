"""Lab 3 Task 4, Step 3 — constant-rate labelled traffic against the live canary split.

Sends requests through adapter.invoke() (its own auth caching — see
cloudlayer.gcp.GcpAdapter._fetch_identity_token — keeps this fast enough to sustain a
real rate; the impersonation tier added for this script is what makes 60 req/s possible
at all instead of serialising through a single local proxy). Each row is drawn (with
replacement) from a real held-out labelled pool; the label is kept client-side only —
it is never sent in the request body.

Runs for --duration-seconds (a safety cap; the operator stops it by rollback + a short
evidence-collection tail, not by this script deciding anything about detection). Writes
one JSON line per request to --out (timestamp, request_id, prediction, probability,
label, latency_ms, model_version — probability added beyond the original field list
because log loss needs it and accuracy alone has essentially no signal for this mild a
degradation, see the threshold discussion), and a small canary_start.json recording
exactly when this run began (needed to report detection time).

Does NOT grant/revoke roles/iam.serviceAccountTokenCreator itself — this process runs
for an extended period and gets stopped externally (killed after rollback +
evidence-collection, not by exiting on its own), so a script-internal try/finally would
not reliably run. The caller is responsible for calling grant_token_creator() once
before starting this and revoke_token_creator() once after it's fully stopped.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config

REPO_ROOT = Path(__file__).resolve().parents[1]
_write_lock = threading.Lock()


def send_one(adapter, endpoint: str, row: dict, log_path: Path) -> None:
    t0 = time.perf_counter()
    try:
        result = adapter.invoke(endpoint, row["features"], route="/predict")
        latency_ms = (time.perf_counter() - t0) * 1000
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": result.get("request_id"),
            "prediction": result.get("prediction"),
            "probability": result.get("probability"),
            "label": row["label"],
            "latency_ms": round(latency_ms, 2),
            "model_version": result.get("model_version"),
        }
    except Exception as exc:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "error": str(exc)[:200],
            "label": row["label"],
        }
    with _write_lock:
        with log_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--rate", type=float, default=60.0, help="requests per second")
    ap.add_argument("--duration-seconds", type=int, default=1800)
    ap.add_argument("--rows-file", type=Path, default=REPO_ROOT / "reports" / "raw" / "canary_holdout.json")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "raw" / "canary_log.jsonl")
    ap.add_argument("--start-meta", type=Path, default=REPO_ROOT / "reports" / "raw" / "canary_start.json")
    ap.add_argument("--max-workers", type=int, default=40)
    args = ap.parse_args()

    rows = json.loads(args.rows_file.read_text())
    cfg = config.load()
    adapter = get_adapter(cfg)

    # Synchronous, RETRIED warm-up before the thread pool starts — both to populate
    # invoke()'s auth caches in the main thread (see the p95/p99 spike this fixed,
    # documented on GcpAdapter._cache_lock) and, just as important, to make sure
    # impersonation actually succeeds before relying on it: a single unretried attempt
    # that lands during IAM propagation lag would cache _user_token_unavailable=True
    # permanently for this process, silently downgrading the entire ~20+ minute run to
    # the local-proxy fallback instead of the fast impersonation path.
    print("warming up auth cache (retrying until impersonation actually succeeds)...")
    warmup_deadline = time.monotonic() + 120
    while True:
        # _user_token_unavailable is meant to be a permanent "give up" cache within a
        # single successful attempt, but IAM propagation lag is transient — reset it
        # before each retry so a lag-induced failure doesn't permanently poison every
        # later call in this same process to the slow proxy path.
        adapter._user_token_unavailable = False
        result = adapter.invoke(args.endpoint, random.choice(rows)["features"], route="/predict")
        if getattr(adapter, "_impersonated_token_cache", None) is not None:
            break
        if time.monotonic() > warmup_deadline:
            print("WARNING: impersonation never confirmed within 120s — this run may "
                  "fall back to the local proxy and not sustain the target rate.")
            break
        time.sleep(3)
    print(f"warm-up response: prediction={result.get('prediction')} "
          f"model_version={result.get('model_version')}")

    start_ts = datetime.now(UTC).isoformat()
    args.start_meta.parent.mkdir(parents=True, exist_ok=True)
    args.start_meta.write_text(json.dumps({
        "canary_start_ts": start_ts, "rate_req_s": args.rate,
        "duration_seconds": args.duration_seconds, "endpoint": args.endpoint,
    }, indent=2))
    print(f"canary traffic starting: {start_ts}  rate={args.rate} req/s  "
          f"for up to {args.duration_seconds}s")

    interval = 1.0 / args.rate
    loop_start = time.monotonic()
    deadline = loop_start + args.duration_seconds
    sent = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        next_tick = loop_start
        while time.monotonic() < deadline:
            row = random.choice(rows)
            pool.submit(send_one, adapter, args.endpoint, row, args.out)
            sent += 1
            next_tick += interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            if sent % max(1, int(args.rate) * 30) == 0:
                print(f"  sent {sent} requests so far ({time.monotonic() - loop_start:.0f}s elapsed)")

    print(f"done. sent {sent} requests total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Lab 3 Task 4, Step 3 — blind detection from the aggregate metric alone.

Reads the current state of canary_log.jsonl and computes the CUMULATIVE average log
loss since canary_start_ts, over the aggregate stream (every request, regardless of
model_version — that split happens only after an alert, in a separate step). Compares
it to a baseline measured before the canary began, as a z-score using the standard
error estimated directly from the observed per-request loss values (not a borrowed
constant), and prints an ALERT line if z crosses --threshold.

None of this — the metric (log loss), the comparison method (cumulative average vs a
fixed sliding window), the baseline definition, or the threshold — is specified by the
handout. All are documented decisions made for this lab; see reports/lab3-canary.md.

One-shot by design: run this again whenever you want an updated read, rather than
having it poll internally.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]

# Stable model's own log loss on the held-out test split, computed offline before the
# canary was deployed at all (see scripts/register_canary_model.py's val/test AUC and
# the same held-out set): -mean(y*log(p)+(1-y)*log(1-p)) = 0.2707. This is "the metric
# value when everything is healthy" — the reference point every live cumulative average
# is compared against.
BASELINE_LOG_LOSS = 0.2707
EPS = 1e-7


def per_request_loss(label: int, probability: float) -> float:
    p = min(max(probability, EPS), 1 - EPS)
    return -(label * math.log(p) + (1 - label) * math.log(1 - p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=REPO_ROOT / "reports" / "raw" / "canary_log.jsonl")
    ap.add_argument("--start-meta", type=Path, default=REPO_ROOT / "reports" / "raw" / "canary_start.json")
    ap.add_argument("--threshold", type=float, default=1.0, help="alert when z-score exceeds this")
    ap.add_argument("--baseline", type=float, default=BASELINE_LOG_LOSS)
    args = ap.parse_args()

    start_meta = json.loads(args.start_meta.read_text())
    canary_start_ts = start_meta["canary_start_ts"]

    losses = []
    with args.log.open() as fh:
        for line in fh:
            entry = json.loads(line)
            if "error" in entry or entry.get("probability") is None:
                continue
            if entry["timestamp"] < canary_start_ts:
                continue
            losses.append(per_request_loss(entry["label"], entry["probability"]))

    n = len(losses)
    if n < 2:
        print(f"n={n} — not enough data yet")
        return 0

    mean_loss = sum(losses) / n
    variance = sum((x - mean_loss) ** 2 for x in losses) / (n - 1)
    se = math.sqrt(variance / n)
    z = (mean_loss - args.baseline) / se if se > 0 else 0.0

    now = datetime.now(UTC).isoformat()
    print(f"[{now}] n={n}  cumulative_log_loss={mean_loss:.5f}  baseline={args.baseline:.5f}  "
          f"se={se:.5f}  z={z:+.2f}  threshold={args.threshold}")

    if z > args.threshold:
        print(f"ALERT [{now}] z={z:.2f} > {args.threshold} — aggregate log loss has drifted "
              f"above baseline (n={n} requests since canary start {canary_start_ts})")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

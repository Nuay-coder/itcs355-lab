"""Lab 3 Task 4 — export held-out rows WITH true labels for canary quality monitoring.

Not a handout requirement in itself — the handout says detection must come "from
metrics alone," which requires scoring predictions against ground truth; this script is
our own choice of how to get labelled traffic without ever sending the label in a
request (PredictRequest has extra="forbid", so the label is kept alongside each row
client-side, never in the payload).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, data, seeds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("reports/raw/canary_holdout.json"))
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=seeds.DEFAULT_SEED)
    args = ap.parse_args()

    cfg = config.load(strict=False)
    seed = seeds.set_all(args.seed)
    df = data.load_raw(cfg.raw_path)
    _, _, test_df = data.split(df, seed=seed)

    n = min(args.n, len(test_df))
    sample = test_df.sample(n=n, random_state=seed)
    rows = [
        {"features": {f: float(row[f]) for f in data.FEATURES}, "label": int(row[data.TARGET])}
        for _, row in sample.iterrows()
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows))
    print(f"wrote {len(rows)} labelled held-out rows (test split, seed {seed}) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

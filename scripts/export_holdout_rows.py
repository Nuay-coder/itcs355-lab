"""Lab 3 Task 3 — export real held-out test rows for k6 to sample payloads from.

k6 is a separate process (JavaScript, not Python) — it cannot call src.data itself, so
this writes a JSON array of real held-out rows (never training rows, never one repeated
row) to a local file loadtest/k6.js reads via open() at init time.
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
    ap.add_argument("--out", type=Path, default=Path("loadtest/holdout_rows.json"))
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=seeds.DEFAULT_SEED)
    args = ap.parse_args()

    cfg = config.load(strict=False)
    seed = seeds.set_all(args.seed)
    df = data.load_raw(cfg.raw_path)
    _, _, test_df = data.split(df, seed=seed)

    n = min(args.n, len(test_df))
    rows = test_df[data.FEATURES].sample(n=n, random_state=seed).to_dict(orient="records")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows))
    print(f"wrote {len(rows)} held-out rows (test split, seed {seed}) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

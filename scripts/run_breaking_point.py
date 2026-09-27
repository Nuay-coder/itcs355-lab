"""Lab 3 Task 3, Step 3 — run the breaking-point ramp against a live Cloud Run endpoint.

Same auth dance as run_loadtest.py (grant -> mint -> verify a real request -> run k6 ->
revoke in a finally), reused here rather than duplicated logic, just against
loadtest/k6-breaking-point.js instead of loadtest/k6.js.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloudlayer.factory import get_adapter
from run_loadtest import mint_with_retry, wait_until_authenticated
from src import config

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--stage-seconds", type=int, default=20)
    ap.add_argument("--stage-targets", default="10,20,30,40,60,80,100")
    args = ap.parse_args()

    cfg = config.load()
    adapter = get_adapter(cfg)

    (REPO_ROOT / "reports" / "raw").mkdir(parents=True, exist_ok=True)

    print(f"granting token-creator + run.invoker for {args.endpoint} ...")
    adapter.grant_token_creator(endpoint=args.endpoint)

    try:
        token = mint_with_retry(adapter, args.endpoint)
        url = adapter.service_url(args.endpoint).rstrip("/") + "/predict"
        print("verifying a real authenticated request succeeds ...")
        wait_until_authenticated(url, token)
        print("verified. running ramp ...\n")

        subprocess.run(
            ["k6", "run",
             "-e", f"TARGET={url}",
             "-e", f"TOKEN={token}",
             "-e", f"STAGE_SECONDS={args.stage_seconds}",
             "-e", f"STAGE_TARGETS={args.stage_targets}",
             "loadtest/k6-breaking-point.js"],
            cwd=REPO_ROOT,
        )
    finally:
        print("\nrevoking token-creator + run.invoker ...")
        adapter.revoke_token_creator(endpoint=args.endpoint)
        print("revoked.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

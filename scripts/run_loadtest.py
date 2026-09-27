"""Lab 3 Task 3 — orchestrate the k6 load test end to end.

k6 is a separate process and cannot authenticate itself against an IAM-protected Cloud
Run service, so this script: grants roles/iam.serviceAccountTokenCreator (+
roles/run.invoker on the endpoint), retries minting an audience-scoped token until IAM
propagation catches up, then — separately — retries a real authenticated request until
THAT succeeds too, before ever starting k6. Those are two independent IAM propagations
(impersonation rights, and invoker rights on the service) that do not complete at the
same time: the first real run of this script minted a token successfully in well under
a minute, then still hit a 28% edge-rejection rate (401/403 from Cloud Run itself, never
reaching the container — confirmed by the app's own request log showing zero non-200
responses in that window) because invoker propagation was still catching up. Runs k6 at
each requested concurrency level, and — in a try/finally, so it happens even if k6 fails
or is interrupted — revokes both grants again. One token is minted once before the whole
loop; at ~70s per concurrency level this run finishes in a few minutes, nowhere near the
token's 1-hour lifetime.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from cloudlayer.factory import get_adapter
from src import config

REPO_ROOT = Path(__file__).resolve().parents[1]

# A row well inside every PLAUSIBLE_RANGES bound — used only to confirm the endpoint is
# actually reachable and authenticated, never counted in any reported measurement.
_PROBE_ROW = {
    "temp_c": 78.4, "vibration_mm_s": 3.1, "pressure_kpa": 315.2,
    "hours_since_service": 4200.0, "load_pct": 68.0, "ambient_humidity": 55.0,
}


def mint_with_retry(adapter, endpoint: str, timeout_s: int = 180, interval_s: int = 8) -> str:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            return adapter.mint_loadtest_token(endpoint)
        except RuntimeError as exc:
            last_error = exc
            time.sleep(interval_s)
    raise RuntimeError(f"Token never became mintable within {timeout_s}s (IAM propagation "
                        f"delay?). Last error:\n{last_error}")


def wait_until_authenticated(url: str, token: str, timeout_s: int = 240, interval_s: int = 8,
                              consecutive_required: int = 5) -> None:
    """Confirm a REAL request succeeds end to end, not just that a token was minted —
    minting only proves impersonation propagated, not that run.invoker has too.

    Requires `consecutive_required` successes in a row, not just one. Confirmed live
    that a single success is not enough: the first breaking-point run passed this check
    with one clean 200, then still hit an 87% edge-rejection rate (8728 403s in Cloud
    Run's own platform request log, confirmed via `gcloud logging read` against the
    `run.googleapis.com/requests` log — never reaching the container) once concurrency
    fanned requests out across more of Google's edge fleet than the single verification
    request happened to hit. One success only proves one edge node picked up the IAM
    change; repeated successes over a spread of real requests are the closest available
    proxy for "the whole fleet has it" without a metrics-level check to prove propagation directly.
    """
    deadline = time.monotonic() + timeout_s
    streak = 0
    last = None
    while time.monotonic() < deadline:
        try:
            resp = requests.post(
                url, data=json.dumps(_PROBE_ROW),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                streak += 1
                if streak >= consecutive_required:
                    return
                time.sleep(interval_s)
                continue
            last = f"{resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as exc:
            last = str(exc)
        streak = 0
        time.sleep(interval_s)
    raise RuntimeError(f"Endpoint never accepted {consecutive_required} authenticated "
                        f"requests in a row within {timeout_s}s. Last response:\n{last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="itcs355-predict")
    ap.add_argument("--vus", type=int, nargs="+", default=[1, 10, 50])
    ap.add_argument("--warmup-seconds", type=int, default=10)
    ap.add_argument("--duration-seconds", type=int, default=60)
    args = ap.parse_args()

    cfg = config.load()
    adapter = get_adapter(cfg)

    (REPO_ROOT / "reports" / "raw").mkdir(parents=True, exist_ok=True)

    print(f"granting token-creator + run.invoker for {args.endpoint} ...")
    member = adapter.grant_token_creator(endpoint=args.endpoint)
    print(f"granted to {member}; waiting for IAM propagation and minting a token ...")

    try:
        token = mint_with_retry(adapter, args.endpoint)
        print("token minted.")
        url = adapter.service_url(args.endpoint).rstrip("/") + "/predict"

        print("verifying a real authenticated request succeeds (separate propagation "
              "from minting) ...")
        wait_until_authenticated(url, token)
        print("verified — endpoint is actually reachable with this token.")

        for vus in args.vus:
            print(f"\n=== VUS={vus} ===")
            result = subprocess.run(
                ["k6", "run",
                 "-e", f"TARGET={url}",
                 "-e", f"VUS={vus}",
                 "-e", f"TOKEN={token}",
                 "-e", f"WARMUP_SECONDS={args.warmup_seconds}",
                 "-e", f"DURATION_SECONDS={args.duration_seconds}",
                 "loadtest/k6.js"],
                cwd=REPO_ROOT,
            )
            if result.returncode not in (0, 99):  # 99 = k6 threshold crossed, still valid data
                print(f"k6 exited {result.returncode} at VUS={vus} — see output above")
    finally:
        print("\nrevoking token-creator + run.invoker ...")
        adapter.revoke_token_creator(endpoint=args.endpoint)
        print("revoked.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

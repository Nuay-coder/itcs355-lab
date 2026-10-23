"""Lab 5 — the registration gate.

Compares a candidate against the currently registered production model. Exits non-zero
when the candidate should NOT be registered, which is what makes the pipeline condition
real rather than decorative.

    python scripts/evaluation_gate.py --metrics reports/metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Chosen and defended (Lab 5 Task 1):
#
#   MIN_ABSOLUTE = 0.75 — a floor well below the production range this model has
#   actually scored in (0.8426-0.8545, reports/lab2-comparison.md). It exists only to
#   catch a candidate that is badly broken, not to judge performance — the margin
#   below does that.
#
#   MIN_IMPROVEMENT = 0.006 — set from this repo's own measured seed noise, not a
#   round number. Lab 2's seed-variance check (reports/lab2-comparison.md) found a
#   per-config std of 0.0056-0.0060 across 4 seeds; the gap between the two
#   candidate configs there (0.0009) was ~6.7x smaller than that noise and explicitly
#   called "statistically indistinguishable". Requiring at least one seed-std of
#   improvement is the minimum bar for "this might be real" rather than "this is the
#   seed". The original placeholder here, 0.002, was itself below that noise floor and
#   would have let a noisy candidate through — note for Task 1's write-up.
MIN_ABSOLUTE = 0.75
MIN_IMPROVEMENT = 0.006

# The registered model this gate compares against. Hardcoded like
# scripts/register_best_model.py's LINEAGE and scripts/deploy_service.py's
# DEFAULT_MODEL — scripts/ is operational tooling, not src/, so a resource name here
# is not a portability-audit leak.
MODEL_NAME = "projects/126202218664/locations/asia-southeast1/models/437231793901404160"
PRODUCTION_ALIAS = "production"


def _incumbent_lineage_key(metric: str) -> str:
    """The registry only carries two generic metric slots (metric_val, metric_test),
    not whatever name a particular training run used for its metrics.json field."""
    if metric.startswith("test"):
        return "metric_test"
    if metric.startswith("val"):
        return "metric_val"
    raise ValueError(f"no registry lineage field maps to metric {metric!r}")


def _read_incumbent_from_registry(metric: str) -> float | None:
    from cloudlayer.factory import get_adapter
    from src import config

    adapter = get_adapter(config.load(strict=False))
    stored = adapter.describe_model(MODEL_NAME, PRODUCTION_ALIAS)
    key = _incumbent_lineage_key(metric)
    if key not in stored["lineage"]:
        raise KeyError(f"registry lineage for {MODEL_NAME}@{PRODUCTION_ALIAS} has no {key!r}")
    print(f"incumbent read from registry: {key}={stored['lineage'][key]} "
          f"(version {stored['version_id']})")
    return float(stored["lineage"][key])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--metric", default="test_roc_auc")
    ap.add_argument("--incumbent", type=float, default=None,
                    help="current production score; omit to read from the registry")
    ap.add_argument("--gate-decision-out", type=Path, default=None,
                    help="write 'pass' or 'fail' here — the KFP output the pipeline's "
                         "register step branches on")
    args = ap.parse_args()

    def decide(result: int) -> int:
        if args.gate_decision_out is not None:
            args.gate_decision_out.parent.mkdir(parents=True, exist_ok=True)
            args.gate_decision_out.write_text("pass" if result == 0 else "fail")
        return result

    payload = json.loads(args.metrics.read_text())
    candidate = float(payload[args.metric])
    print(f"candidate {args.metric} = {candidate:.4f}")

    if candidate < MIN_ABSOLUTE:
        print(f"GATE FAIL  below absolute floor {MIN_ABSOLUTE}")
        return decide(1)

    incumbent = args.incumbent
    if incumbent is None:
        try:
            incumbent = _read_incumbent_from_registry(args.metric)
        except Exception as exc:
            print(f"no incumbent in registry ({type(exc).__name__}: {exc}) — "
                  "treating as first registration")
            print("GATE PASS")
            return decide(0)

    delta = candidate - incumbent
    print(f"incumbent {incumbent:.4f}  delta {delta:+.4f}  required {MIN_IMPROVEMENT:+.4f}")
    if delta < MIN_IMPROVEMENT:
        print("GATE FAIL  improvement within noise; not registering")
        return decide(1)

    print("GATE PASS")
    return decide(0)


if __name__ == "__main__":
    raise SystemExit(main())

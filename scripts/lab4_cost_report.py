"""Lab 4 — cost report, verified against the real GCP Billing Console.

Not scripts/cost_report.py -- that script is Lab 5 scaffolding with its own
estimate-vs-actual framing and TODO markers for a later lab. This one reports
exactly what Billing -> Reports showed for Lab 4's resources, grouped by the
`lab` resource label -- the finest grouping GCP's Reports UI offers without a
BigQuery billing export, which this account does not have configured.

    python scripts/lab4_cost_report.py \
        --window-start 2026-10-01 --window-end 2026-10-11 \
        --lab-usage-cost 0.13 --lab-other-savings 0.13

Every number here was read by hand from the Billing Console (Reports, grouped by
label, scoped to this project) -- there is no API to automate this with:
`gcloud billing` has no cost-query subcommand (only `accounts`/`budgets`/
`projects`), and no BigQuery export exists to query instead.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=int, default=4)
    ap.add_argument("--window-start", required=True, help="YYYY-MM-DD, UTC")
    ap.add_argument("--window-end", required=True, help="YYYY-MM-DD, UTC")
    ap.add_argument("--lab-usage-cost", type=float, required=True,
                     help="Gross usage cost (THB) for lab:<N>, before savings")
    ap.add_argument("--lab-other-savings", type=float, required=True,
                     help="Other-savings THB subtracted for lab:<N> (positive number)")
    ap.add_argument("--unlabelled-usage-cost", type=float, default=0.0)
    ap.add_argument("--gross-service", default="Cloud Run",
                     help="Which service accounted for the lab's gross usage cost")
    ap.add_argument("--out", type=Path, default=Path("reports/lab4-cost.md"))
    args = ap.parse_args()

    cfg = config.load(strict=False)
    retrieved = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    net = round(args.lab_usage_cost - args.lab_other_savings, 2)
    grand_total = round(net + args.unlabelled_usage_cost, 2)

    other_services = [
        "Artifact Registry", "Cloud Storage", "Cloud Logging",
        "Cloud Scheduler / Cloud Monitoring (metrics, alerts, dashboard)",
    ]
    service_rows = "\n".join(
        [f"| {args.gross_service} | THB {args.lab_usage_cost:.2f} |"]
        + [f"| {s} | THB 0.00 |" for s in other_services]
    )

    content = f"""# Lab 4 — Cost report

Retrieved: {retrieved} · Usage window: {args.window_start} to {args.window_end} (UTC,
month-to-date — the current invoice period has not closed yet). Scope: project
`{cfg.project_id}`, grouped by resource label `lab`; exact labelled resources are
in `cloudlayer/gcp.py`'s `cfg.tags({args.lab})` / `_service_manifest(lab={args.lab})`.
Source: GCP Billing Console → Reports (Group by: Label, scoped to Projects:
{cfg.project_id}), Usage cost + Other savings columns, current period.

**Limit:** GCP's Reports UI has no "Resource ID" grouping dimension — the finest
grouping available without a BigQuery billing export (not configured on this
account) is by label, not by individual resource. The table below is one row per
label group, not one row per resource.

| Label group | Usage cost (gross) | Other savings | Net |
|---|---|---|---|
| `lab:{args.lab}` | THB {args.lab_usage_cost:.2f} | -THB {args.lab_other_savings:.2f} | THB {net:.2f} |
| [Charges for other usage] (unlabelled) | THB {args.unlabelled_usage_cost:.2f} | THB 0.00 | THB {args.unlabelled_usage_cost:.2f} |

`lab:{args.lab}` reported subtotal (gross, before savings): **THB {args.lab_usage_cost:.2f}**.
All reported rows for this project, this window: **THB {grand_total:.2f}** net.

## By service (within `lab:{args.lab}`)

GCP's Reports UI doesn't separate Cloud Run *services* from Cloud Run *jobs* cost,
or break either down further to the individual resource (staging service vs.
drift job vs. scheduler).

| Service | Usage cost (gross) |
|---|---|
{service_rows}

## Screenshots

- [Group by: Service, project-filtered](../lab-guides/lab4/evidence/billing-console-cost-breakdown.jpg)
- [Group by: Label (`lab`), project-filtered](../lab-guides/lab4/evidence/billing-console-label-grouped.jpg)

## Limits

- No per-resource breakdown — GCP's Console Reports caps out at label/service/SKU
  grouping; per-resource would need a BigQuery billing export, not set up on this
  account, and export is forward-only (wouldn't recover this window retroactively
  even if enabled now).
- This is near-real-time Reports data, not a finalized invoice line — the current
  month's invoice has not closed.
- `gcloud billing` has no cost-query subcommand (`accounts`/`budgets`/`projects`
  only) — this data was retrieved via the Console UI, not scriptably.
"""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content)
    print(f"wrote {args.out}")
    print(f"lab:{args.lab} gross={args.lab_usage_cost:.2f} net={net:.2f} THB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

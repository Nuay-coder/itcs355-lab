# Lab 4 — Cost report

Retrieved: 2026-10-11T15:27:08Z · Usage window: 2026-10-01 to 2026-10-11 (UTC,
month-to-date — the current invoice period has not closed yet). Scope: project
`itcs355-6688033`, grouped by resource label `lab`; exact labelled resources are
in `cloudlayer/gcp.py`'s `cfg.tags(4)` / `_service_manifest(lab=4)`.
Source: GCP Billing Console → Reports (Group by: Label, scoped to Projects:
itcs355-6688033), Usage cost + Other savings columns, current period.

**Limit:** GCP's Reports UI has no "Resource ID" grouping dimension — the finest
grouping available without a BigQuery billing export (not configured on this
account) is by label, not by individual resource. The table below is one row per
label group, not one row per resource.

| Label group | Usage cost (gross) | Other savings | Net |
|---|---|---|---|
| `lab:4` | THB 0.13 | -THB 0.13 | THB 0.00 |
| [Charges for other usage] (unlabelled) | THB 0.00 | THB 0.00 | THB 0.00 |

`lab:4` reported subtotal (gross, before savings): **THB 0.13**.
All reported rows for this project, this window: **THB 0.00** net.

## By service (within `lab:4`)

GCP's Reports UI doesn't separate Cloud Run *services* from Cloud Run *jobs* cost,
or break either down further to the individual resource (staging service vs.
drift job vs. scheduler).

| Service | Usage cost (gross) |
|---|---|
| Cloud Run | THB 0.13 |
| Artifact Registry | THB 0.00 |
| Cloud Storage | THB 0.00 |
| Cloud Logging | THB 0.00 |
| Cloud Scheduler / Cloud Monitoring (metrics, alerts, dashboard) | THB 0.00 |

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

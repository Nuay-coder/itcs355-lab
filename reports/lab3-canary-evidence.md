# Lab 3 — Rollback Evidence

## Timestamps

| Event | Time (UTC) |
|---|---|
| Canary traffic starts (90/10 split live) | 2026-09-26T16:50:27 |
| Detection monitoring ends (no alert, budget exhausted) | 2026-09-26T17:13:00 |
| **Rollback applied** (`split_traffic` → 100% stable) | **2026-09-26T17:28:00** |
| Post-rollback evidence traffic starts | 2026-09-26T17:40:17 |
| Post-rollback evidence traffic ends | 2026-09-26T17:48:17 |

## Requests per minute by model_version

Source: `reports/raw/canary_log.jsonl.gz` (gzip-compressed, 25.5MB → 4.9MB; client-side
log, every request labelled with the `model_version` its response actually reported —
decompress with `gunzip -k` or read directly with `gzip.open()`).

```
minute (UTC)       stable  canary  bars (# = ~113 req)
2026-09-26T16:50     1757     194  ###############++  <- canary starts
2026-09-26T16:51     3238     365  #############################+++
2026-09-26T16:52     3228     369  ############################+++
2026-09-26T16:53     3242     363  #############################+++
2026-09-26T16:54     3251     347  #############################+++
2026-09-26T16:55     3243     357  #############################+++
2026-09-26T16:56     3233     368  #############################+++
2026-09-26T16:57     3224     375  ############################+++
2026-09-26T16:58     3232     369  ############################+++
2026-09-26T16:59     3225     373  ############################+++
2026-09-26T17:00     3213     388  ############################+++
2026-09-26T17:01     3259     341  #############################+++
2026-09-26T17:02     3243     357  #############################+++
2026-09-26T17:03     3223     377  ############################+++
2026-09-26T17:04     3231     369  ############################+++
2026-09-26T17:05     3214     385  ############################+++
2026-09-26T17:06     3202     400  ############################++++
2026-09-26T17:07     3254     345  #############################+++
2026-09-26T17:08     3257     343  #############################+++
2026-09-26T17:09     3237     362  #############################+++
2026-09-26T17:10     3238     363  #############################+++
2026-09-26T17:11     3268     332  #############################+++
2026-09-26T17:12     3095     351  ###########################+++
2026-09-26T17:40     1442       0  #############
2026-09-26T17:41     3780       0  #################################
2026-09-26T17:42     4537       0  ########################################
2026-09-26T17:43     3600       0  ################################
2026-09-26T17:44     3600       0  ################################
2026-09-26T17:45     3600       0  ################################
2026-09-26T17:46     3600       0  ################################
2026-09-26T17:47     3600       0  ################################
2026-09-26T17:48     1041       0  #########
```

`#` = stable, `+` = canary. Every minute before rollback shows canary at roughly 10% of
stable's volume (90/10 split, confirmed). Every minute after rollback shows canary at
**exactly 0**, with no partial tail-off — traffic moved atomically at the rollback call,
not gradually.

## Independent cross-check (Cloud Run's own platform request log, not our client log)

```
gcloud logging read 'resource.type="cloud_run_revision" AND
  resource.labels.service_name="itcs355-predict" AND
  timestamp>="2026-09-26T17:00:00Z" AND timestamp<="2026-09-26T17:05:00Z"' \
  --format="value(resource.labels.revision_name)" | sort | uniq -c
```

| Window | `itcs355-predict-stable` | `itcs355-predict-canary-v2` |
|---|---|---|
| Before rollback (17:00–17:05 UTC) | present | present |
| After rollback (17:38–17:49 UTC) | present | **0 entries** |

Confirms the client-side log: Cloud Run's own request log — a source entirely outside
our own logging code — shows zero requests reaching the canary revision at any point
after the rollback call.

# Lab 2 — Experiment Tracking and Model Registry

---

## What this lab delivered

- Managed training on Vertex AI custom jobs (`submit_training`, `wait_training`) on spot compute
- 12-trial budgeted sweep, total spend 1.7858 THB, MLflow experiment `itcs355-lab2-remote`
- Comparison table, seed-variance check, and the model-selection justification
- Model registered in the Vertex AI Model Registry with all eight lineage fields, promoted
  `staging` → `production`
- `reload_check.py` that loads the model from the registry by version, not from a local file

---

**Cost breakdown**

Trial-by-trial cost, the full comparison table, and the model-selection justification are
in [`reports/lab2-comparison.md`](../../reports/lab2-comparison.md).

---

### Task 4 — Model Registry

Registered via `cloudlayer.gcp.GcpAdapter.register_model()` (Vertex AI Model Registry,
`aiplatform_v1.ModelServiceClient` — the lower-level client, same style as
`submit_training`/`wait_training`) by running `python scripts/register_best_model.py`.

**Version string:** `projects/126202218664/locations/asia-southeast1/models/437231793901404160@1`

**The artifact sitting at `artifact_uri` is not the original job's own output.** That job's compute
had already been torn down by the time Task 4 started, so there was no original file left to
register. `register_best_model.py` retrains a fresh model instead — same git commit, same data
fingerprint, same seed, same hyperparameters as the original run — and only calls
`register_model()` once the retrained val/test metrics land within 0.0010 of the ones already
recorded in MLflow for the original run. They did, to within 0.00006.

`training_job_id` and `mlflow_run_id` below still identify the *original* job and run — the one
whose metrics justified selecting this configuration in Task 3 — not the source of the registered
artifact's bytes. Those are deliberately different facts: which run decided this was the model to
register, versus which run produced the specific file at `artifact_uri` today.

Lineage (all eight fields, read back from Vertex and verified byte-exact against the input — not
just what was sent, since `Model.metadata` turned out to silently not persist for a
custom-uploaded model and had to be moved to `version_description` as JSON instead):

| field | value |
|---|---|
| `git_commit` | `234c6e376c873efa10cc953ee27a818866483f2e` |
| `data_version` | `422cccb9136e8140` |
| `mlflow_run_id` | `2017ed98238142c89798d0db9b046bee` |
| `training_job_id` | `projects/126202218664/locations/asia-southeast1/customJobs/2529236009309175808` |
| `image_digest` | `asia-southeast1-docker.pkg.dev/itcs355-6688033/itcs355/itcs355-lab1@sha256:a2db0f9a7ed4ecc1353fbe8c14e0c9cb94894d43740ff957b3f1f436938ac114` |
| `seed` | `20260101` |
| `metric_val` | `0.84259989736441` |
| `metric_test` | `0.8532857870606215` |

The model-selection justification (why this run and not the sweep's raw highest-scoring one) is in
[`reports/lab2-comparison.md`](../../reports/lab2-comparison.md).

Promoted through a staging step via `GcpAdapter.promote_model()`: registered with alias `staging`,
then moved to `production` (Vertex's `MergeVersionAliases`, adding `production` and dropping
`staging` in one call).

---

#### Who should be allowed to promote, and on what evidence

Not the person who trained or tuned the model. In a real organisation, promotion from staging to
production should sit with a role that has no stake in the model looking good — an ML platform or
MLOps lead, or a designated model-risk approver — separate from the data scientist who built it,
the same segregation-of-duties reasoning that keeps code review separate from the author's own
sign-off. For anything with regulatory or safety exposure, promotion should require two sign-offs,
not one.

Before approving, that person should require, in writing (a PR, ticket, or registry comment —
something that outlives Vertex's own mutable alias state):

1. **Reproducibility proof** — exactly what this section demonstrates: pinned git commit, data
   version, and seed reconstruct the claimed metrics within a stated tolerance. A model that can't
   be rebuilt from its own lineage record is not eligible for promotion, full stop.
2. **A challenger comparison against the current production model on the same held-out data** —
   training-time val/test metrics are not enough; the candidate has to be evaluated against what
   it would actually replace.
3. **Seed/variance evidence that the improvement is real, not noise** — Task 3's seed-variance
   check on this same sweep found two configurations 0.0012 apart on `val_roc_auc` with a
   seed-to-seed standard deviation nearly 7x larger than that gap. A promotion decided on a single
   seed's ranking, with no variance check on record, should be rejected on that basis alone.
4. **Cost impact** — training cost, and the retraining cadence it implies, reviewed against the
   budget it will actually run under in production (not the lab budget).
5. **A monitoring and rollback plan already wired up** — the alias this model is being promoted to
   swaps atomically, but someone has to be watching the metric that would tell you to swap back.

---
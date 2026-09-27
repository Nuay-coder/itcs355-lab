"""Lab 3 Task 4 Step 1 — register the deliberately-degraded canary model.

Same reproduce-and-verify discipline as register_best_model.py: reconstructs the exact
model from its recorded lineage (git commit, data fingerprint, seed, hyperparameters)
and refuses to register unless the reconstructed metrics match the MLflow run this was
trained through (python -m src.train --experiment itcs355-lab3-canary
--run-name canary-maxdepth2-minleaf30) within tolerance. Registers as a NEW VERSION
under the same Vertex model resource as production — Vertex's version_aliases default
("staging") is fine; deploy() in Task 2 addresses versions by "name@version_id" or
alias, and the canary revision will use the concrete version_id, not an alias, so it
never gets pulled along if "production" is repointed later.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from cloudlayer.factory import get_adapter
from src import config, data, seeds
from src.train import git_commit

PARAMS = {"n_estimators": 100, "max_depth": 2, "min_samples_leaf": 30}

LINEAGE = {
    "git_commit": "ccae9812db02c50a7cece26f3022a5d42e4116fd",
    "data_version": "422cccb9136e8140",
    "mlflow_run_id": "7af24efc1db449d8b7f52c3e20d92e06",
    "training_job_id": "local",
    "image_digest": "n/a",
    "seed": "20260101",
    "metric_val": "0.8362272995350761",
    "metric_test": "0.8480691493936527",
}
MODEL_NAME = "itcs355-lab2-sensor-risk"
TOLERANCE = 0.0010  # matches register_best_model.py / reports/lab2-comparison.md


def main() -> int:
    cfg = config.load()

    commit = git_commit()
    if commit != LINEAGE["git_commit"]:
        print(f"REFUSING to register: local git_commit {commit} != recorded {LINEAGE['git_commit']}")
        return 1

    fingerprint = data.data_fingerprint(cfg.raw_path)
    if fingerprint != LINEAGE["data_version"]:
        print(f"REFUSING to register: local data_fingerprint {fingerprint} != recorded {LINEAGE['data_version']}")
        return 1

    seed = seeds.set_all(int(LINEAGE["seed"]))
    df = data.load_raw(cfg.raw_path)
    train_df, val_df, test_df = data.split(df, seed=seed)

    model = RandomForestClassifier(random_state=seed, n_jobs=-1, **PARAMS)
    model.fit(train_df[data.FEATURES], train_df[data.TARGET])

    val_auc = roc_auc_score(val_df[data.TARGET], model.predict_proba(val_df[data.FEATURES])[:, 1])
    test_auc = roc_auc_score(test_df[data.TARGET], model.predict_proba(test_df[data.FEATURES])[:, 1])

    val_diff = abs(val_auc - float(LINEAGE["metric_val"]))
    test_diff = abs(test_auc - float(LINEAGE["metric_test"]))
    if val_diff > TOLERANCE or test_diff > TOLERANCE:
        print("REFUSING to register: reconstructed metrics differ from the MLflow run "
              f"by more than {TOLERANCE}.")
        print(f"  val:  reconstructed={val_auc!r}  recorded={LINEAGE['metric_val']}  diff={val_diff:.6f}")
        print(f"  test: reconstructed={test_auc!r}  recorded={LINEAGE['metric_test']}  diff={test_diff:.6f}")
        return 1
    print(f"Reconstructed model matches the MLflow run within {TOLERANCE} "
          f"(val diff={val_diff:.6f}, test diff={test_diff:.6f}) — safe to register.")

    local_path = Path("reports/_register_canary_model.joblib")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, local_path)

    adapter = get_adapter(cfg)
    artifact_key = f"models/lab3-canary/{LINEAGE['mlflow_run_id']}/model.joblib"
    artifact_uri = adapter.upload(str(local_path), artifact_key)
    model_dir_uri = artifact_uri.rsplit("/", 1)[0]
    print(f"uploaded model artifact: {artifact_uri}")
    local_path.unlink()

    version = adapter.register_model(model_dir_uri, MODEL_NAME, **LINEAGE)
    print(f"registered: {version}")

    model_name, _, version_id = version.rpartition("@")
    stored = adapter.describe_model(model_name, version_id)
    if stored["lineage"] != LINEAGE:
        print("WARNING: lineage read back from Vertex does not match what was sent.")
        return 1

    print(f"\nVerified registry entry (read back from Vertex, version {stored['version_id']}):")
    print(f"  model:            {stored['name']}")
    print(f"  version_aliases:  {stored['version_aliases']}")
    print(f"  artifact_uri:     {stored['artifact_uri']}")
    print(f"\nmodel_ref for deploy(): {model_name}@{version_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

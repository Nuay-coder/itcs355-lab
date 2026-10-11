"""Lab 4 Task 5 — entrypoint for the scheduled drift-detection Cloud Run Job.

Downloads the reference window and the "most recent production" window from
BLOB_URI, then runs `monitoring.drift` against them with `--emit` so the PSI score
reaches Cloud Monitoring.

Stated gap, not a silent shortcut: this project has never served real traffic
(reports/lab3-cost.md), and service/app.py has no logging sink that persists recent
request feature values anywhere queryable. The "current" window here is simply
whatever file sits at gs://<bucket>/lab4/current.csv at schedule time -- Task 6's
injection exercise uploads a deliberately shifted version of it. A system with real
traffic would instead populate that file from wherever a logging sink (Cloud Logging
-> BigQuery export, or a direct write from service/app.py's middleware) actually
persists recent feature values; that sink does not exist here.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config

REFERENCE_KEY = "lab4/reference.csv"
CURRENT_KEY = "lab4/current.csv"


def main() -> int:
    cfg = config.load(strict=False)
    adapter = get_adapter(cfg)
    bucket_uri = cfg.blob_uri.rstrip("/")
    repo_root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory() as tmp:
        ref_path = Path(tmp) / "reference.csv"
        cur_path = Path(tmp) / "current.csv"
        adapter.download(f"{bucket_uri}/{REFERENCE_KEY}", str(ref_path))
        adapter.download(f"{bucket_uri}/{CURRENT_KEY}", str(cur_path))

        result = subprocess.run(
            [sys.executable, "-m", "monitoring.drift",
             "--reference", str(ref_path), "--current", str(cur_path), "--emit"],
            cwd=str(repo_root),
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

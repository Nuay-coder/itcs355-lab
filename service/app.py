"""Lab 3 — inference service.

Provider-neutral by construction: the model arrives through the adapter, and the same
container image deploys to SageMaker, Azure ML, or Vertex AI. Route paths differ per
platform; that difference belongs in cloudlayer/, never here.

Model loading uses only the Lab 1 seam (`adapter.download`) — MODEL_ARTIFACT_URI is a
direct blob URI resolved once, at deploy time, by the provider-specific deploy() (Task
2). The service itself never calls a provider-only method like describe_model(), so the
exact same image keeps working if the adapter behind it changes.

Run locally:  uvicorn service.app:app --port 8080
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response

from service.schemas import BatchRequest, BatchResponse, PredictRequest, PredictResponse
from src.data import FEATURES

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("service")


def _log(level: str = "info", **fields: Any) -> None:
    line = json.dumps({"ts": datetime.now(UTC).isoformat(), **fields})
    getattr(log, level)(line)


STATE: dict[str, Any] = {
    "model": None,
    "ready": False,
    "version": os.environ.get("MODEL_VERSION", "unknown"),
}

# A plausible row, well inside every PLAUSIBLE_RANGES bound, used only to prove the
# loaded model can actually score before /ready flips to 200.
WARMUP_ROW = {
    "temp_c": 70.0,
    "vibration_mm_s": 3.0,
    "pressure_kpa": 300.0,
    "hours_since_service": 1000.0,
    "load_pct": 50.0,
    "ambient_humidity": 50.0,
}


def _load_model():
    """Load once, at startup. Never per request.

    Loading per request is the commonest cause of a p99 that looks nothing like p50, and
    it is the first thing to check when your latency distribution has a long tail.
    """
    artifact_uri = os.environ.get("MODEL_ARTIFACT_URI")
    if artifact_uri:
        import tempfile

        import joblib

        from cloudlayer.factory import get_adapter
        from src import config

        # strict=False: the container only ever gets CLOUD_PROVIDER + PROJECT_ID (baked
        # in by deploy()), never the rest of cloud.env — BLOB_URI, IDENTITY_REF, etc. are
        # deploy-time-only concerns the service has no business holding at runtime.
        adapter = get_adapter(config.load(strict=False))
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "model.joblib"
            adapter.download(artifact_uri, str(local_path))
            return joblib.load(local_path)

    # Fallback for local development and tests only. Submitting this is not acceptable:
    # your deployed service must load a registered version via MODEL_ARTIFACT_URI. Loud
    # and grep-able on purpose — if deploy() ever forgets to set MODEL_ARTIFACT_URI, this
    # line is what tells you the container quietly fell back to a local path instead of
    # failing loudly, rather than leaving you to guess why /ready served a stale model.
    import joblib

    path = Path(os.environ.get("MODEL_PATH", "reports/model.joblib"))
    _log(
        level="warning",
        msg="LOCAL_MODEL_FALLBACK: MODEL_ARTIFACT_URI is not set — loading MODEL_PATH "
            "directly, bypassing the registry and the adapter. This must never happen "
            "in a deployed container.",
        model_path=str(path),
    )
    if not path.exists():
        raise RuntimeError(
            "No model available. Set MODEL_ARTIFACT_URI, or MODEL_PATH for local dev."
        )
    return joblib.load(path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        model = _load_model()
        model.predict_proba(pd.DataFrame([WARMUP_ROW])[FEATURES])
        STATE["model"] = model
        STATE["ready"] = True
        _log(msg="model loaded and warmed up", model_version=STATE["version"])
    except Exception as exc:  # readiness stays false; liveness still passes
        STATE["model"] = None
        STATE["ready"] = False
        _log(level="error", msg="model load or warm-up failed", error=str(exc))
    yield
    STATE["model"] = None
    STATE["ready"] = False


app = FastAPI(title="ITCS355 inference", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - started) * 1000

    response.headers["x-request-id"] = request_id
    response.headers["x-model-version"] = str(STATE["version"])

    fields = {
        "request_id": request_id,
        "route": request.url.path,
        "status_code": response.status_code,
        "latency_ms": round(latency_ms, 3),
        "model_version": STATE["version"],
    }
    n_rows = getattr(request.state, "n_rows", None)
    if n_rows is not None:
        fields["n_rows"] = n_rows
    timing = getattr(request.state, "timing", None)
    if timing is not None:
        fields.update(timing)
    _log(**fields)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. The process is up. Never touches the model."""
    return {"status": "alive"}


@app.get("/ready")
def ready() -> Response:
    """Readiness. The model is loaded AND a warm-up prediction has succeeded.

    These two are genuinely different, and confusing them causes a specific production
    failure: traffic routed to a container whose model has not finished loading. All three
    providers distinguish them, and Quiz 3 asks about it.
    """
    if not STATE["ready"]:
        body = json.dumps({"status": "not_ready", "reason": "model not loaded"})
        return Response(content=body, media_type="application/json", status_code=503)
    body = json.dumps({"status": "ready", "model_version": STATE["version"]})
    return Response(content=body, media_type="application/json")


def _ensure_ready() -> None:
    if not STATE["ready"]:
        raise HTTPException(status_code=503, detail="model not ready")


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest, request: Request) -> Response:
    _ensure_ready()
    t0 = time.perf_counter()
    frame = pd.DataFrame([payload.model_dump()])[FEATURES]
    t1 = time.perf_counter()

    probability = float(STATE["model"].predict_proba(frame)[:, 1][0])
    prediction = int(STATE["model"].predict(frame)[0])
    t2 = time.perf_counter()

    body = PredictResponse(
        prediction=prediction,
        probability=probability,
        model_version=str(STATE["version"]),
        request_id=request.state.request_id,
    ).model_dump()
    encoded = json.dumps(body)
    t3 = time.perf_counter()

    request.state.timing = {
        "parse_ms": round((t1 - t0) * 1000, 3),
        "predict_ms": round((t2 - t1) * 1000, 3),
        "serialize_ms": round((t3 - t2) * 1000, 3),
    }
    return Response(content=encoded, media_type="application/json")


@app.post("/predict/batch", response_model=BatchResponse)
def predict_batch(payload: BatchRequest, request: Request) -> Response:
    _ensure_ready()
    t0 = time.perf_counter()
    frame = pd.DataFrame([row.model_dump() for row in payload.rows])[FEATURES]
    t1 = time.perf_counter()

    probabilities = STATE["model"].predict_proba(frame)[:, 1]
    predictions = STATE["model"].predict(frame)
    t2 = time.perf_counter()

    items = [
        {"prediction": int(p), "probability": float(prob)}
        for p, prob in zip(predictions, probabilities)
    ]
    body = BatchResponse(
        predictions=items,
        model_version=str(STATE["version"]),
        request_id=request.state.request_id,
        n=len(items),
    ).model_dump()
    encoded = json.dumps(body)
    t3 = time.perf_counter()

    request.state.n_rows = len(items)
    request.state.timing = {
        "parse_ms": round((t1 - t0) * 1000, 3),
        "predict_ms": round((t2 - t1) * 1000, 3),
        "serialize_ms": round((t3 - t2) * 1000, 3),
    }
    return Response(content=encoded, media_type="application/json")

"""Lab 3/4 — service contract tests.

The model loader is mocked entirely: no data file, no registry, no cloud call. That is
the point — these tests must pass on a laptop with no cloud.env at all.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

VALID = {
    "temp_c": 78.4, "vibration_mm_s": 3.1, "pressure_kpa": 315.2,
    "hours_since_service": 4200.0, "load_pct": 68.0, "ambient_humidity": 55.0,
}


class FakeModel:
    """Deterministic stand-in: probability is a pure function of temp_c, so batch and
    single-call results are exactly comparable without touching a real registry."""

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        pos = (frame["temp_c"].to_numpy() / 200.0).clip(0, 1)
        return np.column_stack([1 - pos, pos])

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(frame)[:, 1] >= 0.5).astype(int)


@pytest.fixture(scope="module")
def client():
    os.environ["MODEL_VERSION"] = "test-1"
    os.environ.pop("MODEL_ARTIFACT_URI", None)
    os.environ.pop("MODEL_PATH", None)

    from service import app as service_app

    original_loader = service_app._load_model
    service_app._load_model = lambda: FakeModel()
    try:
        with TestClient(service_app.app) as c:
            yield c
    finally:
        service_app._load_model = original_loader


def test_health_never_touches_model(client):
    """Liveness passes even with no model loaded at all."""
    from service.app import STATE

    original = STATE["model"]
    STATE["model"] = None
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["status"] == "alive"
    finally:
        STATE["model"] = original


def test_ready_after_warmup(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "model_version": "test-1"}


def test_ready_returns_503_before_warmup(client):
    from service.app import STATE

    original = STATE["ready"]
    STATE["ready"] = False
    try:
        r = client.get("/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not_ready"
    finally:
        STATE["ready"] = original


def test_predict_returns_503_not_500_when_not_ready(client):
    from service.app import STATE

    original = STATE["ready"]
    STATE["ready"] = False
    try:
        assert client.post("/predict", json=VALID).status_code == 503
        assert client.post("/predict/batch", json={"rows": [VALID]}).status_code == 503
    finally:
        STATE["ready"] = original


def test_predict_returns_prediction_probability_and_version(client):
    r = client.post("/predict", json=VALID)
    assert r.status_code == 200
    body = r.json()
    assert body["prediction"] in (0, 1)
    assert 0.0 <= body["probability"] <= 1.0
    assert body["model_version"] == "test-1"
    assert body["request_id"]
    assert r.headers["x-model-version"] == "test-1"
    assert r.headers["x-request-id"] == body["request_id"]


def test_request_id_is_echoed_from_header(client):
    r = client.post("/predict", json=VALID, headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    assert r.json()["request_id"] == "abc-123"


def test_out_of_range_input_is_rejected(client):
    bad = {**VALID, "load_pct": 250.0}
    r = client.post("/predict", json=bad)
    assert r.status_code == 422
    assert "load_pct" in str(r.json())


def test_unknown_field_is_rejected(client):
    """extra='forbid'. A silently ignored field is how a caller ends up sending a feature
    you never read while believing it matters."""
    r = client.post("/predict", json={**VALID, "surprise": 1})
    assert r.status_code == 422
    assert "surprise" in str(r.json())


def test_missing_field_is_rejected(client):
    incomplete = {k: v for k, v in VALID.items() if k != "temp_c"}
    r = client.post("/predict", json=incomplete)
    assert r.status_code == 422
    assert "temp_c" in str(r.json())


def test_batch_matches_singles(client):
    rows = [VALID, {**VALID, "temp_c": 92.0}]
    batch = client.post("/predict/batch", json={"rows": rows}).json()
    singles = [client.post("/predict", json=r).json() for r in rows]
    assert batch["n"] == len(rows)
    assert batch["model_version"] == "test-1"
    for item, single in zip(batch["predictions"], singles):
        assert item["prediction"] == single["prediction"]
        assert item["probability"] == pytest.approx(single["probability"], abs=1e-9)


def test_batch_size_limit_enforced(client):
    ok = client.post("/predict/batch", json={"rows": [VALID] * 100})
    assert ok.status_code == 200
    assert ok.json()["n"] == 100

    too_many = client.post("/predict/batch", json={"rows": [VALID] * 101})
    assert too_many.status_code == 422

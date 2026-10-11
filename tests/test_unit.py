"""Lab 4 — unit tests.

Fast, no network, no model fit. These test the preprocessing and feature code directly —
the category Quiz 4 contrasts against data contract tests ("the code is wrong" vs. "the
data is wrong") and model behaviour tests ("the model is wrong").
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from monitoring import drift
from src import data


# --- src/data.py -------------------------------------------------------------

def test_data_fingerprint_changes_with_one_byte(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("x,y\n1,2\n3,4\n")
    b.write_text("x,y\n1,2\n3,5\n")  # one byte different
    assert data.data_fingerprint(a) == data.data_fingerprint(a)
    assert data.data_fingerprint(a) != data.data_fingerprint(b)


def _toy_df(n_machines: int, rows_per_machine: int = 3) -> pd.DataFrame:
    rows = []
    reading_id = 0
    for m in range(n_machines):
        for _ in range(rows_per_machine):
            rows.append({
                data.ID: reading_id,
                data.GROUP: m,
                "temp_c": 70.0,
                "vibration_mm_s": 3.0,
                "pressure_kpa": 300.0,
                "hours_since_service": 1000.0,
                "load_pct": 50.0,
                "ambient_humidity": 50.0,
                data.TARGET: reading_id % 2,
            })
            reading_id += 1
    return pd.DataFrame(rows)


def test_split_fractions_are_approximately_respected():
    df = _toy_df(n_machines=100)
    train, val, test = data.split(df, seed=1, val_frac=0.2, test_frac=0.2)
    n_machines_total = df[data.GROUP].nunique()
    for part, expected_frac in ((val, 0.2), (test, 0.2)):
        n_group = part[data.GROUP].nunique()
        assert abs(n_group / n_machines_total - expected_frac) < 0.05


def test_split_raises_on_too_few_machines():
    df = _toy_df(n_machines=2)  # round(2*0.2) == 0 for both val and test -> empty partitions
    with pytest.raises(ValueError):
        data.split(df, seed=1, val_frac=0.2, test_frac=0.2)


# --- monitoring/drift.py -------------------------------------------------------

def test_psi_zero_for_identical_samples():
    rng = np.random.default_rng(0)
    sample = rng.normal(size=500)
    assert drift.psi(sample, sample) == pytest.approx(0.0, abs=1e-9)


def test_psi_grows_with_shift_magnitude():
    rng = np.random.default_rng(0)
    reference = rng.normal(loc=0.0, scale=1.0, size=2000)
    small_shift = reference + 0.5
    large_shift = reference + 6.0
    psi_small = drift.psi(reference, small_shift)
    psi_large = drift.psi(reference, large_shift)
    assert 0.0 < psi_small < psi_large


def test_psi_zero_for_constant_column():
    constant = np.full(100, 42.0)
    assert drift.psi(constant, constant) == 0.0


def test_ks_statistic_zero_for_identical_samples():
    rng = np.random.default_rng(0)
    sample = rng.normal(size=500)
    assert drift.ks_statistic(sample, sample) == pytest.approx(0.0, abs=1e-9)


def test_ks_statistic_one_for_disjoint_ranges():
    low = np.arange(0, 100, dtype=float)
    high = np.arange(1000, 1100, dtype=float)
    assert drift.ks_statistic(low, high) == pytest.approx(1.0)


def test_verdict_thresholds():
    assert drift.verdict_for(0.0) == "stable"
    assert drift.verdict_for(drift.PSI_NO_CHANGE) == "moderate"
    assert drift.verdict_for(drift.PSI_MODERATE) == "significant"

"""
Tests for detection_engine/external_macro_event.py. Hand-built frames, same
independent-expected-output philosophy as test_large_incoming_payment.py.
"""

import pandas as pd

from detection_engine.external_macro_event import (
    MacroDetectorConfig,
    apply_cooldown,
    detect,
)

CFG = MacroDetectorConfig(min_severity=3, cooldown_days=45)

CLIENTS = pd.DataFrame([
    {"client_id": "C1", "sector": "Energy & Utilities", "country": "DE"},
    {"client_id": "C2", "sector": "Construction", "country": "PL"},
    {"client_id": "C3", "sector": "Energy & Utilities", "country": "FR"},
    {"client_id": "C4", "sector": "Health & Social Care", "country": "DE"},
])


def make_event(event_id, event_date, sector="", country="", severity=4, direction="negative"):
    return {
        "event_id": event_id, "event_date": event_date, "event_type": "commodity_energy_shock",
        "source_name": "test", "real_source_type": "test", "affected_country": country,
        "affected_sector": sector, "direction": direction, "severity": severity,
        "headline": "h", "description": "d",
    }


def test_sector_and_country_both_specified_matches_only_intersection():
    events = pd.DataFrame([make_event("E1", "2024-01-01", sector="Energy & Utilities", country="DE")])
    out = detect(events, CLIENTS, CFG, run_id="t")
    assert set(out["client_id"]) == {"C1"}  # not C3 (wrong country), not C2/C4 (wrong sector)


def test_sector_only_wildcards_country():
    events = pd.DataFrame([make_event("E2", "2024-01-01", sector="Energy & Utilities", country="")])
    out = detect(events, CLIENTS, CFG, run_id="t")
    assert set(out["client_id"]) == {"C1", "C3"}


def test_country_only_wildcards_sector():
    events = pd.DataFrame([make_event("E3", "2024-01-01", sector="", country="DE")])
    out = detect(events, CLIENTS, CFG, run_id="t")
    assert set(out["client_id"]) == {"C1", "C4"}


def test_below_min_severity_produces_no_detections():
    events = pd.DataFrame([make_event("E4", "2024-01-01", sector="", country="DE", severity=2)])
    out = detect(events, CLIENTS, CFG, run_id="t")
    assert out.empty


def test_no_matching_clients_produces_no_rows_not_an_error():
    events = pd.DataFrame([make_event("E5", "2024-01-01", sector="Real Estate", country="IE")])
    out = detect(events, CLIENTS, CFG, run_id="t")
    assert out.empty


def test_cooldown_suppresses_repeat_event_type_within_window():
    events = pd.DataFrame([
        make_event("E6", "2024-01-01", sector="", country="DE"),
        make_event("E7", "2024-01-20", sector="", country="DE"),  # 19 days later, within 45-day cooldown
    ])
    out = detect(events, CLIENTS, CFG, run_id="t")
    out = apply_cooldown(out, CFG)
    c1_rows = out[out.client_id == "C1"].sort_values("event_date")
    assert c1_rows["status"].tolist() == ["detected", "suppressed_cooldown"]


def test_idempotent_detection_id_same_event_same_client():
    events = pd.DataFrame([make_event("E8", "2024-01-01", sector="", country="DE")])
    out1 = detect(events, CLIENTS, CFG, run_id="run1")
    out2 = detect(events, CLIENTS, CFG, run_id="run2")
    assert set(out1["detection_id"]) == set(out2["detection_id"])

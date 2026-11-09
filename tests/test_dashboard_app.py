"""In-process Streamlit smoke. Skipped unless the optional dashboard extra is installed."""
from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "src" / "marketpulse" / "dashboard" / "app.py"


def _run(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETPULSE_DASHBOARD_DEMO", "1")
    monkeypatch.delenv("MARKETPULSE_DASHBOARD_ROOT", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run(timeout=60)
    return app


def test_demo_screen_filters_and_does_not_crash(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path)
    assert not app.exception
    warnings = " ".join(str(block.value) for block in app.warning)
    assert "Synthetic demo" in warnings
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Mentions in filter"] == "11"
    assert metrics["Mean item score"] != "n/a"
    app.sidebar.multiselect(key="tickers-Synthetic demo").set_value(["NVDA"]).run(timeout=60)
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Mentions in filter"] == "2"
    page = " ".join(str(block.value) for block in list(app.info) + list(app.caption) + list(app.markdown))
    assert "Insufficient history" in page
    assert "not a news source" in page or "does not apply" in page
    hrefs = []
    for button in getattr(app, "link_button", []):
        hrefs.append(getattr(button, "url", None) or getattr(button, "href", None))
    assert hrefs
    assert all(str(href).startswith("https://") for href in hrefs)


def test_missing_folder_shows_an_error_state(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETPULSE_DASHBOARD_DEMO", "0")
    monkeypatch.setenv("MARKETPULSE_DASHBOARD_ROOT", str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run(timeout=60)
    app.sidebar.radio(key="data_mode").set_value("Saved folder").run(timeout=60)
    assert not app.exception
    text = " ".join(str(block.value) for block in app.error)
    assert "items.json" in text

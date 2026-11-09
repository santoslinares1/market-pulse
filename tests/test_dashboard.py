"""Dashboard artifact parsing. Streamlit itself is not imported here."""
from datetime import date
import json
import subprocess
import sys

from marketpulse.dashboard import MISSING_EXTRA
from marketpulse.dashboard.__main__ import launch_argv
from marketpulse.dashboard.data import (
    demo_root,
    filter_items,
    filter_observations,
    load_workspace,
    local_date,
    mean_score,
    safe_href,
)


def test_data_module_does_not_import_streamlit():
    script = (
        "import sys; import marketpulse.dashboard.data; "
        "assert 'streamlit' not in sys.modules"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_launch_command_is_loopback_only():
    argv = launch_argv("8501", demo=True, root="/tmp/marketpulse-demo")
    assert "127.0.0.1" in argv
    assert "0.0.0.0" not in argv
    assert "--demo" in argv
    assert "--root" in argv
    assert "pip install -e '.[dashboard]'" in MISSING_EXTRA


def test_demo_corpus_is_labeled_synthetic_and_deduped():
    workspace = load_workspace(demo_root())
    assert workspace.synthetic
    assert workspace.duplicate_mentions == 1
    assert workspace.duplicate_observations == 1
    assert len(workspace.items) == 11
    assert {row["provenance"] for row in workspace.observations} == {"synthetic_demo"}
    assert all(0 <= row["price"] <= 1 for row in workspace.observations)
    assert len(workspace.runs) == 2
    assert sum(run["reported_volume"] for run in workspace.runs) == 8
    assert len(workspace.items) != 8
    assert any("broken" in issue.path or "Ignored" in issue.message or "could not read" in issue.message for issue in workspace.issues)


def test_cordoba_date_boundary_and_source_filter_does_not_define_polymarket():
    workspace = load_workspace(demo_root())
    boundary = next(item for item in workspace.items if item["url"].endswith("nvda-boundary"))
    assert local_date(boundary["timestamp"]) == date(2026, 11, 7)
    nov7 = filter_items(workspace.items, None, None, date(2026, 11, 7), date(2026, 11, 7))
    assert {item["url"] for item in nov7} == {
        "https://example.com/demo/jpm-1",
        "https://example.com/demo/nvda-boundary",
    }
    yahoo_only = filter_items(workspace.items, {"AAPL"}, {"yahoo"}, None, None)
    assert yahoo_only and all(item["source"] == "yahoo" for item in yahoo_only)
    # Source selection is not an argument of the Polymarket filter.
    prices = filter_observations(workspace.observations, {"JPM"}, None, None)
    assert len(prices) == 4
    assert len(filter_observations(workspace.observations, {"JPM"}, date(2026, 11, 9), date(2026, 11, 9))) == 2
    assert mean_score([]) is None
    assert safe_href("javascript:alert(1)") is None
    assert safe_href("https://example.com/demo/jpm-1") == "https://example.com/demo/jpm-1"


def test_empty_malformed_and_out_of_range_prices(tmp_path):
    root = tmp_path / "run"
    (root / "output").mkdir(parents=True)
    (root / "data" / "polymarket").mkdir(parents=True)
    (root / "output" / "items.json").write_text("{", encoding="utf-8")
    (root / "output" / "polymarket.json").write_text("[]", encoding="utf-8")
    (root / "data" / "polymarket" / "observations.json").write_text(json.dumps({
        "schema_version": 1,
        "observations": [
            {"observation_id": "bad", "ticker": "JPM", "price": 1.4, "observed_at": "2026-11-09T00:00:00Z"},
            {"observation_id": "ok", "ticker": "JPM", "price": 0.2, "observed_at": "2026-11-09T00:00:00Z", "provenance": "gamma_market"},
            {"ticker": "JPM", "price": "nan", "observed_at": "not-a-date"},
        ],
    }), encoding="utf-8")
    workspace = load_workspace(root)
    assert workspace.items == []
    assert workspace.polymarket is None
    assert [row["observation_id"] for row in workspace.observations] == ["ok"]
    assert any("items.json" in issue.path for issue in workspace.issues)
    assert workspace.kind == "local_artifacts"
    assert mean_score(workspace.items) is None


def test_missing_folder_is_an_issue_not_a_zero_score(tmp_path):
    workspace = load_workspace(tmp_path / "absent")
    assert workspace.items == []
    assert mean_score(workspace.items) is None
    assert workspace.issues

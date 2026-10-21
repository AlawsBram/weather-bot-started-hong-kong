"""The test period must be unreadable before the freeze, and the freeze must detect edits."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2 import backtest as B, data  # noqa: E402
from v2.model import V2  # noqa: E402


def test_test_period_is_sealed(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "FROZEN", tmp_path / "FROZEN_v2.json")
    with pytest.raises(data.SealError):
        data.load("morning", "test")


def test_freeze_detects_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "RESULTS", tmp_path)
    monkeypatch.setattr(data, "FROZEN", tmp_path / "FROZEN_v2.json")
    from dataclasses import asdict
    B.freeze({"morning": asdict(V2(gamma=1.2))}, {})
    B.load_frozen()
    blob = json.loads(data.FROZEN.read_text())
    blob["settings"]["morning"]["gamma"] = 9
    data.FROZEN.write_text(json.dumps(blob))
    with pytest.raises(AssertionError):
        B.load_frozen()

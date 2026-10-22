"""Telegram alerts (same bot/thread as the HK weather bot), deduplicated per key."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HKBOT = Path(os.environ.get("HKBOT_DIR", Path.home() / "hkbot"))
if str(HKBOT) not in sys.path:
    sys.path.insert(0, str(HKBOT))

PREFIX = "🌍 dead-bucket v2 (50 cities, METAR trigger)"


def _state_file() -> Path:
    from . import config
    return config.STATE_DIR / "alerts.json"


def send(text: str) -> bool:
    try:
        from hkbot.alerts import send_telegram
    except ImportError:
        print("[telegram unavailable]\n" + text)
        return False
    ok, detail = send_telegram(f"{PREFIX}\n{text}")
    if not ok:
        print(f"[telegram failed {detail}]\n{text}")
    return ok


def alert(key: str, text: str, dedup_hours: float = 6.0) -> bool:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    st = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    now = time.time()
    if now - st.get(key, 0) < dedup_hours * 3600:
        return False
    st[key] = now
    f.write_text(json.dumps(st), encoding="utf-8")
    with open(f.parent / "alerts.log", "a", encoding="utf-8") as lg:
        lg.write(f"[{datetime.now(timezone.utc):%FT%TZ}] {key}: {text[:300]}\n")
    return send(text)

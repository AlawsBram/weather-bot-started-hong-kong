"""Telegram alerts with storm protection.

Rules (learned the hard way on the other bots):
  * every problem has a KEY; the same key is sent at most once per ALERT_DEDUP_HOURS,
  * the whole bot sends at most ALERT_MAX_PER_HOUR messages an hour ("report" messages,
    i.e. the daily trade / settle summaries, are exempt from the cap but not from dedup),
  * a problem that clears sends one "resolved" line,
  * everything (sent or suppressed) is written to data/state/alerts.log, so nothing is lost.

Secrets: the bot token and chat id are read from an env file on the VPS; they are never stored
in the code or the repo. Set HK_ALERTS_OFF=1 to print instead of sending (tests, dry runs).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config


def _read_env() -> dict:
    env: dict[str, str] = {}
    for f in config.TELEGRAM_ENV_FILES:
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env.update({k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "HK_TG_"))})
    return env


def _state_path() -> Path:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return config.STATE_DIR / "alerts.json"


def _load() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last": {}, "sent": [], "open": {}}


def _save(st: dict) -> None:
    st["sent"] = [t for t in st.get("sent", []) if t > time.time() - 3600]
    _state_path().write_text(json.dumps(st, indent=1))


def _log(line: str) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.STATE_DIR / "alerts.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}] {line}\n")


def send_telegram(text: str) -> tuple[bool, str]:
    if os.environ.get("HK_ALERTS_OFF") == "1":
        print("[telegram suppressed]\n" + text)
        return True, "suppressed (HK_ALERTS_OFF)"
    env = _read_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat = env.get("HK_TG_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    thread = env.get("HK_TG_THREAD_ID")
    if not token or not chat:
        return False, "no TELEGRAM_BOT_TOKEN / chat id configured"
    data = {"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True}
    if thread:
        data["message_thread_id"] = thread
    err = "failed"
    for attempt in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)
            if r.status_code == 429:
                time.sleep(min(30, int(r.json().get("parameters", {}).get("retry_after", 5))))
                continue
            return r.ok, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
            time.sleep(2 * (attempt + 1))
    return False, err


def send_document(path: Path, caption: str = "") -> tuple[bool, str]:
    if os.environ.get("HK_ALERTS_OFF") == "1":
        return True, "suppressed"
    env = _read_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("HK_TG_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False, "no telegram config"
    data = {"chat_id": chat, "caption": caption[:1000]}
    if env.get("HK_TG_THREAD_ID"):
        data["message_thread_id"] = env["HK_TG_THREAD_ID"]
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data,
                              files={"photo": (path.name, f)}, timeout=60)
        return r.ok, f"HTTP {r.status_code}"
    except (requests.RequestException, OSError) as e:
        return False, type(e).__name__


def alert(key: str, text: str, cls: str = "warn") -> bool:
    """Send `text` unless the same key was sent recently or the hourly cap is reached.
    cls: "warn" (a problem), "report" (scheduled summary, exempt from the hourly cap)."""
    st = _load()
    now = time.time()
    last = st["last"].get(key, 0)
    if cls == "warn":
        st.setdefault("open", {})[key] = now        # remembered so a later pass can say "resolved"
    if now - last < config.ALERT_DEDUP_HOURS * 3600:
        _log(f"DEDUP {key}: {text[:160]}")
        _save(st)
        return False
    if cls != "report" and len([t for t in st.get("sent", []) if t > now - 3600]) >= config.ALERT_MAX_PER_HOUR:
        _log(f"CAPPED {key}: {text[:160]}")
        _save(st)
        return False
    prefix = ("🌡️ HK weather bot v1 (forecast models vs market price, paper)" if cls == "report"
              else "⚠️ HK weather bot v1")
    ok, detail = send_telegram(f"{prefix}\n{text}")
    st["last"][key] = now
    if cls != "report":
        st.setdefault("sent", []).append(now)
    _log(f"{'SENT' if ok else 'FAILED ' + detail} {key}: {text[:300]}")
    _save(st)
    return ok


def resolve(key: str, text: str) -> None:
    """Call when a check passes. If that key had an open problem, send one 'resolved' line."""
    st = _load()
    if st.get("open", {}).get(key):
        st["open"].pop(key, None)
        st["last"].pop(key, None)
        _save(st)
        ok, detail = send_telegram(f"✅ HK weather bot\n{text}")
        _log(f"{'RESOLVED' if ok else 'RESOLVE-FAILED ' + detail} {key}: {text[:200]}")


def heartbeat(name: str, **fields) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    fields.update(ts=time.time(), utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    (config.STATE_DIR / f"hb_{name}.json").write_text(json.dumps(fields, indent=1, default=str))


def read_heartbeat(name: str) -> dict:
    try:
        return json.loads((config.STATE_DIR / f"hb_{name}.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

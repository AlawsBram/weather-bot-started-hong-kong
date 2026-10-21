"""Headless Chrome: read the numbers each page SHOWS, and save a screenshot as proof.

Used for two things:
  1. Cross-check: the number on the page must equal the number from the data feed.
     If they ever differ, the feed or the page changed and we alert before it corrupts calibration.
  2. Fallback: if a data feed breaks, the number read off the page is used instead (tagged as such).

This is an ordinary headless Chrome visiting public pages at a low rate (HKO pages at most every
3 hours, Polymarket every 30 minutes). It does not disguise itself or try to get past CAPTCHAs.
If a site starts blocking it, the snapshot records the failure and the alerts tell you.

All parsers work on plain text so they can be unit-tested without a browser.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

# Hosts we never need for reading numbers (keeps Chrome light on the VPS).
_BLOCK_TYPES = {"image", "media", "font"}
_BLOCK_HOSTS = ("googletagmanager.com", "google-analytics.com", "doubleclick.net", "facebook.net",
                "hotjar", "intercom", "sentry.io", "datadoghq", "segment.io")


@dataclass
class PageResult:
    name: str
    url: str
    ok: bool
    values: dict = field(default_factory=dict)
    screenshot: str | None = None
    error: str | None = None


# ----------------------------------------------------------------------------
# Text parsers (tested in tests/test_snapshot.py)
# ----------------------------------------------------------------------------

_FND_ITEM = re.compile(r"^\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+\(\w{3}\)\s+(\d{1,2})\s*\|\s*(\d{1,2})\s*°C")


def parse_fnd_items(items: list[str], year: int, today: date | None = None) -> dict[date, float]:
    """9-day page items like '21 Oct (Wed) 26 | 33°C 55-90% ...' -> {date: max}.
    The page shows no year, so a January date seen in December belongs to the next year."""
    out: dict[date, float] = {}
    for t in items:
        m = _FND_ITEM.match(t)
        if not m or m.group(2) not in MONTHS:
            continue
        mon = MONTHS[m.group(2)]
        y = year + 1 if today is not None and today.month == 12 and mon == 1 else year
        try:
            out[date(y, mon, int(m.group(1)))] = float(m.group(4))
        except ValueError:
            continue
    return out


_EXTRACT_ROW = re.compile(r"^\s*(\d{2})\s+[\d.]+\s+(-?\d+\.\d)\s")


def parse_extract_rows(rows: list[str], year: int, month: int) -> dict[date, float]:
    """Daily Extract table rows like '19 1015.4 31.9 28.5 ...' -> {date: Absolute Daily Max}."""
    out: dict[date, float] = {}
    for r in rows:
        m = _EXTRACT_ROW.match(r)
        if m:
            try:
                out[date(year, month, int(m.group(1)))] = float(m.group(2))
            except ValueError:
                continue
    return out


_CENTS = r"(\d+(?:\.\d+)?)\s*¢|--"


def parse_polymarket_text(text: str, labels: list[str]) -> dict[str, dict]:
    """Event page text -> {label: {"buy_yes": 0.079, "buy_no": 0.93}} (prices as fractions).

    Each bucket block on the page reads: '31°C\\n\\n$3,805 Vol.\\n\\n7%\\n\\nBuy Yes\\n7.9¢\\nBuy No\\n93.0¢'.
    Labels are matched longest-first so '28°C or below' is not mistaken for '28°C'."""
    out: dict[str, dict] = {}
    starts = []
    for lab in sorted(labels, key=len, reverse=True):
        for m in re.finditer(re.escape(lab) + r"\s*\n", text):
            if not any(s <= m.start() < e for s, e, _ in starts):
                starts.append((m.start(), m.end(), lab))
                break
    starts.sort()
    for i, (s, e, lab) in enumerate(starts):
        block = text[e: starts[i + 1][0] if i + 1 < len(starts) else e + 400]
        y = re.search(r"Buy Yes\s*(?:" + _CENTS + ")", block)
        n = re.search(r"Buy No\s*(?:" + _CENTS + ")", block)
        if y or n:
            out[lab] = {
                "buy_yes": float(y.group(1)) / 100 if y and y.group(1) else None,
                "buy_no": float(n.group(1)) / 100 if n and n.group(1) else None,
            }
    return out


# ----------------------------------------------------------------------------
# Browser
# ----------------------------------------------------------------------------

class Browser:
    """One headless Chrome for a whole snapshot round (opened lazily, closed at the end)."""

    def __init__(self):
        self._pw = self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ensure(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright   # imported here: tests don't need it
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
        return self._browser

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001 - closing must never crash a round
            pass
        self._browser = self._pw = None

    def _page(self, block_heavy: bool):
        ctx = self._ensure().new_context(viewport={"width": 1366, "height": 1000}, locale="en-US",
                                         timezone_id=config.HK_TZ)
        page = ctx.new_page()
        page.set_default_timeout(config.PAGE_TIMEOUT_S * 1000)
        if block_heavy:
            def route(r):
                req = r.request
                if req.resource_type in _BLOCK_TYPES or any(h in req.url for h in _BLOCK_HOSTS):
                    return r.abort()
                return r.continue_()
            page.route("**/*", route)
        return ctx, page

    def _shot(self, page, path: Path | None, full: bool, clip_h: int | None = None) -> str | None:
        if path is None:
            return None
        try:
            if clip_h:
                h = min(clip_h, page.evaluate("() => document.body.scrollHeight"))
                page.screenshot(path=str(path), type="jpeg", quality=config.SCREENSHOT_QUALITY,
                                clip={"x": 0, "y": 0, "width": 1366, "height": h}, full_page=True)
            else:
                page.screenshot(path=str(path), type="jpeg", quality=config.SCREENSHOT_QUALITY, full_page=full)
            return path.name
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot failed %s: %s", path, e)
            return None

    # --- HKO 9-day forecast page ---------------------------------------------------------
    def hko_fnd(self, shot: Path | None, today: date) -> PageResult:
        url = config.PAGE_HKO_FND
        try:
            ctx, page = self._page(block_heavy=False)       # keep weather icons in the proof
            try:
                page.goto(url, wait_until="networkidle")
                page.wait_for_selector("#old_fnd .old_fnd_item", state="attached")
                items = page.evaluate(
                    r"() => Array.from(document.querySelectorAll('#old_fnd .old_fnd_item'))"
                    r".map(e => e.innerText.replace(/\s+/g, ' '))")
                vals = parse_fnd_items(items, today.year, today)
                name = self._shot(page, shot, full=True)
            finally:
                ctx.close()
            return PageResult("hko_fnd_page", url, bool(vals), {d.isoformat(): v for d, v in vals.items()},
                              name, None if vals else "no forecast items found on the page")
        except Exception as e:  # noqa: BLE001
            return PageResult("hko_fnd_page", url, False, error=f"{type(e).__name__}: {e}"[:300])

    # --- HKO Daily Extract page (the resolution source) ------------------------------------
    def hko_extract(self, shot: Path | None, year: int, month: int) -> PageResult:
        url = config.PAGE_HKO_EXTRACT.format(y=year, m=month)
        try:
            ctx, page = self._page(block_heavy=False)
            try:
                page.goto(url, wait_until="networkidle")
                page.wait_for_selector("table tr", state="attached")
                rows = page.evaluate(r"() => Array.from(document.querySelectorAll('table tr'))"
                                     r".map(r => r.innerText.replace(/\s+/g, ' ').trim())")
                vals = parse_extract_rows(rows, year, month)
                name = self._shot(page, shot, full=True)
            finally:
                ctx.close()
            return PageResult("hko_extract_page", url, bool(vals), {d.isoformat(): v for d, v in vals.items()},
                              name, None if vals else "no day rows found in the table")
        except Exception as e:  # noqa: BLE001
            return PageResult("hko_extract_page", url, False, error=f"{type(e).__name__}: {e}"[:300])

    # --- Polymarket event page -------------------------------------------------------------
    def polymarket(self, shot: Path | None, url: str, labels: list[str]) -> PageResult:
        try:
            ctx, page = self._page(block_heavy=True)
            try:
                page.goto(url, wait_until="domcontentloaded")
                # A region notice ("Continue in view only mode") can cover the page; click it via JS.
                page.wait_for_timeout(2500)
                page.evaluate(r"""() => { for (const b of document.querySelectorAll('button')) {
                    if (/view only/i.test(b.innerText)) { b.click(); } } }""")
                page.wait_for_function(r"() => /Buy Yes/.test(document.body.innerText)",
                                       timeout=config.PAGE_TIMEOUT_S * 1000)
                page.wait_for_timeout(2000)
                vals = parse_polymarket_text(page.inner_text("body"), labels)
                name = self._shot(page, shot, full=False, clip_h=2600)
            finally:
                ctx.close()
            return PageResult("polymarket_page", url, bool(vals), vals, name,
                              None if vals else "no bucket prices found on the page")
        except Exception as e:  # noqa: BLE001
            return PageResult("polymarket_page", url, False, error=f"{type(e).__name__}: {e}"[:300])

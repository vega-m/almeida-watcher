#!/usr/bin/env python3
"""Friday Rush link watcher for the National Theatre.

Polls the Friday Rush page and pings Telegram the moment ticket links appear
(any href that was not on the page at baseline time).

Window logic (all times UK):
  - If config-rush.json has a non-empty "dates" list, only run on those
    YYYY-MM-DD dates (empty/missing file = every invocation).
  - Before FAST_FROM: baseline + slow poll (60s) — waiting for the drop.
  - FAST_FROM..FAST_UNTIL: fast poll (5s) — the drop window.
  - FAST_UNTIL..END: slow poll (30s) — stragglers.
  - After END: exit. Scheduled runs wake up well before the window and wait
    here, so GitHub scheduler jitter can't make it miss the drop.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (required for notify).
Flags: --dry-run (one poll, print, exit)  --announce (ping on startup)
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.nationaltheatre.org.uk/fridayrush/"
UK = ZoneInfo("Europe/London")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CONFIG_RUSH = Path(__file__).parent / "config-rush.json"
FAST_POLL = 5
SLOW_POLL = 30
WAIT_POLL = 60
DEFAULT_FAST_FROM = "12:25"
DEFAULT_FAST_UNTIL = "13:10"
DEFAULT_END = "14:00"
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
NOTIFIED_FILE = Path(__file__).parent / "rush-notified.json"


def hm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def now_uk():
    n = datetime.now(UK)
    return n.hour * 60 + n.minute, n.strftime("%H:%M:%S"), n.strftime("%Y-%m-%d")


def load_window():
    cfg = {}
    if CONFIG_RUSH.exists():
        try:
            cfg = json.loads(CONFIG_RUSH.read_text())
        except json.JSONDecodeError:
            pass
    return (
        cfg.get("dates") or [],
        hm_to_min(cfg.get("fastFrom", DEFAULT_FAST_FROM)),
        hm_to_min(cfg.get("fastUntil", DEFAULT_FAST_UNTIL)),
        hm_to_min(cfg.get("end", DEFAULT_END)),
    )


def norm(href):
    h = html.unescape(href).strip()
    return h.split("#", 1)[0].rstrip("/") if not h.startswith("#") else ""


def fetch_hrefs():
    req = urllib.request.Request(
        URL + ("?t=%d" % int(time.time() * 1000)),  # bust CDN cache
        headers={"User-Agent": UA, "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "replace")
    return {h for h in (norm(m) for m in HREF_RE.findall(body)) if h}


def notify(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            print("Telegram sent", flush=True)
            return
        except Exception as exc:
            print(f"send failed ({attempt + 1}): {exc}", flush=True)
            time.sleep(2)
    raise RuntimeError("telegram undeliverable")


def load_notified():
    try:
        return set(json.loads(NOTIFIED_FILE.read_text()))
    except Exception:
        return set()


def save_notified(seen):
    NOTIFIED_FILE.write_text(json.dumps(sorted(seen)))


def main():
    dry = "--dry-run" in sys.argv
    dates, fast_from, fast_until, end = load_window()
    mins, hm, today = now_uk()
    if dates and today not in dates:
        print(f"today {today} not in armed dates {dates}; exiting", flush=True)
        return 0

    baseline = None
    for attempt in range(3):
        try:
            baseline = fetch_hrefs()
            break
        except Exception as exc:
            print(f"baseline fetch failed ({attempt + 1}): {exc}", flush=True)
            time.sleep(10)
    if baseline is None:
        print("could not fetch baseline; exiting", flush=True)
        return 2
    print(f"baseline: {len(baseline)} links ({hm} UK)", flush=True)
    if dry:
        for h in sorted(baseline):
            print(" ", h)
        return 0

    notified = load_notified()
    if "--announce" in sys.argv:
        notify(
            f"🚀 Rush watcher armed for {today}. Waiting for the drop window; "
            f"fast-polling (5s) from {fast_from // 60:02d}:{fast_from % 60:02d} UK. "
            "You'll get a ping the second ticket links appear."
        )

    # Baseline must be PRE-release: before fast window, keep refreshing it.
    while mins < fast_from:
        print(f"{hm} waiting for {fast_from // 60:02d}:{fast_from % 60:02d} UK window", flush=True)
        time.sleep(WAIT_POLL)
        mins, hm, _ = now_uk()
        try:
            baseline = fetch_hrefs()  # pre-release page may still change
        except Exception as exc:
            print(f"{hm} fetch error: {exc}", flush=True)

    while True:
        mins, hm, _ = now_uk()
        if mins >= end:
            print("window over, exiting", flush=True)
            return 0
        try:
            hrefs = fetch_hrefs()
        except Exception as exc:
            print(f"{hm} fetch error: {exc}", flush=True)
            time.sleep(FAST_POLL)
            continue
        fresh = hrefs - baseline - notified
        if fresh:
            print(f"{hm} NEW LINKS: {sorted(fresh)}", flush=True)
            lines = [f"🏃 Friday Rush links are UP! ({hm} UK)", ""]
            lines += sorted(fresh)
            lines += ["", "Go go go — https://www.nationaltheatre.org.uk/fridayrush/"]
            try:
                notify("\n".join(lines))
                notified |= fresh
                save_notified(notified)
            except RuntimeError as exc:
                print(f"notify failed, will retry: {exc}", flush=True)
        else:
            print(f"{hm} no new links ({len(hrefs)} total)", flush=True)
        time.sleep(FAST_POLL if mins < fast_until else SLOW_POLL)


if __name__ == "__main__":
    sys.exit(main())

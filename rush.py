#!/usr/bin/env python3
"""Friday Rush link watcher for the National Theatre.

Polls the Friday Rush page and pings Telegram the moment ticket links appear
(any href that was not on the page at startup). Cadence: FAST_POLL until
FAST_UNTIL UK time, then SLOW_POLL until end of window, then exit.

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
FAST_POLL = 5
SLOW_POLL = 30
FAST_UNTIL = 13 * 60 + 10  # 13:10 UK
END = 14 * 60              # 14:00 UK
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
NOTIFIED_FILE = Path(__file__).parent / "rush-notified.json"


def now_uk_minutes():
    n = datetime.now(UK)
    return n.hour * 60 + n.minute, n.strftime("%H:%M:%S")


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


def send(text, token, chat_id):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print("Telegram sent", flush=True)


def notify(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    for attempt in range(3):
        try:
            send(text, token, chat_id)
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
    baseline = fetch_hrefs()
    print(f"baseline: {len(baseline)} links", flush=True)
    if dry:
        for h in sorted(baseline):
            print(" ", h)
        return 0

    notified = load_notified()
    if "--announce" in sys.argv:
        t, hm = now_uk_minutes()
        notify(
            "🚀 Rush watcher armed. Polling the Friday Rush page every "
            f"5s until 13:10 UK, then every 30s until 14:00. You'll get a ping "
            f"the second ticket links appear. (armed {hm} UK)"
        )

    while True:
        mins, hm = now_uk_minutes()
        if mins >= END:
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
            lines += [h for h in sorted(fresh)]
            lines += ["", "Go go go — https://www.nationaltheatre.org.uk/fridayrush/"]
            try:
                notify("\n".join(lines))
                notified |= fresh
                save_notified(notified)
            except RuntimeError as exc:
                print(f"notify failed, will retry: {exc}", flush=True)
        else:
            print(f"{hm} no new links ({len(hrefs)} total)", flush=True)
        time.sleep(FAST_POLL if mins < FAST_UNTIL else SLOW_POLL)


if __name__ == "__main__":
    sys.exit(main())

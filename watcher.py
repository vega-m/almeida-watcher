#!/usr/bin/env python3
"""Poll the Almeida Theatre ticketing API for Golden Boy returns and alert via Telegram.

The ticketing site (TNEW/Tessitura) loads its event calendar from
POST /api/products/productionseasons, which returns per-performance status.
A returned ticket flips a performance from isOnSale=false / "Sold out" to
bookable — that flip is what we alert on.

Exit codes: 0 = available tickets found (and notified), 1 = nothing found,
2 = check failed (network/parse error).
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://ticketing.almeida.co.uk/api/products/productionseasons"
CONFIG_PATH = Path(__file__).parent / "config.json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SOLD_OUT_RE = re.compile(r"sold out|off sale|cancelled", re.IGNORECASE)


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def fetch_available(cfg):
    """Return the list of bookable performances for productions matching the title pattern."""
    body = json.dumps(
        {"startDate": cfg["startDate"], "endDate": cfg["endDate"]}
    ).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    pattern = re.compile(cfg["titlePattern"], re.IGNORECASE)
    found = []
    for production in data.get("productions", []):
        title = re.sub(r"<[^>]+>", "", production.get("productionTitle") or "")
        if not pattern.search(title):
            continue
        for perf in production.get("performances") or []:
            status = (perf.get("performanceStatusMessage") or "").strip()
            on_sale = perf.get("isOnSale") is True
            if on_sale or (status and not SOLD_OUT_RE.search(status)):
                found.append(
                    {
                        "production": title,
                        "when": f"{perf.get('displayDate', '')} {perf.get('displayTime', '')}".strip(),
                        "status": status or ("on sale" if on_sale else "unknown"),
                        "url": perf.get("actionUrl") or "",
                    }
                )
    return found


def notify(cfg, performances):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping notification")
        return
    lines = [cfg.get("messagePrefix", "Tickets available!"), ""]
    for p in performances:
        lines.append(f"🎫 {p['production']} — {p['when']} ({p['status']})")
        if p["url"]:
            lines.append(p["url"])
    text = "\n".join(lines)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            print("Telegram notification sent")
            return
        except urllib.error.URLError as exc:
            print(f"Telegram send failed (attempt {attempt + 1}): {exc}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("could not deliver Telegram notification")


def main():
    cfg = load_config()
    args = set(sys.argv[1:])

    if "--test-notify" in args:
        notify(cfg, [{"production": "Watcher", "when": "test run", "status": "live ✅", "url": ""}])
        return 0

    try:
        found = fetch_available(cfg)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"check failed: {exc}")
        return 2

    if not found:
        print(f"no tickets yet ({time.strftime('%Y-%m-%d %H:%M:%S')})")
        return 1

    for p in found:
        print(f"AVAILABLE: {p['production']} — {p['when']} ({p['status']}) {p['url']}")
    if "--dry-run" in args:
        print("dry run: would notify")
        return 0
    notify(cfg, found)
    return 0


if __name__ == "__main__":
    sys.exit(main())

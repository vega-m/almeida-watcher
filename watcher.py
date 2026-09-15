#!/usr/bin/env python3
"""Poll the Almeida Theatre ticketing API for Golden Boy returns and alert via Telegram.

The ticketing site (TNEW/Tessitura) loads its event calendar from
POST /api/products/productionseasons, which returns per-performance status.
A returned ticket flips a performance from isOnSale=false / "Sold out" to
bookable — that flip is what we alert on.

Each performance is alerted at most once per ALERT_COOLDOWN_H hours (state in
ALERT_STATE_FILE, persisted between shifts via actions/cache), because returns
often churn: someone books a return, their cart expires, it returns again.
The watcher keeps running — it never disables itself. Stop it with:
    gh workflow disable watch.yml -R vega-m/almeida-watcher

Exit codes: 0 = new availability found (and notified) or heartbeat sent,
1 = nothing new, 2 = check failed (network/parse error).
"""
import json
import os
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
ALERT_COOLDOWN_H = 12
STATE_PRUNE_S = 7 * 24 * 3600


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def load_state(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    Path(path).write_text(json.dumps(state))


def prune(state):
    cutoff = time.time() - STATE_PRUNE_S
    return {k: v for k, v in state.items() if v > cutoff}


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
                        "id": perf.get("id"),
                        "production": title,
                        "when": f"{perf.get('displayDate', '')} {perf.get('displayTime', '')}".strip(),
                        "status": status or ("on sale" if on_sale else "unknown"),
                        "url": perf.get("actionUrl") or "",
                    }
                )
    return found


def send_text(cfg, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        # Never treat this as success: the caller must keep retrying so a
        # found ticket is not lost to a silent skip.
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
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


def notify(cfg, performances):
    lines = [cfg.get("messagePrefix", "Tickets available!"), ""]
    for p in performances:
        lines.append(f"🎫 {p['production']} — {p['when']} ({p['status']})")
        if p["url"]:
            lines.append(p["url"])
    lines.append("")
    lines.append("Book fast — returns can be gone in minutes.")
    send_text(cfg, "\n".join(lines))


def state_path():
    return os.environ.get(
        "ALERT_STATE_FILE", str(Path(__file__).parent / "alerted.json")
    )


def main():
    cfg = load_config()
    args = set(sys.argv[1:])
    path = state_path()

    if "--test-notify" in args:
        notify(cfg, [{"production": "Watcher", "when": "test run", "status": "live ✅", "url": "", "id": "test"}])
        return 0

    if "--heartbeat" in args:
        try:
            found = fetch_available(cfg)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"heartbeat: API check failed: {exc}")
            send_text(
                cfg,
                "💓 Daily check-in: watcher is alive, but the Almeida availability "
                "check just failed (site may be briefly down).",
            )
            return 0
        state = load_state(path)
        cutoff = time.time() - ALERT_COOLDOWN_H * 3600
        parts = []
        for p in found:
            tag = " (alerted recently)" if float(state.get(str(p["id"]), 0)) > cutoff else ""
            parts.append(f"{p['when']} — {p['status']}{tag}")
        snapshot = "; ".join(parts) if parts else "all sold out"
        state = os.environ.get("WATCH_STATE", "unknown")
        text = f"💓 Daily check-in — watch workflow: {state}. Current snapshot: {snapshot}."
        if state != "active":
            text += (
                "\n⚠️ Re-arm it: gh workflow enable watch.yml -R vega-m/almeida-watcher"
            )
        print(text)
        send_text(cfg, text)
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
        print(f"available: {p['production']} — {p['when']} ({p['status']}) {p['url']}")

    if "--dry-run" in args:
        print("dry run: would notify")
        return 0

    state = load_state(path)
    cutoff = time.time() - ALERT_COOLDOWN_H * 3600
    fresh = [p for p in found if float(state.get(str(p["id"]), 0)) < cutoff]
    if not fresh:
        print(f"all {len(found)} available performance(s) alerted within the last "
              f"{ALERT_COOLDOWN_H}h; staying quiet")
        return 1

    notify(cfg, fresh)
    now = time.time()
    for p in fresh:
        state[str(p["id"])] = now
    save_state(path, prune(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())

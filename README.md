# almeida-watcher

Watches the Almeida Theatre ticketing site for **Golden Boy** returned tickets
and pushes an instant Telegram alert with the booking link.

Runs entirely on GitHub Actions — nothing needs to run locally, so it keeps
working while your laptop is asleep.

## How it works

The ticketing site (TNEW/Tessitura) loads availability from
`POST https://ticketing.almeida.co.uk/api/products/productionseasons`, which
returns per-performance status. A returned ticket flips a performance from
`isOnSale: false` / "Sold out" to bookable — the watcher alerts on that flip.

- A scheduled job starts a new "shift" every 3 hours; each shift loops for
  ~5h20m inside the job, checking once per minute. Jobs hand over through a
  concurrency group, so there are no gaps and worst-case alert latency is
  ~1 minute.
- **It never stops itself.** Returns often churn (booked, cart expires,
  offered again), so each performance is alerted at most once per 12 hours
  (state kept in `alerted.json` via the Actions cache). To STOP the watcher:
  `gh workflow disable watch.yml -R vega-m/almeida-watcher`
- A **daily heartbeat** (`heartbeat.yml`, 9am UK time) messages the current
  snapshot and warns if the watch workflow is disabled. It also commits a line
  to `STATUS.md`, keeping the repo active so GitHub's 60-day-inactivity rule
  never silently disables the schedules.

## Setup

1. Create the two secrets:

   ```
   gh secret set TELEGRAM_BOT_TOKEN   # paste the bot token from @BotFather
   gh secret set TELEGRAM_CHAT_ID     # your chat id
   ```

2. Test: Actions → **watch** → Run workflow (or run `heartbeat` for the
   daily-style message). You should get a Telegram message.

## Useful commands

```bash
# local dry run (checks live, prints findings, sends nothing)
python3 watcher.py --dry-run

# daily-style check-in without waiting for 9am
gh workflow run heartbeat.yml -R vega-m/almeida-watcher

# stop / re-arm the watcher
gh workflow disable watch.yml -R vega-m/almeida-watcher
gh workflow enable watch.yml -R vega-m/almeida-watcher

# change dates / show / message
$EDITOR config.json
```

## Costs & limits (why this runs forever)

- **Minutes:** the repo is public → GitHub Actions minutes are **unlimited and
  free** on standard runners (the 2,000 min/month free tier applies only to
  private repos). One tiny job per 3h ≈ 24/7 for £0.
- **6h job cap:** handled by the shift handover design.
- **60-day scheduler auto-disable** (repos with no activity): handled by the
  heartbeat's daily `STATUS.md` commit.
- **Load on the theatre's site:** one small POST per minute (~1,440/day) —
  human-refresh scale.

## Notes

- When the ping arrives, move fast — in-demand returns can be gone in
  seconds. The box office (020 7359 4404) can also flag returns for you.
- After you've bought tickets, stop the watcher with the disable command
  above (otherwise it keeps watching until the run ends 31 Oct).

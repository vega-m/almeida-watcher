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

A scheduled job starts a new "shift" every 3 hours; each shift loops for
~5h20m inside the job, checking once per minute. Jobs hand over through a
concurrency group, so there are no gaps and worst-case alert latency is
~1 minute. After it finds tickets and notifies you, it **disables itself**.

## Setup

1. Create the two secrets (values are entered hidden, never echoed):

   ```
   gh secret set TELEGRAM_BOT_TOKEN   # paste the bot token from @BotFather
   gh secret set TELEGRAM_CHAT_ID     # your chat id
   ```

   To find your chat id: message your bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.
   (If your PC bot is polling, stop it first or getUpdates returns a conflict.)

2. Test the notification: Actions → **watch** → Run workflow → tick
   *test_notify*. You should get a Telegram message.

3. Run the workflow once unticked to start watching. Done.

## Useful commands

```bash
# local dry run (checks live, prints findings, sends nothing)
python3 watcher.py --dry-run

# re-enable after it found tickets (or you disabled it)
gh workflow enable watch.yml -R <you>/almeida-watcher

# change dates / show / message
$EDITOR config.json
```

## Notes

- Load is one small POST per minute (~1,440/day) — human-refresh scale.
- Free: public repo Actions minutes are unlimited for this usage.
- When the ping arrives, move fast — in-demand returns can be gone in
  seconds. The box office (020 7359 4404) can also flag returns for you.

# artevelde-task-tracker

A small app that runs entirely on GitHub (Actions + Pages) and reads your assignments from the **Artevelde Hogeschool Canvas** calendar (iCal feed), shows the upcoming ones in a mobile-friendly dark dashboard (Dutch UI), and sends **push notifications to your phone** through [ntfy.sh](https://ntfy.sh).

- Python script run hourly by GitHub Actions, vanilla HTML/CSS/JS frontend on GitHub Pages, installable as a PWA. No server to host or keep awake.
- Tasks grouped by week ("Deze week", "Volgende week", "Later"), colour-coded by urgency:
  - 🔴 due within 2 days (ntfy priority 5)
  - 🟠 due within 7 days (priority 4)
  - 🟢 everything else in the lookahead window (priority 3)
- Each assignment triggers **one** notification every time it moves into a *higher* urgency tier, so you get at most three per assignment and no spam. Sent notifications are tracked on a `state` branch in this repo.
- Works offline: the last fetched tasks are kept in `localStorage`.
- Pull-to-refresh on mobile, plus a refresh button. These reload the data published by the last hourly run; they don't query Canvas live.

## Find your Canvas iCal feed URL

1. Log in to Canvas and open **Agenda** (Calendar) in the left menu.
2. Make sure the courses you care about are ticked in the right-hand course list.
3. At the bottom of that sidebar click **Agenda-feed** (*Calendar Feed*).
4. Copy the URL that ends in `.ics` and put it in `.env` as `ICAL_FEED_URL`.

> Treat this URL like a password: anyone who has it can read your calendar. Never commit it (`.env` is git-ignored).

## Install ntfy on your phone

1. Install the **ntfy** app:
   - Android: [Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
   - iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347)
2. Open it, tap **+**, and subscribe to the topic you set as `NTFY_TOPIC` (keep the default server `ntfy.sh`).
3. Test it: `curl -d "hello" ntfy.sh/<your-topic>`

Topics on ntfy.sh have no password, so pick a long, unguessable name (e.g. `artevelde-tasks-8f3k2x9q`).

## Deploy on GitHub (Actions + Pages)

Everything runs from `.github/workflows/update.yml`: every hour it fetches your feed, sends any new ntfy notifications, and publishes the dashboard to GitHub Pages.

1. **Merge to `main`.** Scheduled workflows only run from the default branch.
2. **Add secrets:** repo **Settings → Secrets and variables → Actions → New repository secret**
   - `ICAL_FEED_URL`: your Canvas feed URL
   - `NTFY_TOPIC`: your ntfy topic
3. **(Optional) add variables** on the same page, under the *Variables* tab: `LOOKAHEAD_DAYS` (default `14`) and `NOTIFY_DEFAULT_TIER` (`false` to skip notifications for green tasks).
4. **Enable Pages:** **Settings → Pages → Build and deployment → Source: GitHub Actions**.
5. **Run it once:** **Actions → Update tasks → Run workflow**. When it's green, your dashboard is at

   `https://<your-github-username>.github.io/schoolbutsmart/`

After that it refreshes itself every hour (GitHub's scheduler can run a few minutes late).

Things to know:

- **The site is public.** Anyone who has the link can read your task list. The feed URL itself stays in secrets and is never published.
- The first run notifies about everything currently in the window, so expect a burst. Set `NOTIFY_DEFAULT_TIER=false` if you only want orange/red alerts.
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. If notifications stop, open the Actions tab and re-enable the workflow.
- If the feed can't be fetched, the run fails (GitHub emails you) and the previous version of the site stays online.

## Local preview

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then edit ICAL_FEED_URL and NTFY_TOPIC
python build_site.py --no-notify # writes ./site (drop --no-notify to send pushes)
python -m http.server -d site    # open http://localhost:8000
```

Run the tests with `python -m unittest discover -s tests -t .`.

### Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `ICAL_FEED_URL` | – (required) | Canvas calendar feed URL |
| `NTFY_TOPIC` | – | ntfy topic; notifications are skipped if empty |
| `LOOKAHEAD_DAYS` | `14` | How many days ahead to show and notify |
| `TIMEZONE` | `Europe/Brussels` | Timezone for due dates |
| `NOTIFY_DEFAULT_TIER` | `true` | Also notify for green (>7 days) tasks |
| `BASE_URL` | set by the workflow | Dashboard URL used as the tap-through link in notifications |

## Add the PWA to your phone's home screen

- **Android (Chrome):** open your GitHub Pages URL, tap the **⋮** menu and choose **Install app** / **Add to Home screen**.
- **iOS (Safari):** open the URL, tap the **Share** button, then **Add to Home Screen**. (It must be Safari.)

The app then opens full-screen, and shows the last fetched tasks when you're offline.

## Project layout

```
.github/workflows/update.yml   Hourly job: build, notify, deploy to Pages
build_site.py                  Fetch feed, send notifications, write site/tasks.json
canvas_client.py               iCal fetch/parse, course extraction, in-memory cache
notifier.py                    Lookahead filter, urgency tiers, ntfy notifications, sent-state tracking
web/                           The PWA: HTML, CSS, JS, service worker, manifest, icons
scripts/make_icons.py          Regenerates the PWA icons
tests/                         Unit tests
```

### How the course name is found

Canvas normally writes the summary as `Assignment name [Course]`. The parser also understands `[Course] Assignment name`, then falls back to a `Course:`/`Cursus:` line in the description, then to the event's location, and finally to "Overig".

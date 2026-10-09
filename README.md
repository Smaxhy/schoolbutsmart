# artevelde-task-tracker

A small self-hosted app that reads your assignments from the **Artevelde Hogeschool Canvas** calendar (iCal feed), shows the upcoming ones in a mobile-friendly dark dashboard (Dutch UI), and sends **push notifications to your phone** through [ntfy.sh](https://ntfy.sh).

- Python/Flask backend, vanilla HTML/CSS/JS frontend, installable as a PWA
- Tasks grouped by week ("Deze week", "Volgende week", "Later"), colour-coded by urgency:
  - 🔴 due within 2 days (ntfy priority 5)
  - 🟠 due within 7 days (priority 4)
  - 🟢 everything else in the lookahead window (priority 3)
- Each assignment triggers **one** notification every time it moves into a *higher* urgency tier, so you get at most three per assignment and no spam. Sent notifications are tracked in `data/notifications.json`.
- Works offline: the last fetched tasks are kept in `localStorage`.
- Pull-to-refresh on mobile, plus a refresh button.

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

## Local development

```bash
git clone https://github.com/smaxhy/schoolbutsmart.git
cd schoolbutsmart

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then edit ICAL_FEED_URL, NTFY_TOPIC, SECRET_KEY
flask --app app run --port 5000
```

Open <http://localhost:5000>. The background scheduler fetches the feed 10 seconds after start and then every hour.

Run the tests with `python -m unittest discover -s tests -t .`.

### Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `ICAL_FEED_URL` | – (required) | Canvas calendar feed URL |
| `NTFY_TOPIC` | – | ntfy topic; notifications are skipped if empty |
| `LOOKAHEAD_DAYS` | `14` | How many days ahead to show and notify |
| `SECRET_KEY` | random per start | Flask secret key |
| `PORT` | `5000` | Port (Docker/`python app.py`) |
| `REFRESH_INTERVAL_MINUTES` | `60` | Feed cache lifetime and scheduler interval |
| `BASE_URL` | `RENDER_EXTERNAL_URL` | Dashboard URL used as the tap-through link in notifications |
| `TIMEZONE` | `Europe/Brussels` | Timezone for due dates |
| `NOTIFY_DEFAULT_TIER` | `true` | Also notify for green (>7 days) tasks. Set to `false` to only get orange/red alerts |
| `STATE_FILE` | `data/notifications.json` | Where sent notifications are tracked |

The first run notifies about everything currently in the window, so expect a burst. Set `NOTIFY_DEFAULT_TIER=false` if you'd rather only hear about tasks that are close.

## Deploy to Render (one click)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/smaxhy/schoolbutsmart)

1. Click the button and sign in to Render.
2. Fill in `ICAL_FEED_URL` and `NTFY_TOPIC` when asked (`SECRET_KEY` is generated for you).
3. Deploy. Render builds the `Dockerfile` using `render.yaml`.
4. Open the `*.onrender.com` URL, and set it as `BASE_URL` if you want notifications to link back to it.

Things to know about the free plan:

- **It sleeps after ~15 minutes without traffic**, and the hourly background check only runs while the service is awake. Either use a paid instance, or ping `https://<your-app>.onrender.com/healthz` every 5–10 minutes with a free monitor such as UptimeRobot or cron-job.org.
- **The disk is ephemeral**: `data/notifications.json` is wiped on each deploy/restart, so you may get repeated notifications afterwards. Attach a persistent disk (paid) mounted at `/app/data` to avoid that.
- The dashboard has no login. Anyone who knows the URL can see your task list, so don't share it.

The same `Dockerfile` also works on Railway and Fly.io. It runs a single gunicorn worker on purpose, because the scheduler runs inside the process.

## Add the PWA to your phone's home screen

- **Android (Chrome):** open your deployed URL, tap the **⋮** menu and choose **Install app** / **Add to Home screen**.
- **iOS (Safari):** open the URL, tap the **Share** button, then **Add to Home Screen**. (It must be Safari.)

The app then opens full-screen, and shows the last fetched tasks when you're offline.

## Project layout

```
app.py             Flask app, API routes, APScheduler job
canvas_client.py   iCal fetch/parse, course extraction, in-memory cache
notifier.py        Lookahead filter, urgency tiers, ntfy notifications, sent-state tracking
templates/         index.html (PWA shell)
static/            CSS, JS, service worker, manifest, icons
scripts/           make_icons.py (regenerates the PWA icons)
tests/             unit tests
```

### How the course name is found

Canvas normally writes the summary as `Assignment name [Course]`. The parser also understands `[Course] Assignment name`, then falls back to a `Course:`/`Cursus:` line in the description, then to the event's location, and finally to "Overig".

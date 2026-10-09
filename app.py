"""Flask app: serves the PWA, the JSON API and the background feed checker."""

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, send_from_directory

from canvas_client import CanvasClient, FeedError
from notifier import HIGH_DAYS, URGENT_DAYS, Notifier, filter_upcoming

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        log.warning("Invalid %s, using %s", name, default)
        return default


def _bool_env(name, default):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(24).hex()

    lookahead_days = _int_env("LOOKAHEAD_DAYS", 14)
    refresh_minutes = max(1, _int_env("REFRESH_INTERVAL_MINUTES", 60))
    dashboard_url = os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")

    client = None
    feed_url = os.environ.get("ICAL_FEED_URL", "").strip()
    if feed_url:
        client = CanvasClient(
            feed_url,
            refresh_interval=refresh_minutes * 60,
            timezone=os.environ.get("TIMEZONE", "Europe/Brussels"),
        )
    else:
        log.error("ICAL_FEED_URL is not set; copy .env.example to .env and fill it in")

    notifier = Notifier(
        topic=os.environ.get("NTFY_TOPIC", "").strip(),
        state_file=os.environ.get("STATE_FILE", "data/notifications.json"),
        dashboard_url=dashboard_url,
        server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
        notify_default_tier=_bool_env("NOTIFY_DEFAULT_TIER", True),
    )

    app.extensions["client"] = client
    app.extensions["notifier"] = notifier

    def check_and_notify():
        """Scheduled job: re-fetch the feed and notify about tasks in a new urgency tier."""
        if client is None:
            return
        try:
            tasks = filter_upcoming(client.get_tasks(force=True), lookahead_days)
        except FeedError as exc:
            log.warning("Scheduled check failed: %s", exc)
            return
        sent = notifier.notify_new_tiers(tasks)
        log.info("Scheduled check done: %d tasks in window, %d notifications sent", len(tasks), sent)

    app.extensions["check_and_notify"] = check_and_notify

    # -- routes --------------------------------------------------------

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/sw.js")
    def service_worker():
        # Served from the root so its scope covers the whole app.
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    def tasks_payload(force=False):
        if client is None:
            return jsonify(error="ICAL_FEED_URL is niet ingesteld op de server."), 503
        try:
            tasks = filter_upcoming(client.get_tasks(force=force), lookahead_days)
        except FeedError as exc:
            log.warning("Feed error: %s", exc)
            return jsonify(error="De Canvas-feed kon niet worden opgehaald."), 502
        return jsonify(
            tasks=tasks,
            count=len(tasks),
            lookahead_days=lookahead_days,
            thresholds={"urgent": URGENT_DAYS, "high": HIGH_DAYS},
            fetched_at=client.fetched_at.isoformat() if client.fetched_at else None,
            stale=client.last_error is not None,
        )

    @app.get("/api/tasks")
    def api_tasks():
        return tasks_payload()

    @app.route("/api/refresh", methods=["POST", "GET"])
    def api_refresh():
        return tasks_payload(force=True)

    @app.after_request
    def no_store_api(resp):
        if resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # -- background scheduler -----------------------------------------

    # Under `flask run --debug` the reloader imports the app twice; only run the job in the child.
    is_reloader_parent = bool(os.environ.get("FLASK_DEBUG")) and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    if client is not None and not is_reloader_parent and not app.testing and _bool_env("ENABLE_SCHEDULER", True):
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            check_and_notify,
            "interval",
            minutes=refresh_minutes,
            next_run_time=datetime.now().astimezone() + timedelta(seconds=10),
            max_instances=1,
            coalesce=True,
            id="check_feed",
        )
        scheduler.start()
        app.extensions["scheduler"] = scheduler

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=_int_env("PORT", 5000))

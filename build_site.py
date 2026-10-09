"""Build the static site: fetch the feed, send ntfy notifications, write site/tasks.json.

Run by the GitHub Actions workflow every hour; can also be run locally:

    python build_site.py            # writes ./site, sends notifications
    python build_site.py --no-notify
    python -m http.server -d site   # preview at http://localhost:8000
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv

from canvas_client import CanvasClient, FeedError
from notifier import HIGH_DAYS, URGENT_DAYS, Notifier, filter_upcoming
from weights import classify, load_weights

log = logging.getLogger("build_site")

PUBLIC_FIELDS = ("title", "course", "due_date", "description", "days_until_due", "urgency", "url",
                 "weight", "impact", "big", "big_reasons")


def _bool_env(name, default):
    value = os.environ.get(name)
    return default if not value else value.strip().lower() in ("1", "true", "yes", "on")


def build(out_dir, web_dir, state_file, notify=True, weights_file=None):
    load_dotenv()
    feed_url = os.environ.get("ICAL_FEED_URL", "").strip()
    if not feed_url:
        raise FeedError("ICAL_FEED_URL is not set")
    lookahead = int(os.environ.get("LOOKAHEAD_DAYS") or 14)

    client = CanvasClient(feed_url, timezone=os.environ.get("TIMEZONE") or "Europe/Brussels")
    tasks = filter_upcoming(client.get_tasks(force=True), lookahead)

    weights = load_weights(weights_file or "")
    for task in tasks:
        task.update(classify(task, weights))

    sent = 0
    if notify:
        notifier = Notifier(
            topic=os.environ.get("NTFY_TOPIC", "").strip(),
            state_file=state_file,
            dashboard_url=os.environ.get("BASE_URL", ""),
            server=os.environ.get("NTFY_SERVER") or "https://ntfy.sh",
            notify_default_tier=_bool_env("NOTIFY_DEFAULT_TIER", True),
        )
        sent = notifier.notify_new_tiers(tasks)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(web_dir, out_dir)
    payload = {
        "tasks": [{k: t[k] for k in PUBLIC_FIELDS} for t in tasks],
        "count": len(tasks),
        "lookahead_days": lookahead,
        "thresholds": {"urgent": URGENT_DAYS, "high": HIGH_DAYS},
        "fetched_at": client.fetched_at.isoformat(),
    }
    with open(os.path.join(out_dir, "tasks.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    log.info("Wrote %d tasks to %s, %d notifications sent", len(tasks), out_dir, sent)
    return payload


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=os.path.join(here, "site"))
    parser.add_argument("--web", default=os.path.join(here, "web"))
    parser.add_argument("--state", default=os.path.join(here, "data", "notifications.json"))
    parser.add_argument("--weights", default=os.path.join(here, "weights.json"))
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        build(args.out, args.web, args.state, notify=not args.no_notify, weights_file=args.weights)
    except FeedError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

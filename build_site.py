"""Build the static site: fetch the feed, send ntfy notifications, write site/tasks.json.

Run by the GitHub Actions workflow every 30 minutes; can also be run locally:

    python build_site.py            # writes ./site, sends notifications
    python build_site.py --no-notify
    python -m http.server -d site   # preview at http://localhost:8000
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv

from canvas_client import CanvasClient, FeedError, fetch_course_colors, is_yellow
from notifier import HIGH_DAYS, URGENT_DAYS, Notifier, filter_upcoming
from weights import classify, load_weights

log = logging.getLogger("build_site")

PUBLIC_FIELDS = ("title", "section", "course", "course_short", "due_date", "description", "days_until_due",
                 "urgency", "url", "weight", "impact", "big", "big_rule", "big_reasons", "kind", "calendar",
                 "calendar_type", "calendar_name", "color", "all_day", "duration_minutes")


def calendar_summary(tasks):
    """Per Canvas calendar (= one colour in Canvas): how many upcoming items, of which kinds."""
    calendars = {}
    for t in tasks:
        key = t["calendar"] or f"onbekend:{t['course']}"
        cal = calendars.setdefault(key, {"id": t["calendar"], "type": t["calendar_type"],
                                         "name": t["calendar_name"], "short": t.get("course_short", ""),
                                         "color": t.get("color"), "yellow": bool(t.get("yellow")),
                                         "count": 0, "kinds": {}, "examples": []})
        cal["count"] += 1
        cal["kinds"][t["kind"]] = cal["kinds"].get(t["kind"], 0) + 1
        if len(cal["examples"]) < 4:
            cal["examples"].append(t["title"])
    return sorted(calendars.values(), key=lambda c: (-c["count"], c["name"]))


def log_report(all_tasks, window_tasks):
    """Readable overview in the Actions log, to see how the feed is structured."""
    log.info("Calendar report: %d upcoming items in the feed, %d in the window", len(all_tasks), len(window_tasks))
    for cal in calendar_summary(all_tasks):
        log.info("  %-22s %-28s %3d items  colour=%s yellow=%s  %s  e.g. %s", cal["id"] or "-", cal["name"][:28],
                 cal["count"], cal["color"] or "?", cal["yellow"], cal["kinds"], " | ".join(cal["examples"]))
    for t in window_tasks:
        log.info("  task: %s | %s | %s | %s | %s min | big=%s %s", t["due_date"][:16], t["calendar"] or "-",
                 t["kind"], t["title"][:60], t["duration_minutes"], t["big"],
                 "; ".join(r["text"] if isinstance(r, dict) else r for r in t["big_reasons"]))


def _bool_env(name, default):
    value = os.environ.get(name)
    return default if not value else value.strip().lower() in ("1", "true", "yes", "on")


def app_version(web_dir):
    """Short hash of the frontend files; the page reloads itself when this changes."""
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(web_dir):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            digest.update(os.path.relpath(path, web_dir).encode())
            with open(path, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()[:12]


def build(out_dir, web_dir, state_file, notify=True, weights_file=None):
    load_dotenv()
    feed_url = os.environ.get("ICAL_FEED_URL", "").strip()
    if not feed_url:
        raise FeedError("ICAL_FEED_URL is not set")
    lookahead = int(os.environ.get("LOOKAHEAD_DAYS") or 14)

    client = CanvasClient(feed_url, timezone=os.environ.get("TIMEZONE") or "Europe/Brussels")
    all_tasks = client.get_tasks(force=True)

    # Optional: your Canvas calendar colours, to find the yellow courses automatically.
    colors = fetch_course_colors(feed_url, os.environ.get("CANVAS_TOKEN", "").strip())
    yellow = {cal for cal, color in colors.items() if is_yellow(color)}
    log.info("Canvas colours: %d known, yellow calendars: %s", len(colors), sorted(yellow) or "none")

    weights = load_weights(weights_file or "")
    for task in all_tasks:
        task["color"] = colors.get(task["calendar"])
        task["yellow"] = task["calendar"] in yellow
        task.update(classify(task, weights, yellow))
    tasks = filter_upcoming(all_tasks, lookahead)

    log_report(all_tasks, tasks)

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
    version = app_version(web_dir)
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, encoding="utf-8") as fh:
        index = fh.read()
    with open(index_path, "w", encoding="utf-8") as fh:
        index = index.replace('<meta name="app-version" content="dev">',
                              f'<meta name="app-version" content="{version}">')
        # Versioned URLs, so a phone can never combine a new page with old cached scripts.
        for asset in ("css/style.css", "js/app.js"):
            index = index.replace(f'"{asset}"', f'"{asset}?v={version}"')
        fh.write(index)
    payload = {
        "tasks": [{k: t[k] for k in PUBLIC_FIELDS} for t in tasks],
        "count": len(tasks),
        "lookahead_days": lookahead,
        "thresholds": {"urgent": URGENT_DAYS, "high": HIGH_DAYS},
        "fetched_at": client.fetched_at.isoformat(),
        "app_version": version,
        "calendars": [{k: c[k] for k in ("id", "type", "name", "short", "color", "yellow", "count", "examples")}
                      for c in calendar_summary(all_tasks) if c["id"]],
        "colors_known": bool(colors),
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

"""Task filtering, urgency tiers and ntfy.sh push notifications."""

import json
import logging
import os
import tempfile
import threading
from base64 import b64encode
from datetime import datetime

import requests

log = logging.getLogger(__name__)

URGENT_DAYS = 2
HIGH_DAYS = 7

URGENCY_RANK = {"default": 0, "high": 1, "urgent": 2}
NTFY_PRIORITY = {"urgent": 5, "high": 4, "default": 3}
NTFY_TAGS = {"urgent": "rotating_light", "high": "warning", "default": "calendar"}
TITLE_PREFIX = {"urgent": "Dringend", "high": "Binnenkort", "default": "Nieuw"}

_WEEKDAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
_MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun",
           "jul", "aug", "sep", "okt", "nov", "dec"]


def urgency_for(days_until_due):
    """Map a number of days until the deadline to an urgency tier."""
    if days_until_due <= URGENT_DAYS:
        return "urgent"
    if days_until_due <= HIGH_DAYS:
        return "high"
    return "default"


def filter_upcoming(tasks, lookahead_days):
    """Keep tasks due from today up to `lookahead_days` days ahead, sorted by due date."""
    upcoming = [t for t in tasks if 0 <= t["days_until_due"] <= lookahead_days]
    return sorted(upcoming, key=lambda t: datetime.fromisoformat(t["due_date"]))


def format_due_nl(due):
    """'do 15 okt 23:59' style Dutch short date."""
    return f"{_WEEKDAYS[due.weekday()]} {due.day} {_MONTHS[due.month - 1]} {due:%H:%M}"


def countdown_nl(days):
    if days <= 0:
        return "vandaag"
    if days == 1:
        return "morgen"
    return f"over {days} dagen"


def _header_value(value):
    """HTTP headers are ASCII-safe only; ntfy accepts RFC 2047 encoded-words for the rest."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + b64encode(value.encode("utf-8")).decode("ascii") + "?="


class Notifier:
    def __init__(self, topic, state_file, dashboard_url="", server="https://ntfy.sh",
                 notify_default_tier=True, session=None):
        self.topic = topic
        self.state_file = state_file
        self.dashboard_url = dashboard_url
        self.server = server.rstrip("/")
        self.notify_default_tier = notify_default_tier
        self.session = session or requests.Session()
        self._lock = threading.Lock()

    # -- state ---------------------------------------------------------

    def _load_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("Could not read %s, starting with empty notification state", self.state_file)
            return {}

    def _save_state(self, state):
        directory = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.state_file)
        except OSError:
            log.exception("Could not write notification state")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # -- sending -------------------------------------------------------

    def send(self, task):
        """POST one task to ntfy.sh. Returns True on success."""
        tier = task["urgency"]
        parts = [f"Deadline {format_due_nl(datetime.fromisoformat(task['due_date']))}"
                 f" ({countdown_nl(task['days_until_due'])})"]
        if task.get("course"):
            parts.insert(0, task["course"])
        headers = {
            "Title": _header_value(f"{TITLE_PREFIX[tier]}: {task['title']}"),
            "Priority": str(NTFY_PRIORITY[tier]),
            "Tags": NTFY_TAGS[tier],
        }
        if self.dashboard_url:
            headers["Click"] = self.dashboard_url
        try:
            resp = self.session.post(
                f"{self.server}/{self.topic}",
                data="\n".join(parts).encode("utf-8"),
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.warning("ntfy notification failed: %s", type(exc).__name__)
            return False

    def notify_new_tiers(self, tasks):
        """Notify once per task each time it enters a higher urgency tier.

        `tasks` should already be filtered to the lookahead window. Returns the
        number of notifications sent.
        """
        if not self.topic:
            log.info("NTFY_TOPIC not set, skipping notifications")
            return 0

        sent = 0
        with self._lock:
            old_state = self._load_state()
            # Forget tasks that left the window, so the file doesn't grow forever.
            state = {t["uid"]: old_state[t["uid"]] for t in tasks if t["uid"] in old_state}
            for task in tasks:
                uid, tier = task["uid"], task["urgency"]
                previous = state.get(uid, {}).get("tier")
                is_higher = previous is None or URGENCY_RANK[tier] > URGENCY_RANK[previous]
                if is_higher and (tier != "default" or self.notify_default_tier):
                    if self.send(task):
                        sent += 1
                        state[uid] = {"tier": tier, "due_date": task["due_date"]}
                else:
                    # Same or lower tier (or a silent default tier): just track it.
                    state[uid] = {"tier": tier, "due_date": task["due_date"]}
            if state != old_state:
                self._save_state(state)
        return sent

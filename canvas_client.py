"""Fetch and parse the Canvas iCal feed into clean task dicts."""

import html
import logging
import re
import threading
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

from notifier import urgency_for

log = logging.getLogger(__name__)

DEFAULT_COURSE = "Overig"
DESCRIPTION_LIMIT = 600

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.+)$")
_SUFFIX_RE = re.compile(r"^(.+?)\s*\[([^\]]+)\]\s*$")
_DESC_COURSE_RE = re.compile(r"^\s*(?:course|cursus|vak|opleidingsonderdeel)\s*:\s*(.+?)\s*$",
                             re.I | re.M)


class FeedError(Exception):
    """Raised when the feed can't be fetched or parsed. Never contains the feed URL."""


def clean_text(value):
    """Strip HTML, decode entities and normalise whitespace."""
    if not value:
        return ""
    text = _BR_RE.sub("\n", str(value))
    text = html.unescape(_TAG_RE.sub("", text))
    lines = (_WS_RE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def split_course(summary, description, location):
    """Return (course, title).

    Canvas puts the course in the summary: "Assignment [Course]" (its default) or
    "[Course] Assignment". Falls back to a "Course: ..." line in the description,
    then to the location.
    """
    summary = summary.strip()
    match = _PREFIX_RE.match(summary)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = _SUFFIX_RE.match(summary)
    if match:
        return match.group(2).strip(), match.group(1).strip()
    match = _DESC_COURSE_RE.search(description)
    if match:
        return match.group(1), summary
    if location:
        return location, summary
    return DEFAULT_COURSE, summary


class CanvasClient:
    def __init__(self, feed_url, refresh_interval=3600, timezone="Europe/Brussels", session=None):
        if not feed_url:
            raise FeedError("ICAL_FEED_URL is not configured")
        # Calendar apps use webcal://, which is just https:// for requests.
        self._feed_url = re.sub(r"^webcal://", "https://", feed_url.strip(), flags=re.I)
        self.refresh_interval = refresh_interval
        self.tz = ZoneInfo(timezone)
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._events = []
        self._fetched_monotonic = None
        self.fetched_at = None
        self.last_error = None

    # -- fetching ------------------------------------------------------

    def _download(self):
        try:
            resp = self._session.get(self._feed_url, timeout=20,
                                     headers={"User-Agent": "artevelde-task-tracker/1.0"})
            resp.raise_for_status()
        except requests.HTTPError as exc:
            # str(exc) would contain the URL, which holds the private feed token.
            raise FeedError(f"Feed returned HTTP {exc.response.status_code}") from None
        except requests.RequestException as exc:
            raise FeedError(f"Could not reach the feed ({type(exc).__name__})") from None
        return resp.content

    def _to_local(self, value):
        """Convert an iCal date/datetime to an aware datetime in the local timezone."""
        if isinstance(value, datetime):
            if value.tzinfo is None:  # floating time
                return value.replace(tzinfo=self.tz)
            return value.astimezone(self.tz)
        # All-day events: treat the deadline as end of that day.
        return datetime.combine(value, dtime(23, 59), tzinfo=self.tz)

    def parse(self, raw):
        """Parse raw iCal bytes into a list of event dicts (no time-dependent fields)."""
        try:
            calendar = Calendar.from_ical(raw)
        except ValueError:
            raise FeedError("Feed is not valid iCal data") from None

        events = []
        for component in calendar.walk("VEVENT"):
            try:
                start = component.decoded("dtstart", None) or component.decoded("dtend", None)
                if start is None:
                    continue
                summary = clean_text(component.get("summary", ""))
                if not summary:
                    continue
                description = clean_text(component.get("description", ""))
                location = clean_text(component.get("location", ""))
                course, title = split_course(summary, description, location)
                events.append({
                    "uid": str(component.get("uid") or f"{summary}|{start}"),
                    "title": title,
                    "course": course,
                    "due": self._to_local(start),
                    "description": description,
                    "url": str(component.get("url") or ""),
                })
            except (ValueError, TypeError, KeyError):
                log.warning("Skipping unparsable calendar event")
        return events

    def refresh(self):
        """Re-fetch the feed. On failure the previous data is kept and the error re-raised."""
        try:
            events = self.parse(self._download())
        except FeedError as exc:
            with self._lock:
                self.last_error = str(exc)
            raise
        with self._lock:
            self._events = events
            self._fetched_monotonic = time.monotonic()
            self.fetched_at = datetime.now(self.tz)
            self.last_error = None
        log.info("Feed refreshed: %d events", len(events))
        return len(events)

    def _is_stale(self):
        return (self._fetched_monotonic is None
                or time.monotonic() - self._fetched_monotonic >= self.refresh_interval)

    # -- output --------------------------------------------------------

    def get_tasks(self, force=False, now=None):
        """Return upcoming tasks (not yet due), sorted by due date.

        Refreshes the in-memory cache when it is older than `refresh_interval`. If a
        refresh fails but older data exists, the old data is served and `last_error` is set.
        """
        if force or self._is_stale():
            try:
                self.refresh()
            except FeedError:
                if self._fetched_monotonic is None:
                    raise
                log.warning("Feed refresh failed, serving cached data: %s", self.last_error)

        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        with self._lock:
            events = list(self._events)

        tasks = []
        for event in sorted(events, key=lambda e: e["due"]):
            if event["due"] < now:
                continue
            days = (event["due"].date() - now.date()).days
            description = event["description"]
            if len(description) > DESCRIPTION_LIMIT:
                description = description[:DESCRIPTION_LIMIT].rstrip() + "…"
            tasks.append({
                "uid": event["uid"],
                "title": event["title"],
                "course": event["course"],
                "due_date": event["due"].isoformat(),
                "description": description,
                "days_until_due": days,
                "urgency": urgency_for(days),
                "url": event["url"],
            })
        return tasks

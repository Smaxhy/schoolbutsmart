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
DESCRIPTION_LIMIT = 4000

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.+)$")
_SUFFIX_RE = re.compile(r"^(.+?)\s*\[([^\]]+)\]\s*$")
# Canvas writes UID "event-<type>-<id>" (assignment, calendar-event, sub-assignment,
# assignment-override) and URL ".../calendar?include_contexts=<context>_<id>&...#<type>_<id>",
# where <context> is the calendar the item belongs to (course, group, user = personal,
# account = school-wide calendar). Canvas colours items per calendar, so this is what
# tells the "yellow" items apart. See canvas-lms app/models/calendar_event.rb (IcalEvent).
_UID_RE = re.compile(r"^event-([a-z-]+?)-(\d+)$")
_CONTEXT_RE = re.compile(r"[?&]include_contexts=([a-z_]+?)_(\d+)")
_URL_HOST_RE = re.compile(r"^(https?://[^/]+)/")
_ANCHOR_ID_RE = re.compile(r"#(?:assignment|sub_assignment)_(\d+)$")
# Canvas appends the section of an assignment override to the title: "Opdracht 3 (AVD1-B@S1)".
_SECTION_RE = re.compile(r"\s*\(([^()]*@[^()]*)\)\s*$")
CALENDAR_TYPE_NAMES = {"user": "Persoonlijke agenda", "account": "Schoolagenda", "group": "Groepsagenda",
                       "appointment_group": "Afspraken"}

_DESC_COURSE_RE = re.compile(r"^\s*(?:course|cursus|vak|opleidingsonderdeel)\s*:\s*(.+?)\s*$",
                             re.I | re.M)


def canvas_meta(uid, url, course):
    """Work out item kind, calendar and a direct link from Canvas' UID and URL."""
    match = _UID_RE.match(uid or "")
    raw_kind = match.group(1) if match else ""
    kind = "event" if raw_kind == "calendar-event" else ("assignment" if "assignment" in raw_kind else "other")

    ctx = _CONTEXT_RE.search(url or "")
    calendar_type = ctx.group(1) if ctx else ""
    calendar = f"{ctx.group(1)}_{ctx.group(2)}" if ctx else ""
    if calendar_type == "course":
        calendar_name = course
    else:
        calendar_name = CALENDAR_TYPE_NAMES.get(calendar_type, course or "Overig")

    # Link straight to the assignment instead of the calendar month view, when we can.
    link = url or ""
    host = _URL_HOST_RE.match(url or "")
    anchor = _ANCHOR_ID_RE.search(url or "")
    if host and anchor and calendar_type == "course":
        link = f"{host.group(1)}/courses/{ctx.group(2)}/assignments/{anchor.group(1)}"
    return {"kind": kind, "calendar": calendar, "calendar_type": calendar_type,
            "calendar_name": calendar_name, "link": link}


def split_section(title):
    """("Opdracht 3 (AVD1-B@S1)") -> ("Opdracht 3", "AVD1-B@S1")."""
    match = _SECTION_RE.search(title)
    if not match:
        return title, ""
    return title[:match.start()].strip() or title, match.group(1).strip()


def short_course(course):
    """'PBAGDM - STORY1 - Semester 1' -> 'STORY1'; anything else is returned unchanged."""
    parts = [p.strip() for p in course.split(" - ") if p.strip()]
    return parts[1] if len(parts) >= 3 else course


def fetch_course_colors(feed_url, token, session=None, timeout=15):
    """Your own Canvas calendar colours: {"course_44401": "#f0c61c", ...}.

    Uses GET /api/v1/users/self/colors (Canvas Users API "Get custom colors") on the same
    Canvas host as the feed. Returns {} on any problem; colours are a nice-to-have.
    """
    host = re.match(r"^(?:https?|webcal)://([^/]+)/", feed_url or "", re.I)
    if not host or not token:
        return {}
    try:
        resp = (session or requests).get(f"https://{host.group(1)}/api/v1/users/self/colors",
                                         headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        resp.raise_for_status()
        colors = resp.json().get("custom_colors", {})
    except (requests.RequestException, ValueError, AttributeError) as exc:
        log.warning("Could not read Canvas colours (%s)", type(exc).__name__)
        return {}
    return {k: v for k, v in colors.items() if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v)}


def is_yellow(hex_color):
    """True for yellow-ish colours (hue ~40-70°, clearly saturated and bright)."""
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError):
        return False
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return 38 <= h * 360 <= 72 and s >= 0.45 and v >= 0.55


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
                title, section = split_section(title)
                uid = str(component.get("uid") or f"{summary}|{start}")
                url = str(component.get("url") or "")
                end = component.decoded("dtend", None)
                events.append({
                    "uid": uid,
                    "title": title,
                    "section": section,
                    "course": course,
                    "due": self._to_local(start),
                    "end": self._to_local(end) if end is not None else None,
                    "description": description,
                    "url": url,
                    **canvas_meta(uid, url, course),
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
                "section": event["section"],
                "course": event["course"],
                "course_short": short_course(event["course"]),
                "due_date": event["due"].isoformat(),
                "description": description,
                "days_until_due": days,
                "urgency": urgency_for(days),
                "url": event["link"],
                "kind": event["kind"],
                "calendar": event["calendar"],
                "calendar_type": event["calendar_type"],
                "calendar_name": event["calendar_name"],
                "all_day": event["end"] is None and event["due"].strftime("%H:%M") == "23:59",
                "duration_minutes": (int((event["end"] - event["due"]).total_seconds() // 60)
                                     if event["end"] is not None else 0),
            })
        return tasks

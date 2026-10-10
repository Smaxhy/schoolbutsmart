import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from canvas_client import CanvasClient, FeedError, split_course
from notifier import Notifier, filter_upcoming, urgency_for
from weights import classify, impact_for, load_weights, percentage_in

TZ = ZoneInfo("Europe/Brussels")
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Canvas//EN
BEGIN:VEVENT
UID:event-assignment-1
DTSTART:20261010T215900Z
DTEND:20261010T215900Z
SUMMARY:Verslag stage [Webontwikkeling 2]
DESCRIPTION:<p>Lever je &amp; verslag in.</p><br>Succes!
URL:https://canvas.example/assignments/1
END:VEVENT
BEGIN:VEVENT
UID:event-2
DTSTART;VALUE=DATE:20261020
SUMMARY:[Databases] Examen
LOCATION:Lokaal 2.14
END:VEVENT
BEGIN:VEVENT
UID:event-3
DTSTART:20261001T100000Z
SUMMARY:Oude opdracht [Databases]
END:VEVENT
BEGIN:VEVENT
UID:event-4
DTSTART:20261105T100000Z
SUMMARY:Verre opdracht
LOCATION:Netwerken
END:VEVENT
END:VCALENDAR
"""


def shifted_ics():
    """ICS with every date moved so that NOW lies at today's date (for code that uses the real clock)."""
    import re
    from datetime import date, timedelta
    shift = date.today() - NOW.date()

    def move(m):
        d = datetime.strptime(m.group(2), "%Y%m%d").date() + shift
        return m.group(1) + d.strftime("%Y%m%d")
    return re.sub(rb"(DT(?:START|END)[^:]*:)(\d{8})", lambda m: move(
        type("M", (), {"group": lambda self, i: m.group(i).decode()})()).encode(), ICS)


def make_client():
    client = CanvasClient("webcal://canvas.example/feed.ics")
    client._download = lambda: ICS
    return client


class ClientTests(unittest.TestCase):
    def test_split_course(self):
        self.assertEqual(split_course("[A] B", "", ""), ("A", "B"))
        self.assertEqual(split_course("B [A]", "", ""), ("A", "B"))
        self.assertEqual(split_course("B", "Cursus: A", "room"), ("A", "B"))
        self.assertEqual(split_course("B", "", "room"), ("room", "B"))
        self.assertEqual(split_course("B", "", "")[0], "Overig")

    def test_parse_and_filter(self):
        tasks = make_client().get_tasks(now=NOW)
        self.assertEqual([t["uid"] for t in tasks], ["event-assignment-1", "event-2", "event-4"])
        first = tasks[0]
        self.assertEqual(first["course"], "Webontwikkeling 2")
        self.assertEqual(first["title"], "Verslag stage")
        self.assertEqual(first["description"], "Lever je & verslag in.\nSucces!")
        self.assertEqual(first["days_until_due"], 1)
        self.assertEqual(first["urgency"], "urgent")
        self.assertEqual(first["due_date"], "2026-10-10T23:59:00+02:00")
        self.assertEqual(tasks[1]["days_until_due"], 11)
        self.assertEqual(tasks[1]["urgency"], "default")
        self.assertEqual(tasks[1]["due_date"], "2026-10-20T23:59:00+02:00")

    def test_lookahead(self):
        tasks = make_client().get_tasks(now=NOW)
        self.assertEqual(len(filter_upcoming(tasks, 14)), 2)
        self.assertEqual(len(filter_upcoming(tasks, 30)), 3)

    def test_cache_and_stale_fallback(self):
        client = make_client()
        calls = []
        client._download = lambda: calls.append(1) or ICS
        client.get_tasks(now=NOW)
        client.get_tasks(now=NOW)
        self.assertEqual(len(calls), 1)

        def boom():
            raise FeedError("down")
        client._download = boom
        self.assertTrue(client.get_tasks(force=True, now=NOW))
        self.assertEqual(client.last_error, "down")

    def test_first_fetch_failure_raises(self):
        client = make_client()
        client._download = mock.Mock(side_effect=FeedError("down"))
        with self.assertRaises(FeedError):
            client.get_tasks()

    def test_urgency_tiers(self):
        self.assertEqual([urgency_for(d) for d in (0, 2, 3, 7, 8)],
                         ["urgent", "urgent", "high", "high", "default"])


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.session = mock.Mock()
        self.notifier = Notifier("topic", os.path.join(self.dir.name, "state.json"),
                                 dashboard_url="https://dash.example", session=self.session)

    def tearDown(self):
        self.dir.cleanup()

    def task(self, uid="u1", days=10, **extra):
        return {"uid": uid, "title": "Verslag é", "course": "Web", "days_until_due": days,
                "urgency": urgency_for(days), "due_date": "2026-10-20T23:59:00+02:00", **extra}

    def test_notifies_once_per_tier(self):
        n = self.notifier
        self.assertEqual(n.notify_new_tiers([self.task(days=10)]), 1)
        self.assertEqual(n.notify_new_tiers([self.task(days=10)]), 0)
        self.assertEqual(n.notify_new_tiers([self.task(days=6)]), 1)
        self.assertEqual(n.notify_new_tiers([self.task(days=5)]), 0)
        self.assertEqual(n.notify_new_tiers([self.task(days=1)]), 1)
        self.assertEqual(n.notify_new_tiers([self.task(days=0)]), 0)
        self.assertEqual(self.session.post.call_count, 3)

    def test_request_shape(self):
        self.notifier.notify_new_tiers([self.task(days=1)])
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://ntfy.sh/topic")
        headers = kwargs["headers"]
        self.assertEqual(headers["Priority"], "5")
        self.assertEqual(headers["Click"], "https://dash.example")
        self.assertTrue(headers["Title"].startswith("=?UTF-8?B?"))  # non-latin-1 safe
        headers["Title"].encode("latin-1")

    def test_failed_send_is_retried_next_run(self):
        import requests
        self.session.post.side_effect = requests.ConnectionError()
        self.assertEqual(self.notifier.notify_new_tiers([self.task(days=1)]), 0)
        self.session.post.side_effect = None
        self.assertEqual(self.notifier.notify_new_tiers([self.task(days=1)]), 1)

    def test_state_pruned_and_no_topic(self):
        self.notifier.notify_new_tiers([self.task("a"), self.task("b")])
        self.notifier.notify_new_tiers([self.task("a")])
        with open(self.notifier.state_file) as fh:
            self.assertEqual(list(json.load(fh)), ["a"])
        silent = Notifier("", self.notifier.state_file, session=self.session)
        self.assertEqual(silent.notify_new_tiers([self.task("z")]), 0)


class WeightTests(unittest.TestCase):
    def config(self, rules, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w.json")
            with open(path, "w") as fh:
                json.dump({"rules": rules, **extra}, fh)
            return load_weights(path)

    def test_matching_and_tiers(self):
        cfg = self.config([
            {"course": "Databases", "title": "examen", "weight": 40},
            {"title": "verslag", "weight": 15},
            {"course": "web", "weight": 5},
            {"weight": 99},  # no title/course: ignored
        ])
        self.assertEqual(len(cfg["rules"]), 3)
        t = lambda title, course: {"title": title, "course": course}
        self.assertEqual(impact_for(t("Examen januari", "Databases"), cfg), (40, "high"))
        self.assertEqual(impact_for(t("Examen januari", "Netwerken"), cfg), (None, None))
        self.assertEqual(impact_for(t("Verslag stage", "X"), cfg), (15, "medium"))
        self.assertEqual(impact_for(t("Quiz", "Webontwikkeling 2"), cfg), (5, "low"))

    def test_missing_or_broken_file(self):
        self.assertEqual(load_weights("/nonexistent.json")["rules"], [])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w.json")
            open(path, "w").write("{nope")
            self.assertEqual(load_weights(path)["rules"], [])


class BigTaskTests(unittest.TestCase):
    cfg = {"high_from": 20, "medium_from": 10, "rules": []}

    def task(self, title, description="", course="Web", calendar="course_1"):
        return {"title": title, "description": description, "course": course, "calendar": calendar}

    def test_percentage(self):
        self.assertEqual(percentage_in("Deze opdracht telt voor 30% van het eindcijfer."), 30)
        self.assertEqual(percentage_in("Gewicht: 12,5 %"), 12.5)
        self.assertIsNone(percentage_in("Zorg dat 100% van de code getest is."))
        self.assertIsNone(percentage_in(""))

    def test_real_titles(self):
        # Titles from a real Artevelde (Grafische en Digitale Media) feed.
        small = ['Lesweek 3 "Koor"', "Journalopdracht week 3", "Lesopdracht 1: Parenting",
                 "3. @HOME pentools", "3.3 BUSHALTE: Retouche en toevoegen van lokale lichtinval",
                 "Opdracht 3 LFM: Beeld en Sfeer"]
        for title in small:
            # Course templates mention "examen" in every description: that must not count.
            self.assertFalse(classify(self.task(title, "Voorbereiding op het examen."), self.cfg)["big"], title)
        big = classify(self.task("Tussentijdse opdracht: Illustratief"), self.cfg)
        self.assertTrue(big["big"])
        self.assertEqual(big["big_reasons"][0]["type"], "title")
        self.assertTrue(classify(self.task("Examen Typografie"), self.cfg)["big"])

    def test_yellow_calendar(self):
        result = classify(self.task("Lesopdracht 2: Vrije paden", calendar="course_44401"), self.cfg, {"course_44401"})
        self.assertTrue(result["big"])
        self.assertEqual(result["big_reasons"], [{"type": "yellow", "text": "Geel in je Canvas-kalender"}])
        self.assertFalse(classify(self.task("Lesopdracht 2", calendar="course_1"), self.cfg, {"course_44401"})["big"])

    def test_percent_and_rules(self):
        by_pct = classify(self.task("Opdracht 3", "Telt mee voor 40% van je eindcijfer."), self.cfg)
        self.assertEqual((by_pct["weight"], by_pct["impact"], by_pct["big"]), (40, "high", True))
        small = classify(self.task("Quiz week 2", "Telt voor 5% van de punten."), self.cfg)
        self.assertEqual((small["impact"], small["big"]), ("low", False))
        # A low-weight rule overrides everything else, including yellow.
        cfg = dict(self.cfg, rules=[{"title": "tussentijds", "course": "", "weight": 5}])
        res = classify(self.task("Tussentijdse opdracht", calendar="course_9"), cfg, {"course_9"})
        self.assertEqual((res["big"], res["big_rule"]), (False, False))


class ColorTests(unittest.TestCase):
    def test_yellow_detection(self):
        from canvas_client import is_yellow
        for color in ("#F0C61C", "#FFD700", "#E1A80B", "#8D9900"):
            self.assertTrue(is_yellow(color), color)
        for color in ("#E1185C", "#008400", "#1770AB", "#FFF3B0", "#D97900", "bad"):
            self.assertFalse(is_yellow(color), color)

    def test_fetch_colors(self):
        from canvas_client import fetch_course_colors
        session = mock.Mock()
        session.get.return_value.json.return_value = {"custom_colors": {"course_1": "#F0C61C", "user_2": "nope"}}
        colors = fetch_course_colors("webcal://canvas.example/feeds/calendars/user_x.ics", "tok", session=session)
        self.assertEqual(colors, {"course_1": "#F0C61C"})
        url = session.get.call_args[0][0]
        self.assertEqual(url, "https://canvas.example/api/v1/users/self/colors")
        self.assertEqual(fetch_course_colors("https://canvas.example/f.ics", ""), {})

    def test_canvas_meta_and_titles(self):
        from canvas_client import canvas_meta, short_course, split_section
        meta = canvas_meta("event-assignment-123",
                           "https://c.example/calendar?include_contexts=course_55&month=10&year=2026#assignment_123", "X")
        self.assertEqual((meta["kind"], meta["calendar"], meta["link"]),
                         ("assignment", "course_55", "https://c.example/courses/55/assignments/123"))
        self.assertEqual(split_section('Lesweek 3 "Koor" (AVD1-B@S1)'), ('Lesweek 3 "Koor"', "AVD1-B@S1"))
        self.assertEqual(short_course("PBAGDM - STORY1 - Semester 1"), "STORY1")


class BuildTests(unittest.TestCase):
    def test_build_site(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"ICAL_FEED_URL": "https://canvas.example/f.ics", "NTFY_TOPIC": ""}), \
                mock.patch.object(CanvasClient, "_download", lambda self: shifted_ics()):
            out = os.path.join(tmp, "site")
            web = os.path.join(os.path.dirname(__file__), "..", "web")
            wfile = os.path.join(tmp, "w.json")
            with open(wfile, "w") as fh:
                json.dump({"rules": [{"title": "verslag", "weight": 30}]}, fh)
            payload = build_site.build(out, web, os.path.join(tmp, "state.json"), weights_file=wfile)
            with open(os.path.join(out, "tasks.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["count"], len(data["tasks"]))
            self.assertNotIn("uid", data["tasks"][0])
            self.assertEqual((data["tasks"][0]["weight"], data["tasks"][0]["impact"]), (30, "high"))
            self.assertTrue(data["tasks"][0]["big"])
            self.assertEqual(data["tasks"][1]["big_reasons"], [{"type": "title", "text": "Titel: 'examen'"}])
            self.assertIn("calendars", data)
            self.assertEqual(data["thresholds"], {"urgent": 2, "high": 7})
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                self.assertIn(f'<meta name="app-version" content="{data["app_version"]}">', fh.read())
            self.assertEqual(len(data["app_version"]), 12)
            for name in ("index.html", "sw.js", "manifest.json", "js/app.js", "icons/icon-192.png"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)

    def test_missing_feed_url(self):
        import build_site
        with mock.patch.dict(os.environ, {"ICAL_FEED_URL": ""}), \
                mock.patch("build_site.load_dotenv"):
            self.assertEqual(build_site.main(["--out", "/nonexistent/x"]), 1)


if __name__ == "__main__":
    unittest.main()

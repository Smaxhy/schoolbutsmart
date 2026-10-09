import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from canvas_client import CanvasClient, FeedError, split_course
from notifier import Notifier, filter_upcoming, urgency_for

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


class AppTests(unittest.TestCase):
    def test_routes(self):
        with mock.patch.dict(os.environ, {"ICAL_FEED_URL": "https://canvas.example/f.ics", "NTFY_TOPIC": "t", "ENABLE_SCHEDULER": "false"}):
            import app as app_module
            flask_app = app_module.create_app()
        flask_app.testing = True
        flask_app.extensions["client"]._download = lambda: ICS
        c = flask_app.test_client()
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(c.get("/sw.js").mimetype, "application/javascript")
        self.assertEqual(c.get("/static/manifest.json").status_code, 200)
        resp = c.get("/api/tasks")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("tasks", resp.get_json())
        self.assertEqual(c.post("/api/refresh").status_code, 200)


if __name__ == "__main__":
    unittest.main()

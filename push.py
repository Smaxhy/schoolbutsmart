"""Real phone notifications (Web Push) without an extra app, account or server.

- Keys: the VAPID key pair is derived from the secret Canvas feed URL, so there is no
  extra secret to set up. The public half goes into tasks.json for the phone.
- Mailbox: when you tap "Meldingen aan", the phone posts its push subscription (and the
  courses you marked yellow) to an ntfy.sh topic. Each build reads that topic, keeps the
  subscriptions in state/push.json, and sends pushes with pywebpush.

The site and its data are public already; a subscription only lets someone deliver a push
signed with the private key, which never leaves the build.
"""

import base64
import hashlib
import json
import logging
import os
import tempfile

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

log = logging.getLogger(__name__)

MAILBOX_SERVER = "https://ntfy.sh"
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def vapid_keys(secret):
    """(Vapid02 signer, public key as base64url) derived deterministically from `secret`."""
    seed = hashlib.sha256(b"artevelde-tasks-vapid|" + secret.encode("utf-8")).digest()
    private_value = int.from_bytes(seed, "big") % (_P256_ORDER - 1) + 1
    key = ec.derive_private_key(private_value, ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return Vapid02(private_key=key), _b64url(public)


def mailbox_topic(secret):
    return "artevelde-push-" + hashlib.sha256(b"mailbox|" + secret.encode("utf-8")).hexdigest()[:24]


def _valid_subscription(sub):
    keys = sub.get("keys") if isinstance(sub, dict) else None
    return (isinstance(sub, dict) and str(sub.get("endpoint", "")).startswith("https://")
            and isinstance(keys, dict) and keys.get("p256dh") and keys.get("auth"))


def load_store(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("Could not read %s, starting without push subscriptions", path)
        return {}


def save_store(path, store):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def collect(topic, store_path, session=None):
    """Merge subscriptions posted to the mailbox into the store. Returns the store."""
    store = load_store(store_path)
    try:
        resp = (session or requests).get(f"{MAILBOX_SERVER}/{topic}/json",
                                         params={"poll": "1", "since": "all"}, timeout=20)
        resp.raise_for_status()
        lines = resp.text.splitlines()
    except requests.RequestException as exc:
        log.warning("Could not read the push mailbox (%s)", type(exc).__name__)
        lines = []

    for line in lines:
        try:
            event = json.loads(line)
            if event.get("event") != "message":
                continue
            msg = json.loads(event.get("message") or "")
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        sent_at = int(event.get("time") or 0)
        if msg.get("unsubscribe"):
            store.pop(str(msg["unsubscribe"]), None)
            continue
        sub = msg.get("subscription")
        if not _valid_subscription(sub):
            continue
        known = store.get(sub["endpoint"])
        if known and known.get("time", 0) > sent_at:
            continue  # we already have a newer message from this phone
        yellow = [c for c in msg.get("yellow", []) if isinstance(c, str)][:50]
        store[sub["endpoint"]] = {"subscription": {"endpoint": sub["endpoint"], "keys": sub["keys"]},
                                  "yellow": yellow, "time": sent_at}
    save_store(store_path, store)
    return store


def yellow_from_phones(store):
    """Courses marked yellow on any subscribed phone."""
    return {c for entry in store.values() for c in entry.get("yellow", [])}


class PushSender:
    """Sends one notification to every subscribed phone; drops phones that unsubscribed."""

    def __init__(self, store, store_path, vapid, contact):
        self.store = store
        self.store_path = store_path
        self.vapid = vapid
        self.claims = {"sub": contact}

    def __bool__(self):
        return bool(self.store)

    def __call__(self, title, body, url="", tag=""):
        payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
        delivered, gone = 0, []
        for endpoint, entry in self.store.items():
            try:
                webpush(entry["subscription"], data=payload, vapid_private_key=self.vapid,
                        vapid_claims=dict(self.claims), ttl=24 * 3600, timeout=15)
                delivered += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    gone.append(endpoint)  # the phone unsubscribed or reinstalled
                else:
                    log.warning("Web push failed (HTTP %s)", status)
            except requests.RequestException as exc:
                log.warning("Web push failed (%s)", type(exc).__name__)
        if gone:
            for endpoint in gone:
                self.store.pop(endpoint, None)
            save_store(self.store_path, self.store)
        log.info("Web push: delivered to %d phone(s)", delivered)
        return delivered > 0


def setup(secret, store_path, contact, session=None):
    """Everything the build needs: (sender or None, public key, mailbox topic, yellow set)."""
    vapid, public_key = vapid_keys(secret)
    topic = mailbox_topic(secret)
    store = collect(topic, store_path, session=session)
    log.info("Web push: %d phone(s) subscribed", len(store))
    sender = PushSender(store, store_path, vapid, contact) if store else None
    return sender, public_key, topic, yellow_from_phones(store)


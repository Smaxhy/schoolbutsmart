"""Grade-impact rules from weights.json.

The Canvas calendar feed has no points or grade weights, so you tell the app
yourself which assignments count for how much of your final grade.
"""

import json
import logging

log = logging.getLogger(__name__)

DEFAULT_HIGH_FROM = 20
DEFAULT_MEDIUM_FROM = 10


def load_weights(path):
    """Return {"high_from", "medium_from", "rules"}; an empty config if the file is missing/invalid."""
    config = {"high_from": DEFAULT_HIGH_FROM, "medium_from": DEFAULT_MEDIUM_FROM, "rules": []}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return config
    except (OSError, ValueError):
        log.warning("Could not read %s, ignoring grade weights", path)
        return config

    if not isinstance(raw, dict):
        return config
    for key in ("high_from", "medium_from"):
        if isinstance(raw.get(key), (int, float)):
            config[key] = raw[key]
    for rule in raw.get("rules", []):
        if (isinstance(rule, dict) and isinstance(rule.get("weight"), (int, float))
                and (rule.get("title") or rule.get("course"))):
            config["rules"].append({
                "title": str(rule.get("title", "")).lower(),
                "course": str(rule.get("course", "")).lower(),
                "weight": rule["weight"],
            })
        else:
            log.warning("Ignoring invalid weights rule: %r", rule)
    return config


def impact_for(task, config):
    """Return (weight, impact) for a task. First matching rule wins; (None, None) if none match."""
    title = task["title"].lower()
    course = (task.get("course") or "").lower()
    for rule in config["rules"]:
        if rule["title"] and rule["title"] not in title:
            continue
        if rule["course"] and rule["course"] not in course:
            continue
        weight = rule["weight"]
        if weight >= config["high_from"]:
            return weight, "high"
        if weight >= config["medium_from"]:
            return weight, "medium"
        return weight, "low"
    return None, None

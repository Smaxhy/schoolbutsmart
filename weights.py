"""Grade-impact rules from weights.json.

The Canvas calendar feed has no points or grade weights, so you tell the app
yourself which assignments count for how much of your final grade.
"""

import json
import logging
import re

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


# ---- big-task detection ---------------------------------------------------
# Signals, strongest first:
#   1. a weights.json rule (also the way to say "this is NOT big": give it a low weight)
#   2. the task's Canvas calendar is one of your yellow ones (Canvas colours per course)
#   3. the description states a grade percentage >= high_from ("telt voor 30%")
#   4. the title names an evaluation moment (examen, tussentijdse opdracht, eindwerk, ...)
# Words in descriptions are NOT used: course templates mention "examen" in every task,
# which made weekly exercises look big.

BIG_TITLE_WORDS = (
    "examen", "tentamen", "tussentijds", "eindopdracht", "eindwerk", "eindproject",
    "eindpresentatie", "eindevaluatie", "eindproduct", "bachelorproef", "portfolio", "jury",
    "evaluatiemoment", "proefexamen",
)
# Weekly / small work; a title like this is never "big" on its title alone.
SMALL_TITLE_WORDS = (
    "lesopdracht", "lesweek", "journal", "@home", "oefening", "huiswerk", "voorbereiding",
    "quiz", "reflectie", "logboek",
)
_GRADE_WORDS = r"(?:eindcijfer|eindscore|eindresultaat|cijfer|punten|score|gewicht|weegt|telt|quotering|beoordeling)"
_PCT = r"(\d{1,3}(?:[.,]\d+)?)\s*%"
_PCT_NEAR_GRADE_RE = re.compile(
    rf"{_PCT}[^.\n]{{0,60}}?{_GRADE_WORDS}|{_GRADE_WORDS}[^.\n]{{0,60}}?{_PCT}", re.I)


def _tier(weight, config):
    if weight >= config["high_from"]:
        return "high"
    if weight >= config["medium_from"]:
        return "medium"
    return "low"


def percentage_in(text):
    """Largest 'x %' that sits near a grade word ('telt voor 30% van het eindcijfer'), or None."""
    best = None
    for match in _PCT_NEAR_GRADE_RE.finditer(text or ""):
        raw = match.group(1) or match.group(2)
        value = float(raw.replace(",", "."))
        if 0 < value <= 100:
            best = value if best is None else max(best, value)
    if best is not None and best.is_integer():
        best = int(best)
    return best


def classify(task, config, yellow_calendars=()):
    """Return {"weight", "impact", "big", "big_rule", "big_reasons"} for a task.

    big_reasons is a list of {"type": "rule"|"yellow"|"percent"|"title", "text": ...}.
    big_rule is True/False when a weights.json rule decides, else None; the app uses it
    to recompute "big" when you pick your yellow calendars on the phone.
    """
    weight, impact = impact_for(task, config)
    if weight is not None:
        big = impact == "high"
        reasons = [{"type": "rule", "text": f"{weight}% van je eindcijfer (weights.json)"}] if big else []
        return {"weight": weight, "impact": impact, "big": big, "big_rule": big, "big_reasons": reasons}

    reasons = []
    if task.get("calendar") and task["calendar"] in yellow_calendars:
        reasons.append({"type": "yellow", "text": "Geel in je Canvas-kalender"})

    pct = percentage_in(task.get("description") or "")
    if pct is not None:
        weight, impact = pct, _tier(pct, config)
        if impact == "high":
            reasons.append({"type": "percent", "text": f"Telt voor {pct}% (volgens de beschrijving)"})

    title = task["title"].lower()
    big_word = next((w for w in BIG_TITLE_WORDS if w in title), None)
    if big_word and not any(w in title for w in SMALL_TITLE_WORDS):
        reasons.append({"type": "title", "text": f"Titel: '{big_word}'"})

    return {"weight": weight, "impact": impact, "big": bool(reasons), "big_rule": None, "big_reasons": reasons}

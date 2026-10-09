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
# The feed has no weights, so besides weights.json we look for tell-tale words
# and "x% van het eindcijfer"-style phrases in the title and description.

BIG_TITLE_WORDS = (
    "examen", "tentamen", "eindwerk", "eindopdracht", "eindproject", "eindpresentatie",
    "eindevaluatie", "bachelorproef", "portfolio", "project", "paper", "essay",
    "onderzoek", "presentatie", "verdediging", "stageverslag", "groepswerk", "groepsopdracht",
)
BIG_DESCRIPTION_WORDS = (
    "examen", "tentamen", "eindwerk", "eindopdracht", "eindproject", "bachelorproef",
    "portfolio", "groepswerk", "groepsopdracht",
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


def classify(task, config):
    """Return {"weight", "impact", "big", "big_reasons"} for a task.

    Order: an explicit weights.json rule wins (so a rule with a low weight can silence a
    false positive); otherwise a percentage found in the description; then keywords.
    """
    weight, impact = impact_for(task, config)
    reasons = []
    if weight is not None:
        if impact == "high":
            reasons.append(f"{weight}% van je eindcijfer (weights.json)")
        return {"weight": weight, "impact": impact, "big": impact == "high", "big_reasons": reasons}

    description = task.get("description") or ""
    pct = percentage_in(description)
    if pct is not None:
        weight, impact = pct, _tier(pct, config)
        if impact == "high":
            reasons.append(f"Telt voor {pct}% (volgens de beschrijving)")

    title = task["title"].lower()
    title_word = next((w for w in BIG_TITLE_WORDS if w in title), None)
    if title_word:
        reasons.append(f"Titel bevat '{title_word}'")
    desc_lower = description.lower()
    desc_word = next((w for w in BIG_DESCRIPTION_WORDS if w in desc_lower and w != title_word), None)
    if desc_word:
        reasons.append(f"Beschrijving vermeldt '{desc_word}'")

    return {"weight": weight, "impact": impact, "big": bool(reasons), "big_reasons": reasons}

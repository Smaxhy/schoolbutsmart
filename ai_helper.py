"""Pre-generate "Hulp van Claude" for upcoming tasks with Claude Haiku, during the build.

Runs in GitHub Actions with the ANTHROPIC_API_KEY secret, so nobody has to enter a key on
their phone. Results are cached by task content (state/help.json on the state branch), so
each task is only sent to Claude once, and again only when its title, deadline or
description changes.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
FALLBACK_MODEL = "claude-haiku-4-5"  # for accounts without access to Haiku 5.5 (HTTP 404)
PROMPT_VERSION = "3"  # bump to regenerate everything after changing the prompt
MAX_NEW_PER_RUN = 25

WEEKDAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
          "september", "oktober", "november", "december"]

SYSTEM_PROMPT = """Je bent de assistent van een student van Artevelde Hogeschool (Gent). Je maakt de opdracht uit Canvas zo volledig mogelijk voor de student.

Antwoord in het Nederlands met deze kopjes:
## Wat wordt er gevraagd
1 tot 3 zinnen.
## Klaar om in te dienen
Maak alles wat in tekst kan volledig af, op het niveau van een goede student: de tekst, het journal, de reflectie, de uitleg, het script of de voice-over, de tekst bij een beeld, de presentatietekst. Geen opzet of samenvatting, maar het eindresultaat.
## Zo maak je de rest
Voor wat jij niet kunt maken (tekenen, fotograferen, opnemen, monteren, opmaken): het gekozen concept en de exacte stappen in de juiste software (gereedschap, instellingen, lagen, formaat, export), zodat de student het meteen kan uitvoeren. Laat dit kopje weg als alles al klaar is.
## Checklist voor het indienen

Regels:
- Gebruik wat in de beschrijving staat. Ontbreekt er iets, maak dan een redelijke keuze en zeg in één zin welke. Verzin geen beoordelingscriteria.
- Vraagt de opdracht een persoonlijke tekst, schrijf dan in de ik-vorm en markeer met [vul aan: ...] waar de student een eigen ervaring of detail moet invullen.
- Vraagt de student daarna iets of wil die een aanpassing, voer het uit en geef het volledige aangepaste resultaat.
- Gebruik alleen "## " voor kopjes, "- " of "1. " voor lijsten en **vet** voor nadruk. Geen tabellen."""


def _format_due(iso):
    due = datetime.fromisoformat(iso)
    return f"{WEEKDAYS[due.weekday()]} {due.day} {MONTHS[due.month - 1]} {due.year} om {due:%H:%M}"


def build_prompt(task, today):
    lines = [
        f"Vandaag: {today}",
        f"Vak: {task.get('course') or 'onbekend'}",
        f"Opdracht: {task['title']}",
        f"Deadline: {_format_due(task['due_date'])}",
    ]
    if task.get("weight") is not None:
        lines.append(f"Gewicht: {task['weight']}% van het eindcijfer")
    lines += ["", "Beschrijving uit Canvas:", "<beschrijving>",
              task.get("description") or "(geen beschrijving)", "</beschrijving>"]
    return "\n".join(lines)


def cache_key(task):
    raw = "|".join([MODEL, PROMPT_VERSION, task["title"], task.get("course") or "",
                    task["due_date"], task.get("description") or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("Could not read %s, starting a fresh help cache", path)
        return {}


def save_cache(path, cache):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def ask_claude(client, task, today):
    """One Haiku call; returns the answer text, or None if Claude declined."""
    messages = [{"role": "user", "content": build_prompt(task, today)}]
    try:
        response = client.messages.create(model=MODEL, max_tokens=16000, output_config={"effort": "medium"},
                                          system=SYSTEM_PROMPT, messages=messages)
    except anthropic.NotFoundError:
        log.warning("%s not available for this key, using %s", MODEL, FALLBACK_MODEL)
        response = client.messages.create(model=FALLBACK_MODEL, max_tokens=16000,
                                          system=SYSTEM_PROMPT, messages=messages)
    if response.stop_reason == "refusal":
        log.warning("Claude declined to help with one task")
        return None
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n(Afgebroken: het antwoord was te lang.)"
    return text or None


def add_help(tasks, cache_path, api_key, today, client=None):
    """Set task["help"] for each task (cached text or a fresh Haiku answer). Returns #new."""
    cache = load_cache(cache_path)
    keys = {id(t): cache_key(t) for t in tasks}
    for task in tasks:
        entry = cache.get(keys[id(task)])
        task["help"] = entry["text"] if entry else None

    missing = [t for t in tasks if t["help"] is None]
    new = 0
    if missing and api_key:
        if client is None:
            workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
            # Keys not scoped to a workspace must name one in this header.
            headers = {"anthropic-workspace-id": workspace} if workspace else None
            client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120, default_headers=headers)
        for task in missing[:MAX_NEW_PER_RUN]:
            try:
                text = ask_claude(client, task, today)
            except anthropic.AuthenticationError:
                log.error("ANTHROPIC_API_KEY is invalid; skipping Claude help")
                break
            except anthropic.APIStatusError as exc:
                detail = ((exc.body or {}).get("error") or {}).get("message", "") if isinstance(exc.body, dict) else ""
                log.warning("Claude request failed (HTTP %s: %s); will retry next run", exc.status_code, detail)
                if exc.status_code == 429 or "credit" in str(exc.message).lower():
                    break
                continue
            except anthropic.APIConnectionError:
                log.warning("Could not reach Claude; will retry next run")
                break
            if text:
                key = keys[id(task)]
                cache[key] = {"text": text, "title": task["title"], "created": today}
                task["help"] = text
                new += 1
    elif missing:
        log.info("No ANTHROPIC_API_KEY: %d tasks without Claude help", len(missing))

    # Keep only entries for tasks that still exist, so the file doesn't grow forever.
    live = set(keys.values())
    pruned = {k: v for k, v in cache.items() if k in live}
    save_cache(cache_path, pruned)
    log.info("Claude help: %d cached, %d new", sum(1 for t in tasks if t["help"]) - new, new)
    return new

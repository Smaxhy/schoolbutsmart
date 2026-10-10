(() => {
  "use strict";

  const CACHE_KEY = "artevelde-tasks-cache-v1";
  const VIEW_KEY = "artevelde-tasks-view";
  const API_KEY_KEY = "artevelde-tasks-anthropic-key";
  const PLAN_PREFIX = "artevelde-tasks-plan:";
  const YELLOW_KEY = "artevelde-tasks-yellow-calendars";
  const RELOADED_KEY = "artevelde-tasks-reloaded-for";
  const POLL_MS = 5 * 60 * 1000;
  const APP_VERSION = (document.querySelector('meta[name="app-version"]') || {}).content || "dev";

  const WEEKDAYS = ["zo", "ma", "di", "wo", "do", "vr", "za"];
  const WEEKDAYS_LONG = ["zondag", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag"];
  const MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
  const MONTHS_LONG = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
    "september", "oktober", "november", "december"];
  const GROUPS = [
    { key: "this", title: "Deze week" },
    { key: "next", title: "Volgende week" },
    { key: "later", title: "Later" },
  ];

  const $ = (id) => document.getElementById(id);
  const appEl = $("app");
  const statusEl = $("status");
  const updatedEl = $("updated");
  const refreshBtn = $("refresh");
  const detailEl = $("detail");
  const settingsEl = $("settings-sheet");
  const apiKeyInput = $("api-key");
  const tabs = document.querySelectorAll(".tab");

  let current = null; // last rendered payload
  let view = "all";
  let planAbort = null;

  // ---- storage (can throw in private mode) ------------------------------

  function storageGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* ignore */ }
  }

  function loadCache() {
    try {
      return JSON.parse(storageGet(CACHE_KEY));
    } catch (e) {
      return null;
    }
  }

  if (storageGet(VIEW_KEY) === "big") view = "big";

  // ---- helpers ----------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Show deadlines in the school's timezone (the offset the server sent), whatever the device uses.
  function wallClock(iso) {
    return new Date(iso.slice(0, 19));
  }

  function startOfDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  const pad = (n) => String(n).padStart(2, "0");
  const formatTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const formatShort = (d) => `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  const formatLong = (d) => `${WEEKDAYS_LONG[d.getDay()]} ${d.getDate()} ${MONTHS_LONG[d.getMonth()]} ${d.getFullYear()}`;

  function countdown(days) {
    if (days <= 0) return "vandaag";
    if (days === 1) return "morgen";
    return `over ${days} dagen`;
  }

  // Recomputed on the client so cached (offline) data doesn't show stale countdowns.
  function daysUntil(due, now) {
    return Math.round((startOfDay(due) - startOfDay(now)) / 86400000);
  }

  function urgencyFor(days, thresholds) {
    if (days <= thresholds.urgent) return "urgent";
    if (days <= thresholds.high) return "high";
    return "default";
  }

  function chipColors(name) {
    let hash = 0;
    for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
    const hue = hash % 360;
    return { background: `hsl(${hue} 40% 26%)`, color: `hsl(${hue} 85% 84%)` };
  }

  function weekGroup(due, now) {
    const monday = startOfDay(now);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    const nextMonday = new Date(monday);
    nextMonday.setDate(monday.getDate() + 7);
    const afterNext = new Date(monday);
    afterNext.setDate(monday.getDate() + 14);
    if (due < nextMonday) return "this";
    if (due < afterNext) return "next";
    return "later";
  }

  function courseChip(task) {
    const name = task.course_short || task.course || "Overig";
    const chip = el("span", "chip", name);
    chip.title = task.course || name;
    if (task.color) {
      chip.style.background = `color-mix(in srgb, ${task.color} 32%, #1c1e22)`;
      chip.style.color = `color-mix(in srgb, ${task.color} 55%, #ffffff)`;
    } else {
      Object.assign(chip.style, chipColors(task.course || "Overig"));
    }
    return chip;
  }

  // ---- big tasks ----------------------------------------------------------
  // Canvas colours each course; the yellow ones hold the big tasks. You pick them in the
  // settings (pre-filled from your Canvas colours when the build has a Canvas token).

  function storedYellow() {
    try {
      const raw = JSON.parse(storageGet(YELLOW_KEY));
      return Array.isArray(raw) ? new Set(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function yellowCalendars(data) {
    return storedYellow() || new Set((data.calendars || []).filter((c) => c.yellow).map((c) => c.id));
  }

  // Older data had plain strings as reasons.
  const reasonObj = (r) => (typeof r === "string" ? { type: "title", text: r } : r);

  function bigInfo(task, yellow) {
    if (task.big_rule === true || task.big_rule === false) {
      return { big: task.big_rule, reasons: (task.big_reasons || []).map(reasonObj) };
    }
    const reasons = (task.big_reasons || []).map(reasonObj).filter((r) => r.type !== "yellow");
    if (task.calendar && yellow.has(task.calendar)) reasons.unshift({ type: "yellow", text: "Geel in je Canvas-kalender" });
    return { big: reasons.length > 0, reasons };
  }

  function planKey(task) {
    return `${PLAN_PREFIX}${task.course}|${task.title}|${task.due_date}`;
  }

  function linkOut(a, href) {
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  // Put http(s) links in plain text into <a> elements, without ever using innerHTML.
  function appendLinkified(parent, text) {
    const re = /https?:\/\/[^\s<>"')]+/g;
    let last = 0;
    let match;
    while ((match = re.exec(text))) {
      const url = match[0].replace(/[.,;:!?]+$/, "");
      if (match.index > last) parent.append(text.slice(last, match.index));
      parent.append(linkOut(el("a", null, url), url));
      last = match.index + url.length;
      re.lastIndex = last;
    }
    if (last < text.length) parent.append(text.slice(last));
  }

  // Tiny, safe Markdown subset for Claude's answer: "## " headings, "- "/"1. " lists, **bold**.
  function appendInline(parent, text) {
    text.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) parent.append(el("strong", null, part.slice(2, -2)));
      else if (part) parent.append(part);
    });
  }

  function renderMarkdown(container, text) {
    container.replaceChildren();
    let list = null;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line) {
        list = null;
        continue;
      }
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      const bullet = line.match(/^[-*•]\s+(.*)$/);
      const numbered = line.match(/^\d+[.)]\s+(.*)$/);
      if (heading) {
        list = null;
        appendInline(container.appendChild(el("h4")), heading[1]);
      } else if (bullet || numbered) {
        const tag = bullet ? "UL" : "OL";
        if (!list || list.tagName !== tag) list = container.appendChild(el(tag.toLowerCase()));
        appendInline(list.appendChild(el("li")), (bullet || numbered)[1]);
      } else {
        list = null;
        appendInline(container.appendChild(el("p")), line);
      }
    }
  }

  // ---- settings (API key) -----------------------------------------------

  function renderCalendarPicker() {
    const box = $("calendar-picker");
    box.replaceChildren();
    const calendars = (current && current.calendars) || [];
    if (!calendars.length) {
      box.append(el("p", "muted small", "Nog geen vakken gevonden. Vernieuw eerst je taken."));
      return;
    }
    const chosen = yellowCalendars(current);
    $("picker-auto").hidden = !(current.colors_known && !storedYellow());
    calendars.forEach((cal) => {
      const row = el("label", "cal-row");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.checked = chosen.has(cal.id);
      box2.addEventListener("change", () => {
        const next = yellowCalendars(current);
        if (box2.checked) next.add(cal.id);
        else next.delete(cal.id);
        storageSet(YELLOW_KEY, JSON.stringify([...next]));
        $("picker-auto").hidden = true;
        render(current);
      });
      const swatch = el("span", "swatch");
      swatch.style.background = cal.color || chipColors(cal.name || "").background;
      const text = el("span", "cal-text");
      text.append(el("strong", null, cal.short || cal.name), el("span", "muted small", ` · ${cal.count} taken`));
      if (cal.examples && cal.examples.length) text.append(el("span", "cal-examples", cal.examples.slice(0, 2).join(" · ")));
      row.append(box2, swatch, text);
      box.append(row);
    });
  }

  function openSettings() {
    apiKeyInput.value = storageGet(API_KEY_KEY) || "";
    renderCalendarPicker();
    settingsEl.showModal();
    settingsEl.scrollTop = 0;
  }

  $("settings").addEventListener("click", openSettings);
  $("settings-close").addEventListener("click", () => settingsEl.close("cancel"));
  $("key-remove").addEventListener("click", () => {
    storageSet(API_KEY_KEY, null);
    apiKeyInput.value = "";
  });
  settingsEl.addEventListener("close", () => {
    if (settingsEl.returnValue === "save") {
      const key = apiKeyInput.value.trim();
      storageSet(API_KEY_KEY, key || null);
    }
  });

  // ---- Claude plan ------------------------------------------------------

  function renderPlanBox(box, task, text, { streaming = false, error = null } = {}) {
    box.replaceChildren();
    const head = el("div", "ai-head");
    head.append(el("strong", null, "✨ Plan van Claude"));
    if (streaming) {
      const stop = el("button", null, "Stop");
      stop.type = "button";
      stop.addEventListener("click", () => planAbort && planAbort.abort());
      head.append(stop);
    } else if (text) {
      const again = el("button", null, "Opnieuw");
      again.type = "button";
      again.addEventListener("click", () => runPlan(task, box));
      head.append(again);
    }
    box.append(head);

    const body = el("div", "ai-body");
    if (text) renderMarkdown(body, text);
    else if (streaming) body.append(el("p", "thinking", "Claude denkt na"));
    box.append(body);

    if (error) box.append(el("p", "status error", error));
    if (!streaming && text) {
      box.append(el("p", "ai-note", "Gebruik dit als hulp om te plannen. Controleer altijd de opdracht en de regels over AI in Canvas."));
    }
  }

  async function runPlan(task, box) {
    const apiKey = storageGet(API_KEY_KEY);
    if (!apiKey) {
      openSettings();
      return;
    }
    if (planAbort) planAbort.abort();
    const controller = new AbortController();
    planAbort = controller;
    box.hidden = false;
    renderPlanBox(box, task, "", { streaming: true });

    let latest = "";
    try {
      const { streamPlan } = await import(`./claude.js?v=${APP_VERSION}`);
      const now = new Date();
      latest = await streamPlan(task, apiKey, {
        signal: controller.signal,
        today: `${formatLong(now)} ${formatTime(now)}`,
        onText: (text) => {
          latest = text;
          if (planAbort === controller) renderPlanBox(box, task, text, { streaming: true });
        },
      });
      storageSet(planKey(task), latest);
      if (planAbort === controller) renderPlanBox(box, task, latest);
    } catch (err) {
      if (planAbort === controller) {
        renderPlanBox(box, task, latest, { error: (err && err.message) || "Er ging iets mis." });
      }
    } finally {
      if (planAbort === controller) planAbort = null;
      if (reloadPending) setTimeout(() => reloadPending && reloadSoon(), 500);
    }
  }

  // ---- detail sheet -----------------------------------------------------

  function openDetail(item) {
    const { task, due, days, urgency, big, reasons } = item;
    if (planAbort) planAbort.abort();
    const inner = el("div", "sheet-inner");

    const head = el("div", "sheet-head");
    const close = el("button", "icon-btn close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Sluiten");
    close.addEventListener("click", () => detailEl.close());
    head.append(courseChip(task), close);

    const title = el("h2", null, task.title);
    title.id = "detail-title";

    const when = el("p", "when");
    when.append(el("strong", null, formatLong(due)), ` om ${formatTime(due)}`);

    const facts = el("div", "facts");
    facts.append(el("span", `fact ${urgency}`, countdown(days)));
    if (big) facts.append(el("span", "fact big", "★ Grote taak"));
    if (typeof task.weight === "number") facts.append(el("span", "fact", `${task.weight}% van je eindcijfer`));

    inner.append(head, title, when, facts);

    if (big && reasons.length) {
      inner.append(el("h3", null, "Waarom een grote taak"));
      const ul = el("ul", "reasons");
      reasons.forEach((r) => ul.append(el("li", null, r.text)));
      inner.append(ul);
    }

    inner.append(el("h3", null, "Wat moet je doen"));
    const body = el("p", task.description ? "body-text" : "body-text none");
    if (task.description) appendLinkified(body, task.description);
    else body.textContent = "Geen beschrijving in de kalender. Open de opdracht in Canvas voor de details.";
    inner.append(body);

    const actions = el("div", "row");
    const planBtn = el("button", "btn claude", "✨ Plan met Claude");
    planBtn.type = "button";
    actions.append(planBtn);
    if (task.url && /^https?:\/\//i.test(task.url)) {
      actions.append(linkOut(el("a", "btn primary", "Open in Canvas"), task.url));
    }
    inner.append(actions);

    const planBox = el("div", "ai");
    planBox.hidden = true;
    inner.append(planBox);
    planBtn.addEventListener("click", () => runPlan(task, planBox));

    const saved = storageGet(planKey(task));
    if (saved) {
      planBox.hidden = false;
      renderPlanBox(planBox, task, saved);
    }

    detailEl.replaceChildren(inner);
    if (!detailEl.open) detailEl.showModal();
    detailEl.scrollTop = 0;
  }

  detailEl.addEventListener("close", () => {
    if (planAbort) planAbort.abort();
  });
  [detailEl, settingsEl].forEach((sheet) => sheet.addEventListener("click", (e) => {
    if (e.target === sheet) sheet.close(); // click on the backdrop
  }));

  // ---- list rendering ---------------------------------------------------

  function renderCard(item) {
    const { task, due, days, urgency, big } = item;
    const card = el("article", `card ${urgency}${big ? " is-big" : ""}`);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `${task.title}, ${countdown(days)}. Details bekijken`);

    const date = el("div", "date-block");
    date.append(el("span", "wd", WEEKDAYS[due.getDay()]), el("span", "day", String(due.getDate())), el("span", "mon", MONTHS[due.getMonth()]));

    const bodyEl = el("div", "card-body");
    const top = el("div", "card-top");
    top.append(courseChip(task), el("span", "countdown", countdown(days)));

    const meta = el("div", "card-meta");
    meta.append(el("span", null, formatTime(due)));
    if (big) meta.append(el("span", "badge big", "★ Groot"));
    if (typeof task.weight === "number") meta.append(el("span", "badge weight", `${task.weight}%`));

    bodyEl.append(top, el("h3", null, task.title), meta);
    if (task.description) bodyEl.append(el("p", "desc", task.description));
    card.append(date, bodyEl);

    card.addEventListener("click", () => openDetail(item));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openDetail(item);
      }
    });
    return card;
  }

  function section(title, items, className) {
    const sec = el("section");
    const h = el("h2", `group-title${className ? ` ${className}` : ""}`, title);
    h.append(el("span", "count", String(items.length)));
    sec.append(h);
    items.forEach((i) => sec.append(renderCard(i)));
    return sec;
  }

  function weekSections(items) {
    return GROUPS
      .map((g) => ({ g, list: items.filter((i) => i.group === g.key) }))
      .filter(({ list }) => list.length)
      .map(({ g, list }) => section(g.title, list));
  }

  function emptyState(emoji, title, text) {
    const box = el("div", "empty");
    box.append(el("span", "emoji", emoji), el("h2", null, title), el("p", null, text));
    return box;
  }

  function render(data) {
    current = data;
    const now = new Date();
    const thresholds = data.thresholds || { urgent: 2, high: 7 };
    const lookahead = data.lookahead_days || 14;

    const yellow = yellowCalendars(data);
    const all = (data.tasks || [])
      .map((task) => {
        const due = wallClock(task.due_date);
        const days = daysUntil(due, now);
        return { task, due, days, urgency: urgencyFor(days, thresholds), group: weekGroup(due, now), ...bigInfo(task, yellow) };
      })
      .filter((i) => i.due >= now && i.days <= lookahead)
      .sort((a, b) => a.due - b.due);
    const big = all.filter((i) => i.big);

    $("stat-urgent").textContent = all.filter((i) => i.urgency === "urgent").length;
    $("stat-week").textContent = all.filter((i) => i.group === "this").length;
    $("stat-big").textContent = big.length;
    updatedEl.textContent = data.fetched_at ? `Bijgewerkt ${formatShort(wallClock(data.fetched_at))} ${formatTime(wallClock(data.fetched_at))}` : "";

    appEl.replaceChildren();
    if (view === "big") {
      if (!big.length) {
        appEl.append(emptyState("🌿", "Geen grote taken op komst", "Examens, projecten en zware opdrachten verschijnen hier."));
      } else {
        weekSections(big).forEach((s) => appEl.append(s));
      }
      return;
    }

    if (!all.length) {
      appEl.append(emptyState("🎉", "Geen taken op komst", `Niets te doen in de komende ${lookahead} dagen.`));
      return;
    }
    if (big.length) appEl.append(section("★ Grote taken", big, "big"));
    weekSections(all.filter((i) => !i.big)).forEach((s) => appEl.append(s));
  }

  // A rendering bug must never leave the page stuck on "Laden…".
  function safeRender(data) {
    try {
      render(data);
    } catch (err) {
      console.error(err);
      appEl.replaceChildren(emptyState("⚠️", "Er ging iets mis bij het tonen van je taken", "Tik op vernieuwen of herlaad de pagina."));
    }
  }

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.className = isError ? "status error" : "status";
    statusEl.hidden = !message;
  }

  // ---- auto-update ------------------------------------------------------

  let reloadPending = false;
  let reloadVersion = null;

  function busy() {
    return detailEl.open || settingsEl.open || planAbort !== null;
  }

  // Reload into the new version, but never in the middle of reading a task or a Claude plan.
  function reloadSoon() {
    if (busy()) {
      reloadPending = true;
      return;
    }
    reloadPending = false;
    if (reloadVersion) {
      try {
        sessionStorage.setItem(RELOADED_KEY, reloadVersion);
      } catch (e) { /* ignore */ }
    }
    window.location.reload();
  }

  [detailEl, settingsEl].forEach((sheet) => sheet.addEventListener("close", () => {
    if (reloadPending) setTimeout(() => reloadPending && reloadSoon(), 500);
  }));

  function checkAppVersion(data) {
    const latest = data.app_version;
    if (!latest || APP_VERSION === "dev" || latest === APP_VERSION) return;
    let reloadedFor = null;
    try {
      reloadedFor = sessionStorage.getItem(RELOADED_KEY);
    } catch (e) { /* ignore */ }
    if (reloadedFor === latest) return; // already tried once; a stale CDN copy must not cause a loop
    reloadVersion = latest;
    reloadSoon();
  }

  // ---- data -------------------------------------------------------------

  let loading = false;

  async function load() {
    if (loading) return;
    loading = true;
    refreshBtn.classList.add("spinning");
    try {
      const resp = await fetch(`tasks.json?t=${Date.now()}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      storageSet(CACHE_KEY, JSON.stringify(data));
      checkAppVersion(data);
      safeRender(data);
      showStatus("", false);
    } catch (err) {
      const cached = loadCache();
      if (cached) {
        safeRender(cached);
        showStatus(navigator.onLine ? "Kon niet vernieuwen; dit zijn de laatst opgehaalde taken." : "Je bent offline; dit zijn de laatst opgehaalde taken.", false);
      } else {
        appEl.replaceChildren(emptyState("⚠️", "Kon taken niet laden", "Controleer je verbinding en probeer opnieuw."));
        showStatus(navigator.onLine ? "" : "Je bent offline en er zijn nog geen opgeslagen gegevens.", true);
      }
    } finally {
      loading = false;
      refreshBtn.classList.remove("spinning");
    }
  }

  // ---- tabs -------------------------------------------------------------

  function syncTabs() {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === view)));
  }

  tabs.forEach((t) => t.addEventListener("click", () => {
    view = t.dataset.view;
    storageSet(VIEW_KEY, view);
    syncTabs();
    if (current) render(current);
  }));

  // ---- pull to refresh --------------------------------------------------

  function setupPullToRefresh() {
    const ptr = $("ptr");
    const label = $("ptr-label");
    const THRESHOLD = 70;
    const MAX = 110;
    let startY = null;
    let pull = 0;

    const setHeight = (px) => { ptr.style.height = `${px}px`; };
    const sheetOpen = () => detailEl.open || settingsEl.open;

    document.addEventListener("touchstart", (e) => {
      startY = window.scrollY <= 0 && e.touches.length === 1 && !sheetOpen() ? e.touches[0].clientY : null;
      pull = 0;
      ptr.classList.remove("animating");
    }, { passive: true });

    document.addEventListener("touchmove", (e) => {
      if (startY === null) return;
      const dy = e.touches[0].clientY - startY;
      if (dy <= 0 || window.scrollY > 0) {
        pull = 0;
        setHeight(0);
        return;
      }
      pull = Math.min(dy * 0.5, MAX);
      label.textContent = pull >= THRESHOLD ? "Loslaten om te vernieuwen" : "Trek om te vernieuwen";
      setHeight(pull);
    }, { passive: true });

    function end() {
      if (startY === null) return;
      const triggered = pull >= THRESHOLD;
      startY = null;
      pull = 0;
      ptr.classList.add("animating");
      setHeight(0);
      if (triggered) load();
    }

    document.addEventListener("touchend", end, { passive: true });
    document.addEventListener("touchcancel", end, { passive: true });
  }

  // ---- init -------------------------------------------------------------

  refreshBtn.addEventListener("click", () => load());
  window.addEventListener("online", () => load());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });
  setupPullToRefresh();
  syncTabs();
  setInterval(() => {
    if (document.visibilityState === "visible") load();
  }, POLL_MS);

  const cached = loadCache();
  if (cached) safeRender(cached);
  load();

  if ("serviceWorker" in navigator) {
    // A new service worker takes over right away; reload so the page runs the new code too.
    const hadController = Boolean(navigator.serviceWorker.controller);
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (hadController) reloadSoon();
    });
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).then((reg) => {
        document.addEventListener("visibilitychange", () => {
          if (document.visibilityState === "visible") reg.update().catch(() => {});
        });
      }).catch(() => { /* not fatal */ });
    });
  }
})();

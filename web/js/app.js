(() => {
  "use strict";

  const CACHE_KEY = "artevelde-tasks-cache-v1";
  const FILTER_KEY = "artevelde-tasks-filter";
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
  const IMPACT_LABEL = { high: "Zwaar", medium: "Middel", low: "Licht" };

  const appEl = document.getElementById("app");
  const statusEl = document.getElementById("status");
  const summaryEl = document.getElementById("summary");
  const refreshBtn = document.getElementById("refresh");
  const detailEl = document.getElementById("detail");
  const filterBtns = document.querySelectorAll(".filter");

  let current = null; // last rendered payload
  let filter = "all";

  // ---- storage (can throw in private mode) ------------------------------

  function loadCache() {
    try {
      return JSON.parse(localStorage.getItem(CACHE_KEY));
    } catch (e) {
      return null;
    }
  }

  function saveCache(data) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(data));
    } catch (e) { /* ignore */ }
  }

  try {
    if (localStorage.getItem(FILTER_KEY) === "high") filter = "high";
  } catch (e) { /* ignore */ }

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

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function formatTime(d) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function formatDue(due) {
    return `${WEEKDAYS[due.getDay()]} ${due.getDate()} ${MONTHS[due.getMonth()]}`;
  }

  function formatDueLong(due) {
    return `${WEEKDAYS_LONG[due.getDay()]} ${due.getDate()} ${MONTHS_LONG[due.getMonth()]} ${due.getFullYear()}`;
  }

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
    return { background: `hsl(${hue} 45% 28%)`, color: `hsl(${hue} 80% 85%)` };
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

  function courseChip(course) {
    const chip = el("span", "chip", course || "Overig");
    Object.assign(chip.style, chipColors(course || "Overig"));
    return chip;
  }

  function impactBadge(task) {
    if (!task.impact) return null;
    const pct = typeof task.weight === "number" ? `${task.weight}%` : "";
    const text = task.impact === "high" ? `★ Zwaar${pct ? ` · ${pct}` : ""}` : `${IMPACT_LABEL[task.impact]}${pct ? ` · ${pct}` : ""}`;
    return el("span", `impact ${task.impact}`, text);
  }

  // Put http(s) links in plain text into <a> elements, without ever using innerHTML.
  function appendLinkified(parent, text) {
    const re = /https?:\/\/[^\s<>"')]+/g;
    let last = 0;
    let match;
    while ((match = re.exec(text))) {
      const url = match[0].replace(/[.,;:!?]+$/, "");
      if (match.index > last) parent.append(text.slice(last, match.index));
      const a = el("a", null, url);
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      parent.append(a);
      last = match.index + url.length;
      re.lastIndex = last;
    }
    if (last < text.length) parent.append(text.slice(last));
  }

  // ---- detail sheet -----------------------------------------------------

  function openDetail(task, due, days, urgency) {
    const inner = el("div", "detail-inner");

    const head = el("div", "detail-head");
    head.append(courseChip(task.course));
    const close = el("button", "icon-btn close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Sluiten");
    close.addEventListener("click", () => detailEl.close());
    head.append(close);

    const title = el("h2", null, task.title);
    title.id = "detail-title";

    const when = el("p", "detail-when");
    const strong = el("strong", null, formatDueLong(due));
    when.append(strong, ` om ${formatTime(due)}`);

    const facts = el("div", "detail-facts");
    facts.append(el("span", `fact ${urgency}`, countdown(days)));
    if (task.impact) {
      const pct = typeof task.weight === "number" ? ` (${task.weight}% van je eindcijfer)` : "";
      facts.append(el("span", "fact", `${task.impact === "high" ? "★ " : ""}Gewicht: ${IMPACT_LABEL[task.impact]}${pct}`));
    }

    const heading = el("h3", null, "Wat moet je doen");
    const body = el("p", task.description ? "detail-body" : "detail-body none");
    if (task.description) appendLinkified(body, task.description);
    else body.textContent = "Geen beschrijving in de kalender. Open de opdracht in Canvas voor de details.";

    inner.append(head, title, when, facts, heading, body);
    if (task.url && /^https?:\/\//i.test(task.url)) {
      const link = el("a", "canvas-link", "Open in Canvas");
      link.href = task.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      inner.append(link);
    }

    detailEl.replaceChildren(inner);
    if (!detailEl.open) detailEl.showModal();
  }

  detailEl.addEventListener("click", (e) => {
    if (e.target === detailEl) detailEl.close(); // click on the backdrop
  });

  // ---- rendering --------------------------------------------------------

  function renderCard(task, due, days, urgency) {
    const card = el("article", `card ${urgency}`);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `${task.title}, details bekijken`);

    const top = el("div", "card-top");
    top.append(courseChip(task.course), el("span", "countdown", countdown(days)));

    const meta = el("div", "card-meta");
    meta.append(el("span", "due", `${formatDue(due)} · ${formatTime(due)}`));
    const badge = impactBadge(task);
    if (badge) meta.append(badge);

    card.append(top, el("h3", null, task.title), meta);
    if (task.description) card.append(el("p", "desc", task.description));

    const open = () => openDetail(task, due, days, urgency);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    return card;
  }

  function renderEmpty(filtered) {
    const box = el("div", "empty");
    box.append(
      el("span", "emoji", filtered ? "🔍" : "🎉"),
      el("h2", null, filtered ? "Geen zware taken op komst" : "Geen taken op komst"),
      el("p", null, filtered ? "Pas het filter aan om alle taken te zien." : "Je hebt niets te doen de komende tijd."),
    );
    return box;
  }

  function summarize(items, now) {
    const week = items.filter((i) => i.group === "this");
    const heavy = items.filter((i) => i.task.impact === "high");
    const parts = [`${week.length} ${week.length === 1 ? "taak" : "taken"} deze week`];
    if (heavy.length) parts.push(`${heavy.length} zwaar`);
    return parts.join(" · ");
  }

  function render(data) {
    current = data;
    const now = new Date();
    const thresholds = data.thresholds || { urgent: 2, high: 7 };
    const lookahead = data.lookahead_days || 14;

    const all = (data.tasks || [])
      .map((task) => {
        const due = wallClock(task.due_date);
        const days = daysUntil(due, now);
        return { task, due, days, urgency: urgencyFor(days, thresholds), group: weekGroup(due, now) };
      })
      .filter((i) => i.due >= now && i.days <= lookahead)
      .sort((a, b) => a.due - b.due);

    summaryEl.textContent = summarize(all, now);
    const items = filter === "high" ? all.filter((i) => i.task.impact === "high") : all;

    appEl.replaceChildren();
    if (!items.length) {
      appEl.append(renderEmpty(filter === "high" && all.length > 0));
      return;
    }

    for (const group of GROUPS) {
      const inGroup = items.filter((i) => i.group === group.key);
      if (!inGroup.length) continue;
      const section = el("section");
      section.append(el("h2", "group-title", group.title));
      for (const i of inGroup) section.append(renderCard(i.task, i.due, i.days, i.urgency));
      appEl.append(section);
    }
  }

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.className = isError ? "status error" : "status";
    statusEl.hidden = !message;
  }

  function formatUpdated(iso) {
    const d = wallClock(iso);
    return `${formatDue(d)} ${formatTime(d)}`;
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
      saveCache(data);
      render(data);
      showStatus("", false);
    } catch (err) {
      const cached = loadCache();
      if (cached) {
        render(cached);
        const when = cached.fetched_at ? ` Laatst bijgewerkt: ${formatUpdated(cached.fetched_at)}.` : "";
        showStatus(`${navigator.onLine ? "Kon niet vernieuwen." : "Je bent offline."}${when}`, false);
      } else {
        appEl.replaceChildren(el("div", "empty", "Kon taken niet laden."));
        showStatus(navigator.onLine ? "Kon taken niet laden." : "Je bent offline en er zijn nog geen opgeslagen gegevens.", true);
      }
    } finally {
      loading = false;
      refreshBtn.classList.remove("spinning");
    }
  }

  // ---- filter -----------------------------------------------------------

  function syncFilterButtons() {
    filterBtns.forEach((btn) => {
      const active = btn.dataset.filter === filter;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", String(active));
    });
  }

  filterBtns.forEach((btn) => btn.addEventListener("click", () => {
    filter = btn.dataset.filter;
    try {
      localStorage.setItem(FILTER_KEY, filter);
    } catch (e) { /* ignore */ }
    syncFilterButtons();
    if (current) render(current);
  }));

  // ---- pull to refresh --------------------------------------------------

  function setupPullToRefresh() {
    const ptr = document.getElementById("ptr");
    const label = document.getElementById("ptr-label");
    const THRESHOLD = 70;
    const MAX = 110;
    let startY = null;
    let pull = 0;

    function setHeight(px) {
      ptr.style.height = `${px}px`;
    }

    document.addEventListener("touchstart", (e) => {
      startY = window.scrollY <= 0 && e.touches.length === 1 && !detailEl.open ? e.touches[0].clientY : null;
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
  syncFilterButtons();

  const cached = loadCache();
  if (cached) render(cached);
  load();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js").catch(() => { /* not fatal */ });
    });
  }
})();

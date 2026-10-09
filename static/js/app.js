(() => {
  "use strict";

  const CACHE_KEY = "artevelde-tasks-cache-v1";
  const WEEKDAYS = ["zo", "ma", "di", "wo", "do", "vr", "za"];
  const MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
  const GROUPS = [
    { key: "this", title: "Deze week" },
    { key: "next", title: "Volgende week" },
    { key: "later", title: "Later" },
  ];

  const appEl = document.getElementById("app");
  const statusEl = document.getElementById("status");
  const refreshBtn = document.getElementById("refresh");

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

  function formatDue(due) {
    return `${WEEKDAYS[due.getDay()]} ${due.getDate()} ${MONTHS[due.getMonth()]}`;
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

  // ---- rendering --------------------------------------------------------

  function renderCard(task, now, thresholds) {
    const due = wallClock(task.due_date);
    const days = daysUntil(due, now);
    const card = el("article", `card ${urgencyFor(days, thresholds)}`);

    const top = el("div", "card-top");
    const chip = el("span", "chip", task.course || "Overig");
    Object.assign(chip.style, chipColors(task.course || "Overig"));
    top.append(chip, el("span", "countdown", countdown(days)));

    const title = el("h3");
    if (task.url && /^https?:\/\//i.test(task.url)) {
      const link = el("a", null, task.title);
      link.href = task.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      title.append(link);
    } else {
      title.textContent = task.title;
    }

    card.append(top, title, el("div", "due", `${formatDue(due)} · ${pad(due.getHours())}:${pad(due.getMinutes())}`));
    if (task.description) card.append(el("p", "desc", task.description));
    return card;
  }

  function renderEmpty() {
    const box = el("div", "empty");
    box.append(el("span", "emoji", "🎉"), el("h2", null, "Geen taken op komst"), el("p", null, "Je hebt niets te doen de komende tijd."));
    return box;
  }

  function render(data) {
    const now = new Date();
    const thresholds = data.thresholds || { urgent: 2, high: 7 };
    const lookahead = data.lookahead_days || 14;

    const tasks = (data.tasks || [])
      .map((t) => ({ t, due: wallClock(t.due_date) }))
      .filter(({ due }) => due >= now && daysUntil(due, now) <= lookahead)
      .sort((a, b) => a.due - b.due);

    appEl.replaceChildren();
    if (!tasks.length) {
      appEl.append(renderEmpty());
      return;
    }

    for (const group of GROUPS) {
      const items = tasks.filter(({ due }) => weekGroup(due, now) === group.key);
      if (!items.length) continue;
      const section = el("section");
      section.append(el("h2", "group-title", group.title));
      for (const { t } of items) section.append(renderCard(t, now, thresholds));
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
    return `${formatDue(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ---- data -------------------------------------------------------------

  let loading = false;

  async function load({ force = false } = {}) {
    if (loading) return;
    loading = true;
    refreshBtn.classList.add("spinning");
    try {
      const resp = await fetch(force ? "/api/refresh" : "/api/tasks", {
        method: force ? "POST" : "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      saveCache(data);
      render(data);
      showStatus(data.stale ? "De feed is tijdelijk onbereikbaar; dit zijn de laatst bekende gegevens." : "", false);
    } catch (err) {
      const cached = loadCache();
      if (cached) {
        render(cached);
        const when = cached.fetched_at ? ` Laatst bijgewerkt: ${formatUpdated(cached.fetched_at)}.` : "";
        showStatus(`${navigator.onLine ? "Kon niet vernieuwen." : "Je bent offline."}${when}`, false);
      } else {
        appEl.replaceChildren(el("div", "empty", err.message || "Kon taken niet laden."));
        showStatus(navigator.onLine ? "Kon taken niet laden." : "Je bent offline en er zijn nog geen opgeslagen gegevens.", true);
      }
    } finally {
      loading = false;
      refreshBtn.classList.remove("spinning");
    }
  }

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
      startY = window.scrollY <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
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
      if (triggered) load({ force: true });
    }

    document.addEventListener("touchend", end, { passive: true });
    document.addEventListener("touchcancel", end, { passive: true });
  }

  // ---- init -------------------------------------------------------------

  refreshBtn.addEventListener("click", () => load({ force: true }));
  window.addEventListener("online", () => load());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });
  setupPullToRefresh();

  const cached = loadCache();
  if (cached) render(cached);
  load();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => { /* not fatal */ });
    });
  }
})();

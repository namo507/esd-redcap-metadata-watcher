/* ESD Visitboard - core.js

   Shared state, the fetch helper, routing and the page chrome.
   Loaded first: everything else assumes these exist.
*/

const S = { board: null, detail: null, selected: null, status: "all", search: "",
            section: "team", assignments: {}, lastImport: null,
            syncTab: "availability", logicNode: null, dueOpen: null, batch: null };

const $ = (id) => document.getElementById(id);

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* One entry point for both modes. With the Python backend present these are
 * real HTTP calls; on the public build StaticBoard answers the same routes from
 * a snapshot the engine produced. The rendering code below does not know or
 * care which it is talking to. */

let STATIC = false;

/* The API's base URL. Empty means same origin, which is what `make serve`
   gives you; a full URL is what a separately hosted page needs. Trailing
   slashes are trimmed so "https://host/" and "https://host" behave alike. */
const API_BASE = ((window.ESD_CONFIG || {}).API_BASE || "").replace(/\/+$/, "");

function apiUrl(path) {
  return API_BASE + path;
}

async function api(path, opts) {
  if (STATIC) {
    try {
      return window.StaticBoard.route(path, opts);
    } catch (err) {
      throw new Error(err.message);
    }
  }
  const res = await fetch(apiUrl(path),
    Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;

function toast(message, bad) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast is-on" + (bad ? " is-bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 4200);
}

/* ------------------------------------------------------------------ KPIs */

function drawKpis() {
  // Three numbers, each of which changes what someone does next. "Coordinators
  // on call" never moved, and "busiest vs average" is the fairness panel's job
  // to show properly rather than compress into a ratio nobody can act on.
  const queue = S.board.queue;
  const toAssign = queue.filter((v) => v.status !== "assigned").length;
  const attention = queue.filter((v) => v.needs_attention).length;
  const items = [
    [toAssign, toAssign === 1 ? "visit still to assign" : "visits still to assign"],
    [queue.length - toAssign, "assigned so far"],
    [attention, attention === 1 ? "needs a closer look" : "need a closer look"],
  ];
  $("kpis").innerHTML = items.map(([v, k]) =>
    `<div class="kpi"><div class="kpi-value">${esc(v)}</div><div class="kpi-label">${esc(k)}</div></div>`
  ).join("");
}

/* ----------------------------------------------------------------- queue */

/* --------------------------------------------------------------- calendar */

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"];

function isoDay(iso) {
  // Parse as a local date. new Date("2026-08-19") is UTC midnight, which lands
  // on the previous day west of Greenwich and shifts the whole grid by one.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function mondayOf(date) {
  const d = new Date(date);
  const shift = (d.getDay() + 6) % 7; // Monday = 0
  d.setDate(d.getDate() - shift);
  d.setHours(0, 0, 0, 0);
  return d;
}

const SECTIONS = {
  team:   ["This week", "The team.",
           "Who is free each day, and which visits they are signed off to run."],
  assign: ["Next visit", "Assign a visit.",
           "Pick a visit. The board rules out who cannot go and suggests a clinician and a tech."],
  sync:   ["Calendars", "Calendars.",
           "Upload an Outlook print. Nothing counts until it has been checked."],
  logic:  ["The workings", "How it decides.",
           "Every step from an uploaded calendar to two named people."],
};

const SECTION_NAMES = ["team", "assign", "sync", "logic"];

/* ------------------------------------------------------------------ routing
   The board is one page, but its four views are places. Reflecting them in the
   URL is what makes the back button, a bookmark and a pasted link behave the
   way anyone expects -- including a link straight to one visit, which is how
   coordinators actually pass work to each other. */

function routeFor(section, visitId) {
  return section === "assign" && visitId
    ? `#/assign/${encodeURIComponent(visitId)}`
    : `#/${section}`;
}

function parseRoute() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [section, visitId] = raw.split("/");
  return {
    section: SECTION_NAMES.includes(section) ? section : SECTION_NAMES[0],
    visitId: visitId ? decodeURIComponent(visitId) : null,
  };
}

function syncRoute(replace) {
  const want = routeFor(S.section, S.selected);
  if (location.hash === want) return;
  if (replace) history.replaceState(null, "", want);
  else history.pushState(null, "", want);
}

async function applyRoute() {
  const { section, visitId } = parseRoute();
  if (visitId && visitId !== S.selected) {
    // A link can outlive the board it was made from. Say so and land on
    // something usable rather than leaving an empty pane and no explanation.
    const known = (S.board && S.board.queue || []).some((v) => v.id === visitId);
    if (known) {
      await selectVisit(visitId, { silent: true });
    } else {
      toast(`Visit ${visitId} is not on this board.`, true);
      const fallback = (S.board && S.board.queue || [])[0];
      if (fallback) await selectVisit(fallback.id, { silent: true });
      history.replaceState(null, "", routeFor(section, S.selected));
    }
  }
  if (section !== S.section) setSection(section, { fromRoute: true });
  else document.querySelectorAll(".navbtn").forEach((b) =>
    b.setAttribute("aria-current", b.dataset.section === section ? "page" : "false"));
}

function setSection(name, opts) {
  const fromRoute = opts && opts.fromRoute;
  S.section = name;
  document.querySelectorAll(".navbtn").forEach((b) => {
    const on = b.dataset.section === name;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-current", on ? "page" : "false");
  });
  SECTION_NAMES.forEach((k) => {
    $("sec-" + k).hidden = k !== name;
  });
  const [eyebrow, title, sub] = SECTIONS[name];
  $("hero-eyebrow").textContent = eyebrow;
  $("hero-title").textContent = title;
  $("hero-sub").textContent = sub;
  if (name === "team") { drawTeam(); drawDue(); }
  if (name === "sync") { drawSync(); drawData(); drawReadTable(); }
  if (name === "logic") { drawLogic(); drawSettings(); }
  if (!fromRoute) syncRoute(false);
}

/* ---------------------------------------------------------------- detail */

/* --------------------------------------------------------------- actions */

/* -------------------------------------------------------------- fairness */

function prettyBlock(startIso, endIso) {
  try {
    const a = new Date(startIso);
    const b = new Date(endIso);
    const day = a.toLocaleDateString(undefined,
      { weekday: "short", month: "short", day: "numeric" });
    const fmt = (d) => d.toLocaleTimeString(undefined,
      { hour: "numeric", minute: "2-digit" });
    return `${day}, ${fmt(a)} – ${fmt(b)}`;
  } catch (e) {
    return `${startIso} – ${endIso}`;
  }
}

function csv(rows) {
  return rows.map((r) => r.map((cell) => {
    const v = cell == null ? "" : String(cell);
    return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }).join(",")).join("\n");
}

function download(name, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------------- logic
   A picture of the decision procedure, drawn from the live configuration
   rather than kept in step by hand. Nodes are buttons: the diagram stays
   readable at a glance, and the detail is one tap away instead of crowding it. */

function nameFor(id) {
  const r = (S.board.roster || []).find((x) => x.id === id);
  return r ? r.name : id;
}

function drawSyncBadge() {
  const h = S.board.health;
  const mins = h.last_synced_minutes;
  const fresh = mins <= 15 ? "fresh" : mins <= 60 ? "stale" : "old";
  const label = mins < 1 ? "just now"
    : mins < 60 ? `${mins} min ago`
    : `${Math.round(mins / 60)} h ago`;
  const el = $("sync-badge");
  el.className = `sync-badge is-${fresh}`;
  // The board's own clock, not the browser's. If the two disagree the figure
  // beside it is measured against the server's, and showing the browser's would
  // quietly explain a stale number with the wrong time.
  const clock = S.board.health.server_time
    ? new Date(S.board.health.server_time).toLocaleString(undefined,
        { weekday: "short", day: "numeric", month: "short",
          hour: "numeric", minute: "2-digit" })
    : "";
  el.innerHTML = `<i></i><b>${esc(clock)}</b><span class="long"> &middot; calendars ${esc(label)}</span>`;
  el.title = `Board clock: ${esc(clock)}. Availability evidence last refreshed ${esc(label)}.`;
}

/* ---------------------------------------------------------------- live sync
   The board is a live operational view, so it refreshes itself. Redraw keeps
   the current section and the selected visit: a page that jumps back to the
   top every minute is worse than a stale one. */

function drawFooter() {
  const h = S.board.health;
  $("foot-meta").innerHTML =
    `Engine ${esc(h.engine_version)} &middot; weights ${esc(h.weight_vector_id)}
     (${esc(h.config_fingerprint)}) &middot; week of ${esc(h.week_of)}<br>
     Demonstration data. Delegated free/busy only; event titles are never requested.`;
}

/* ----------------------------------------------------------------- boot */

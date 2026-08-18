/* ESD Visitboard front end.
 *
 * No framework, no build step. One fetch of /api/board draws the whole screen,
 * one fetch of /api/visit draws the detail. Every judgement shown here comes
 * from the engine; this file only renders it.
 */
"use strict";

const S = { board: null, detail: null, selected: null, status: "all", search: "", view: "list" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* One entry point for both modes. With the Python backend present these are
 * real HTTP calls; on the public build StaticBoard answers the same routes from
 * a snapshot the engine produced. The rendering code below does not know or
 * care which it is talking to. */
let STATIC = false;

async function api(path, opts) {
  if (STATIC) {
    try {
      return window.StaticBoard.route(path, opts);
    } catch (err) {
      throw new Error(err.message);
    }
  }
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
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

function visibleQueue() {
  const q = S.search.trim().toLowerCase();
  return S.board.queue.filter((v) => {
    if (S.status !== "all" && v.status !== S.status) return false;
    if (!q) return true;
    return (v.family_label + " " + v.id + " " + v.title).toLowerCase().includes(q);
  });
}

function drawQueue() {
  const rows = visibleQueue();
  const total = S.board.queue.length;
  $("queue-count").textContent =
    rows.length === total ? `${total} visits` : `${rows.length} of ${total} visits`;

  $("queue").innerHTML = rows.length ? rows.map((v) => {
    const done = v.status === "assigned";
    const tag = done
      ? (v.provisional ? '<span class="tag tag-prov">Needs confirming</span>'
                       : '<span class="tag tag-done">Assigned</span>')
      : '<span class="tag tag-todo">To assign</span>';
    return `<li><button class="qitem ${v.id === S.selected ? "is-on" : ""} ${done ? "is-done" : ""}"
        type="button" data-visit="${esc(v.id)}">
      <span class="qitem-top"><span class="qitem-day">${esc(v.day_label)}</span>${tag}</span>
      <span class="qitem-fam">${esc(v.family_label)}</span>
      <span class="qitem-meta">${esc(v.title)}</span>
      ${done ? `<span class="qitem-who">${esc(v.assigned_to)}</span>` : ""}
    </button></li>`;
  }).join("") : `<li class="note" style="padding:1rem">No visits match that filter.
    <button class="btn btn-quiet" type="button" id="clear-filters">Clear filters</button></li>`;

  $("queue").querySelectorAll("[data-visit]").forEach((b) =>
    b.addEventListener("click", () => selectVisit(b.dataset.visit)));
  if (S.view === "calendar") drawCalendar();
  const clear = $("clear-filters");
  if (clear) clear.addEventListener("click", () => {
    S.search = ""; S.status = "all"; $("filter-search").value = "";
    $("filter-status").querySelectorAll(".chip").forEach((c) =>
      c.classList.toggle("is-on", c.dataset.status === "all"));
    drawQueue();
  });
}


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

function drawCalendar() {
  const visits = visibleQueue();
  if (!visits.length) {
    $("calendar").innerHTML = '<p class="note" style="padding:1rem">No visits match that filter.</p>';
    $("calendar-title").textContent = "Nothing to show";
    $("calendar-note").textContent = "";
    return;
  }

  // Group by ISO date, then lay the weeks out Monday to Friday. Home visits are
  // weekday work, so a weekend column would be five empty cells every row.
  const byDay = {};
  visits.forEach((v) => { (byDay[v.date] = byDay[v.date] || []).push(v); });

  const days = Object.keys(byDay).sort();
  const first = mondayOf(isoDay(days[0]));
  const last = mondayOf(isoDay(days[days.length - 1]));
  const weeks = [];
  for (let w = new Date(first); w <= last; w.setDate(w.getDate() + 7)) {
    weeks.push(new Date(w));
  }

  const today = new Date(); today.setHours(0, 0, 0, 0);

  const head = `<div class="cal-row cal-head">${DAY_NAMES.map((d) =>
    `<div class="cal-hcell">${d}</div>`).join("")}</div>`;

  const body = weeks.map((monday) => {
    const cells = DAY_NAMES.map((_, i) => {
      const day = new Date(monday); day.setDate(day.getDate() + i);
      const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
      const items = byDay[iso] || [];
      const isToday = day.getTime() === today.getTime();
      const cards = items.map((v) => {
        const state = v.status === "assigned" ? "done" : v.needs_attention ? "attn" : "todo";
        const who = v.status === "assigned" ? esc(v.assigned_to) : "Needs someone";
        return `<button class="cal-card cal-${state} ${v.id === S.selected ? "is-on" : ""}"
            type="button" data-visit="${esc(v.id)}"
            title="${esc(v.family_label)} — ${esc(v.title)}">
          <span class="cal-fam">${esc(v.family_label)}</span>
          <span class="cal-meta">${esc(v.title)}</span>
          <span class="cal-who">${who}</span>
        </button>`;
      }).join("");
      return `<div class="cal-cell ${isToday ? "is-today" : ""} ${items.length ? "" : "is-empty"}">
        <div class="cal-date">${day.getDate()} ${day.toLocaleString("en-GB", { month: "short" })}</div>
        ${cards}
      </div>`;
    }).join("");
    return `<div class="cal-row">${cells}</div>`;
  }).join("");

  $("calendar").innerHTML = head + body;
  $("calendar").querySelectorAll("[data-visit]").forEach((b) =>
    b.addEventListener("click", () => selectVisit(b.dataset.visit)));

  const unassigned = visits.filter((v) => v.status !== "assigned").length;
  $("calendar-title").textContent =
    `${visits.length} visit${visits.length === 1 ? "" : "s"} across ${weeks.length} week${weeks.length === 1 ? "" : "s"}`;
  $("calendar-note").innerHTML = unassigned
    ? `<b>${unassigned}</b> still need${unassigned === 1 ? "s" : ""} someone. Pick a day to open it.`
    : "Every visit on the board has someone assigned.";
}

function setView(view) {
  S.view = view;
  document.querySelectorAll(".viewbtn").forEach((b) =>
    b.classList.toggle("is-on", b.dataset.view === view));
  $("calendar-card").hidden = view !== "calendar";
  if (view === "calendar") drawCalendar();
}

/* ---------------------------------------------------------------- detail */

function contributionBar(c) {
  // The fill is the score on its true 0-to-1 scale, not normalised to the
  // widest option. A pool where nobody scores well must LOOK like one; a bar
  // stretched to 100% would hide exactly the case worth noticing.
  const fill = Math.max(0, Math.min(1, c.score)) * 100;
  const total = c.contributions.reduce((s, x) => s + Math.max(0, x.contribution), 0);
  const segs = c.contributions.map((x) => {
    const pct = total > 0 ? (Math.max(0, x.contribution) / total) * 100 : 0;
    return pct < 0.5 ? "" : `<span class="seg-${x.key}" style="width:${pct.toFixed(1)}%"
      title="${esc(x.label)} contributed ${x.contribution.toFixed(3)}"></span>`;
  }).join("");
  // Every criterion is listed, including the ones that scored nothing. A
  // missing row reads as "not considered"; a zero reads as "considered, earned
  // nothing", which is the true statement.
  // Words, not decimals. "0.19" means nothing to a reader; the bar already
  // shows how much each reason contributed, and greying the ones that earned
  // nothing keeps them visible as considered-and-not-met rather than dropping
  // them, which would read as never considered. The number stays in the title
  // for anyone auditing.
  const legend = c.contributions
    .slice()
    .sort((a, b) => b.contribution - a.contribution)
    .map((x) => `<span class="${x.contribution > 0.001 ? "" : "is-zero"}"
      title="${esc(x.help)} Contributed ${x.contribution.toFixed(2)} of ${c.score.toFixed(2)}."
      >${x.contribution > 0.001 ? `<i class="seg-${x.key}"></i>` : ""}${esc(x.label)}</span>`)
    .join("");
  // No 0-to-1 score on the face of the card. The bar shows how much of a
  // possible fit was earned and the legend names what earned it; the number
  // itself is in the tooltip for anyone who wants to audit it.
  return `<div class="bar" title="Fit ${c.score.toFixed(2)} of a possible 1.00">
      <div class="bar-fill" style="width:${fill.toFixed(1)}%">${segs}</div>
    </div>
    <div class="legend">${legend}</div>`;
}

function whyLine(c) {
  const bits = [];
  if (c.prior_visits > 0) {
    bits.push(`has seen this family <b>${c.prior_visits} time${c.prior_visits > 1 ? "s" : ""}</b>`);
  } else {
    bits.push("has not met this family");
  }
  if (c.did_previous_checkpoint) bits.push("ran the <b>previous checkpoint</b>");
  bits.push(`<b>${Math.round(c.utilization * 100)}%</b> of their week is committed`);
  bits.push(`<b>${c.travel_minutes} min</b> round trip`);
  return bits.join(" &middot; ");
}

function candidateCard(c, canAssign, recommendedId) {
  // "Best match" belongs to the person the board would actually send. If a
  // fairness veto blocks rank 1, the next assignable candidate carries the
  // primary action and says so, rather than hiding behind "choose instead".
  const best = c.id === recommendedId;
  const demoted = c.rank === 1 && !c.assignable;
  return `<article class="cand ${best ? "is-best" : ""} ${c.assignable ? "" : "is-blocked"}">
    <div class="cand-top">
      <div class="avatar">${esc(c.initials)}</div>
      <div>
        <div class="cand-name">${esc(c.name)}</div>
        <div class="cand-sub">${c.slot ? "Free " + esc(c.slot) : "Availability unverified"}</div>
      </div>
      <div class="cand-right">
        <span class="rank-pill">${best ? (c.rank === 1 ? "Best match" : "Best available") : "Option " + c.rank}</span>
        <div class="confidence">${esc(c.confidence)}</div>
      </div>
    </div>
    ${contributionBar(c)}
    <p class="why">Strongest reason: <b>${esc(c.leads_on)}</b> &middot; ${whyLine(c)}</p>
    ${c.blocked_by.length ? `<p class="blocked-line">Cannot be assigned: ${esc(c.blocked_by.join("; "))}${demoted ? ". The next person down carries the recommendation." : ""}</p>` : ""}
    ${canAssign && c.assignable
      ? `<div class="assign-row" style="margin-top:.8rem">
           <button class="btn ${best ? "" : "btn-ghost"}" type="button"
             data-assign="${esc(c.id)}" data-recommended="${best ? "1" : "0"}"
             data-name="${esc(c.name)}">
             ${best ? "Assign " + esc(c.name) : "Choose " + esc(c.name) + " instead"}
           </button>
         </div>` : ""}
  </article>`;
}

function drawDetail() {
  const d = S.detail;
  if (!d) { $("detail").innerHTML = '<div class="card empty">Pick a visit from the queue.</div>'; return; }
  const v = d.visit;
  const assigned = d.assigned;

  const notices = d.notices.map((n) =>
    `<div class="notice notice-${n.tone === "alert" ? "alert" : "warn"}">
       <span>${n.tone === "alert" ? "&#9888;" : "&#9432;"}</span><span>${esc(n.message)}</span>
     </div>`).join("");

  const excluded = d.excluded.length ? `<details class="excluded">
      <summary>Not available for this visit (${d.excluded.length})</summary>
      <div class="exlist">${d.excluded.map((e) =>
        `<div class="exrow"><span>${esc(e.name)}</span><span>${esc(e.reason)}</span></div>`).join("")}</div>
    </details>` : "";

  const assignBlock = assigned
    ? `<div class="assign"><div class="assigned-banner">
         <span>&#10003; ${esc(assigned.coordinator_name)} is assigned${assigned.slot ? " &middot; " + esc(assigned.slot) : ""}${assigned.override ? " (chosen over the top suggestion)" : ""}${assigned.provisional ? " &middot; confirm in Outlook before telling the family" : ""}</span>
         <button class="btn btn-quiet" type="button" id="btn-undo">Undo</button>
       </div></div>`
    : (d.candidates.length
        ? `<div class="assign">
             <div id="reason-slot"></div>
             <p class="note">Choosing anyone other than the best match records a reason.
               That is what separates "the data was wrong" from "I disagreed", and it is
               what the weights are re-checked against.</p>
           </div>`
        : `<div class="assign"><p class="blocker">Nobody is eligible. This visit needs manual scheduling.</p></div>`);

  $("detail").innerHTML = `
    <div class="card">
      <div class="visit-head">
        <div>
          <p class="eyebrow">${esc(v.id)} &middot; ${esc(v.protocol)}</p>
          <h2>${esc(v.family_label)} &middot; ${esc(v.title)}</h2>
          <p class="note" style="margin-top:.3rem">Book anywhere in ${esc(v.window)}</p>
        </div>
        <span class="pill">${esc(d.family_preference)}</span>
      </div>
      <div class="facts">
        <div class="fact"><div class="fact-k">Visit length</div><div class="fact-v">${v.duration_hours} h</div></div>
        <div class="fact"><div class="fact-k">Where</div><div class="fact-v">Home visit</div></div>
        <div class="fact"><div class="fact-k">Who can go</div><div class="fact-v">${d.candidates.length} of ${d.candidates.length + d.excluded.length}</div></div>
      </div>
      ${notices}
    </div>

    <div class="card" style="margin-top:1.3rem">
      <div class="card-head"><div>
        <p class="eyebrow">Who the board would send</p>
        <h2>${d.close_call ? "Close call &mdash; two good options" : "Ranked by fit"}</h2>
      </div></div>
      <div class="cands">${d.candidates.map((c) => candidateCard(c, !assigned, d.recommended_id)).join("")
        || '<p class="note">Nobody passed the eligibility checks.</p>'}</div>
      ${excluded}
      ${assignBlock}
    </div>`;

  $("detail").querySelectorAll("[data-assign]").forEach((b) =>
    b.addEventListener("click", () =>
      onAssignClick(b.dataset.assign, b.dataset.recommended === "1", b.dataset.name)));
  const undo = $("btn-undo");
  if (undo) undo.addEventListener("click", () => unassign(v.id));
}

/* --------------------------------------------------------------- actions */

function onAssignClick(coordinatorId, isRecommended, name) {
  if (isRecommended) { doAssign(coordinatorId, null, null); return; }
  const slot = $("reason-slot");
  if (!slot) return;
  const codes = S.board.reason_codes;
  slot.innerHTML = `
    <p class="eyebrow" style="margin-top:.2rem">Why ${esc(name)} instead?</p>
    <div class="assign-row">
      <select class="select" id="reason-code" aria-label="Reason">
        <option value="">Pick a reason&hellip;</option>
        ${codes.map((r) => `<option value="${esc(r.code)}">${esc(r.label)}</option>`).join("")}
      </select>
      <input class="input" id="reason-text" style="max-width:260px" placeholder="Anything to add (optional)">
      <button class="btn" type="button" id="reason-go">Confirm ${esc(name)}</button>
      <button class="btn btn-quiet" type="button" id="reason-cancel">Cancel</button>
    </div>`;
  slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("reason-go").addEventListener("click", () => {
    const code = $("reason-code").value;
    if (!code) { toast("Pick a reason first.", true); return; }
    doAssign(coordinatorId, code, $("reason-text").value.trim());
  });
  $("reason-cancel").addEventListener("click", () => { slot.innerHTML = ""; });
}

async function doAssign(coordinatorId, reasonCode, reasonText) {
  try {
    const out = await api("/api/assign", {
      method: "POST",
      body: JSON.stringify({
        visit_id: S.selected, coordinator_id: coordinatorId,
        reason_code: reasonCode, reason_text: reasonText,
      }),
    });
    toast(`${out.assignment.coordinator_name} assigned.`);
    await refresh();
    await selectVisit(S.selected);
  } catch (err) { toast(err.message, true); }
}

async function unassign(visitId) {
  try {
    await api("/api/unassign", { method: "POST", body: JSON.stringify({ visit_id: visitId }) });
    toast("Assignment undone.");
    await refresh();
    await selectVisit(visitId);
  } catch (err) { toast(err.message, true); }
}

async function selectVisit(visitId) {
  S.selected = visitId;
  drawQueue();
  try {
    S.detail = await api("/api/visit?id=" + encodeURIComponent(visitId));
  } catch (err) { toast(err.message, true); S.detail = null; }
  drawDetail();
}

/* -------------------------------------------------------------- fairness */

function drawFairness() {
  const f = S.board.fairness;
  const max = Math.max(0.35, ...f.rows.map((r) => r.utilization));
  $("fairness").innerHTML = f.rows.map((r) => {
    const pct = Math.min(100, (r.utilization / max) * 100);
    const cls = r.utilization > 0.85 ? "hot" : r.utilization < 0.15 ? "idle" : "";
    return `<div class="frow">
      <div class="frow-name">${esc(r.name)}</div>
      <div class="ftrack"><div class="ffill ${cls}" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="fval">${r.visits} visit${r.visits === 1 ? "" : "s"} &middot; ${r.hours} of ${r.capacity} h</div>
    </div>`;
  }).join("");

  const label = { even: "Evenly spread", uneven: "Somewhat uneven", lopsided: "Lopsided" }[f.status];
  const pill = $("fairness-status");
  pill.textContent = label;
  pill.className = "pill" + (f.status === "uneven" ? " warn" : f.status === "lopsided" ? " bad" : "");

  $("fairness-note").innerHTML = f.permutation_p >= 0.10
    ? `The busiest person carries <b>${f.imbalance.toFixed(2)}×</b> the average.
       A shuffle test puts that inside what eligibility alone would produce
       (p&nbsp;=&nbsp;${f.permutation_p.toFixed(2)}), so this is about who is qualified and free,
       not about the ranking.`
    : `The busiest person carries <b>${f.imbalance.toFixed(2)}×</b> the average.
       A shuffle test says eligibility alone does not explain that
       (p&nbsp;=&nbsp;${f.permutation_p.toFixed(2)}). Worth a look.`;
}

function drawActivity() {
  const rows = S.board.activity;
  $("activity").innerHTML = rows.length
    ? rows.map((a) => `<li class="arow"><span class="atime">${esc(a.at)}</span><span class="amsg">${esc(a.message)}</span></li>`).join("")
    : '<li class="note">Nothing yet.</li>';
}

function drawFooter() {
  const h = S.board.health;
  $("foot-meta").innerHTML =
    `Engine ${esc(h.engine_version)} &middot; weights ${esc(h.weight_vector_id)}
     (${esc(h.config_fingerprint)}) &middot; week of ${esc(h.week_of)}<br>
     Demonstration data. Delegated free/busy only; event titles are never requested.`;
}

/* ----------------------------------------------------------------- boot */

async function refresh() {
  S.board = await api("/api/board");
  drawKpis(); drawQueue(); drawFairness(); drawActivity(); drawFooter();
}

async function detectMode() {
  // A backend answering /api/health means the live board; anything else means
  // the static build. Deciding by probe rather than by build flag keeps one
  // copy of the frontend for both.
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    if (res.ok) return false;
  } catch (err) { /* no backend here */ }
  await window.StaticBoard.load();
  return true;
}

async function boot() {
  STATIC = await detectMode();
  if (STATIC) document.body.classList.add("is-static");
  $("filter-search").addEventListener("input", (e) => { S.search = e.target.value; drawQueue(); });
  $("filter-status").querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      S.status = chip.dataset.status;
      $("filter-status").querySelectorAll(".chip").forEach((c) => c.classList.remove("is-on"));
      chip.classList.add("is-on");
      drawQueue();
    }));
  document.querySelectorAll(".viewbtn").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));
  $("btn-reset").addEventListener("click", async () => {
    if (!confirm("Reset the board? This clears the assignments made in this session.")) return;
    await api("/api/reset", { method: "POST" });
    await refresh();
    const first = S.board.queue[0];
    if (first) await selectVisit(first.id);
    toast("Board reset.");
  });

  try {
    await refresh();
    const firstOpen = S.board.queue.find((v) => v.status !== "assigned") || S.board.queue[0];
    if (firstOpen) await selectVisit(firstOpen.id);
  } catch (err) {
    $("detail").innerHTML = `<div class="card empty">Could not reach the board.<br>
      <span class="note">${esc(err.message)}</span></div>`;
  }
}

document.addEventListener("DOMContentLoaded", boot);

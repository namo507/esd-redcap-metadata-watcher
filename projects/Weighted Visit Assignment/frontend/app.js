/* ESD Visitboard front end.
 *
 * No framework, no build step. One fetch of /api/board draws the whole screen,
 * one fetch of /api/visit draws the detail. Every judgement shown here comes
 * from the engine; this file only renders it.
 */
"use strict";

const S = { board: null, detail: null, selected: null, status: "all", search: "",
            section: "week", assignments: {}, lastImport: null };

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
  if (S.section === "week") drawCalendar();
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

const DUE_TONE = {
  overdue: "attn", closing: "attn", open: "todo",
  upcoming: "route", unknown: "route", complete: "done",
};

function drawDue() {
  const sched = S.board.schedule;
  const card = $("due-card");
  if (!sched || !sched.rows || !sched.rows.length) { card.hidden = true; return; }
  card.hidden = false;

  const counts = sched.counts || {};
  $("due-key").innerHTML = ["overdue", "closing", "open", "upcoming", "unknown"]
    .filter((k) => counts[k])
    .map((k) => `<span><i class="dot dot-${DUE_TONE[k]}"></i>${counts[k]} ${esc(k)}</span>`)
    .join("");

  const rows = sched.rows.filter((r) => r.status !== "complete");
  $("due-list").innerHTML = `
    <div class="tablewrap"><table class="tbl">
      <thead><tr>
        <th>Family</th><th>Study</th><th>Next checkpoint</th><th>Status</th>
        <th>Window closes</th><th>Days</th><th>Pressure</th>
      </tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td>Family ${esc(String(r.family_id).replace(/^F/, ""))}</td>
        <td>${esc(r.protocol)}</td>
        <td>${esc(r.checkpoint || "\u2014")}</td>
        <td><span class="statchip is-${
          r.status === "overdue" || r.status === "closing" ? "fail"
          : r.status === "open" ? "pass" : "skip"}">${esc(r.status_label)}</span></td>
        <td>${r.window_end ? esc(r.window_end) : "\u2014"}</td>
        <td class="${r.status === "overdue" ? "over" : ""}">${
          r.days_remaining == null ? "\u2014"
          : r.status === "overdue" ? `${Math.abs(r.days_remaining)} late`
          : r.days_remaining}</td>
        <td>${urgencyBar(r.urgency)}</td>
      </tr>`).join("")}</tbody>
    </table></div>
    ${sched.confirmed ? "" : `<div class="notice notice-warn" style="margin-top:.9rem">
      <span>&#9432;</span><span>These dates are <b>provisional</b>. The offsets were
      read off the checkpoint names and the windows are a placeholder, because the
      study's real acceptance windows are not recorded anywhere in this repo.
      Confirm them in <code>config/protocol-schedule.json</code> and every date here
      becomes authoritative.</span></div>`}`;
}

function urgencyBar(u) {
  const pct = Math.round(Math.max(0, Math.min(1, u || 0)) * 100);
  return `<span class="ubar" title="${pct}% of the window used">
    <i style="width:${pct}%"></i></span>`;
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
        const state = !v.automated ? "route"
          : v.status === "assigned" ? "done"
          : v.needs_attention ? "attn" : "todo";
        const who = !v.automated
          ? (v.route === "remote_24_month" ? "Remote questionnaire" : "Assigned by hand")
          : v.status === "assigned" ? esc(v.assigned_to) : "Needs someone";
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
    b.addEventListener("click", async () => {
      await selectVisit(b.dataset.visit, { silent: true });
      setSection("assign");
    }));

  const unassigned = visits.filter((v) => v.status !== "assigned").length;
  $("calendar-title").textContent =
    `${visits.length} visit${visits.length === 1 ? "" : "s"} across ${weeks.length} week${weeks.length === 1 ? "" : "s"}`;
  $("calendar-note").innerHTML = unassigned
    ? `<b>${unassigned}</b> still need${unassigned === 1 ? "s" : ""} someone. Pick a day to open it.`
    : "Every visit on the board has someone assigned.";
}

const SECTIONS = {
  week:     ["The week", "Plan the week.", "Every visit on the board, on the day it falls. Colour tells you what still needs a decision."],
  assign:   ["Next visit assignment", "Assign a visit.", "Pick a visit. The board checks every calendar, rules out who cannot go, and explains who it would send."],
  workload: ["Fair shares", "How the work is spread.", "Visits, hours, out-of-hours duty and van trips across the team."],
  sync:     ["Evidence", "Sync calendars.", "Upload the Outlook print. Nothing counts until a human has confirmed it."],
  data:     ["Records", "Data and exports.", "Take the audit trail with you, or bring the roster in."],
};

const SECTION_NAMES = ["week", "assign", "workload", "sync", "data"];

/* ------------------------------------------------------------------ routing
   The board is one page, but its five views are places. Reflecting them in the
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
    section: SECTION_NAMES.includes(section) ? section : "week",
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
  ["week", "assign", "workload", "sync", "data"].forEach((k) => {
    $("sec-" + k).hidden = k !== name;
  });
  const [eyebrow, title, sub] = SECTIONS[name];
  $("hero-eyebrow").textContent = eyebrow;
  $("hero-title").textContent = title;
  $("hero-sub").textContent = sub;
  if (name === "week") { drawDue(); drawCalendar(); }
  if (name === "workload") { drawFairness(); drawEquity(); drawActivity(); }
  if (name === "sync") drawSync();
  if (name === "data") drawData();
  if (!fromRoute) syncRoute(false);
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

function filterChips(v) {
  const rows = (v.filters || []).filter((f) => f.state !== "not_applicable");
  const skipped = (v.filters || []).filter((f) => f.state === "not_applicable");
  if (!rows.length && !skipped.length) return "";
  return `<div class="chiprow">
    ${rows.map((f) => `<span class="statchip is-${esc(f.state)}" title="${esc(f.why)}">
      ${esc(f.label)}: ${f.state === "pass" ? "ok" : "no"}</span>`).join("")}
    ${skipped.map((f) => `<span class="statchip is-skip" title="${esc(f.why)}">
      ${esc(f.label)}: n/a</span>`).join("")}
  </div>`;
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
    <p class="reason">${esc(c.reason || "")}</p>
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

  // Some visits never enter the ranking pipeline at all. Showing a ranked list
  // for them would invite a decision the board is not entitled to make.
  if (!v.automated) {
    $("detail").innerHTML = `<div class="card">
      <p class="eyebrow">${esc(v.id)} &middot; ${esc(v.protocol)}</p>
      <h2>${esc(v.family_label)} &middot; ${esc(v.title)}</h2>
      <div class="notice notice-${v.escalate ? "alert" : "warn"}" style="margin-top:1rem">
        <span>${v.escalate ? "&#9888;" : "&#9432;"}</span><span>${esc(v.route_reason || "")}</span>
      </div>
      <div class="facts">
        <div class="fact"><div class="fact-k">Contact by</div><div class="fact-v">${esc(v.preferred_contact_method)}</div></div>
        <div class="fact"><div class="fact-k">Window</div><div class="fact-v">${esc(v.window)}</div></div>
      </div>
      ${v.scheduling_notes ? `<div class="notes"><b>Note from the family record:</b> ${esc(v.scheduling_notes)}</div>` : ""}
    </div>`;
    return;
  }
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
      ${v.scheduling_notes ? `<div class="notes"><b>Note from the family record:</b> ${esc(v.scheduling_notes)}</div>` : ""}
      <div class="facts">
        <div class="fact"><div class="fact-k">Contact by</div><div class="fact-v">${esc(v.preferred_contact_method)}</div></div>
        <div class="fact"><div class="fact-k">Visit length</div><div class="fact-v">${v.duration_hours} h${v.is_ndd ? " <span class=\"tag tag-ndd\">NDD +60m</span>" : ""}</div></div>
        <div class="fact"><div class="fact-k">Drive</div><div class="fact-v">${v.drive_time_minutes} min</div></div>
        <div class="fact"><div class="fact-k">Where</div><div class="fact-v">${
          v.location === "home" ? "Home visit"
          : v.location === "remote" ? "Remote" : "In the lab"}${
          v.requires_clinician ? " <span class=\"tag tag-ndd\">clinician</span>" : ""}</div></div>
        <div class="fact"><div class="fact-k">Who can go</div><div class="fact-v">${d.candidates.length} of ${d.candidates.length + d.excluded.length}</div></div>
      </div>
      ${filterChips(v)}
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
    S.assignments[S.selected] = out.assignment;
    toast(`${out.assignment.coordinator_name} assigned.`);
    await refresh();
    await selectVisit(S.selected);
  } catch (err) { toast(err.message, true); }
}

async function unassign(visitId) {
  try {
    await api("/api/unassign", { method: "POST", body: JSON.stringify({ visit_id: visitId }) });
    delete S.assignments[visitId];
    toast("Assignment undone.");
    await refresh();
    await selectVisit(visitId);
  } catch (err) { toast(err.message, true); }
}

async function selectVisit(visitId, opts) {
  const silent = opts && opts.silent;
  S.selected = visitId;
  drawQueue();
  try {
    S.detail = await api("/api/visit?id=" + encodeURIComponent(visitId));
  } catch (err) {
    if (!silent) toast(err.message, true);
    S.detail = null;
  }
  drawDetail();
  if (!silent && S.section === "assign") syncRoute(false);
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

function drawEquity() {
  const rows = S.board.roster;
  const avgOoh = rows.reduce((s, r) => s + (r.out_of_hours || 0), 0) / (rows.length || 1);
  $("equity").innerHTML = `
    <thead><tr><th>Coordinator</th><th>Visits this week</th><th>Out-of-hours</th>
      <th>Van trained</th><th>Tech trained</th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td>${esc(r.name)}</td>
      <td>${r.visits_this_week}</td>
      <td class="${(r.out_of_hours || 0) > avgOoh ? "over" : ""}">${r.out_of_hours || 0}${(r.out_of_hours || 0) > avgOoh ? " <span class=\"flag\">above average</span>" : ""}</td>
      <td>${r.van_trained ? "Yes" : "&mdash;"}</td>
      <td>${r.tech_trained ? "Yes" : "&mdash;"}</td>
    </tr>`).join("")}</tbody>`;
}

const TIER_WORD = {
  1: "Live Outlook feed",
  2: "Timed export",
  3: "Month grid",
};

function drawSync() {
  drawSyncRoster();
  drawUpload();
  drawImportResult();
  drawAbsences();
  drawFilters();
  drawAvailability();
  drawColorMatch();
  drawReviewQueue();
  drawImportHistory();
}

const AVAIL_WORD = {
  busy: "Spoken for", light: "Some commitments", open: "Clear", unknown: "Not visible",
};
const DAY_INITIAL = ["M", "T", "W", "T", "F", "S", "S"];

const FILTER_ICON = { offered_window: "\u25F7", clinician_shift: "\u271A", lab_space: "\u25A3" };

function drawAbsences() {
  const cal = S.board.calendar || {};
  const rows = cal.unavailable || [];
  const unresolved = cal.unresolved_names || [];
  const card = $("sync-absence-card");
  if (!rows.length && !unresolved.length) { card.hidden = true; return; }
  card.hidden = false;

  const byDay = {};
  rows.forEach((r) => { (byDay[r.day] = byDay[r.day] || []).push(r); });

  $("sync-absence").innerHTML = `
    ${Object.keys(byDay).sort().length ? `<div class="absencelist">${
      Object.keys(byDay).sort().map((day) => {
        const dt = new Date(day + "T00:00:00");
        return `<div class="absencerow">
          <div class="absenceday">${dt.toLocaleDateString(undefined,
            { weekday: "short", month: "short", day: "numeric" })}</div>
          <div class="absencewho">${byDay[day].map((r) =>
            `<span class="statchip is-fail">${esc(r.name)}</span>`).join("")}</div>
        </div>`;
      }).join("")}</div>` : ""}
    ${unresolved.length ? `<div class="notice notice-alert" style="margin-top:1rem">
      <span>&#9888;</span><span>
      ${unresolved.map((u) => `<b>${esc(u.name)}</b> (${u.days.length} day${
        u.days.length === 1 ? "" : "s"})`).join(", ")}
      could not be matched to anyone on the roster, so <b>those days are not
      blocked</b>. Guessing which person a nickname means would take someone off
      the board who is actually free. Add the name to
      <code>config/calendar-roles.json</code> under <code>name_aliases</code>.
      </span></div>` : ""}`;
}

function drawFilters() {
  const cal = S.board.calendar || {};
  const filters = cal.filters || [];
  const card = $("sync-filters-card");
  if (!filters.length) { card.hidden = true; return; }
  card.hidden = false;

  const roles = (cal.roles || []).filter((r) => r.role !== "coordinator");
  $("sync-filters").innerHTML = `
    <div class="filtergrid">${filters.map((f) => `
      <div class="filtercard is-${f.active ? "on" : "off"}">
        <div class="filterhead">
          <span class="filtericon">${FILTER_ICON[f.role] || "\u25CF"}</span>
          <div>
            <div class="cand-name">${esc(f.label)}</div>
            <div class="cand-sub">${f.polarity === "positive"
              ? "Says when a visit may happen" : "Says when the room is taken"}</div>
          </div>
        </div>
        <div class="filterstate">${f.active
          ? `<b>${f.windows}</b> window${f.windows === 1 ? "" : "s"}, ${f.hours} h`
          : "Nothing in the uploaded range"}</div>
        <div class="filterwhy">${esc(f.meaning)}</div>
      </div>`).join("")}</div>
    ${roles.length ? `<p class="note" style="margin-top:.9rem">
      Read from this export: ${roles.map((r) =>
        `<b>${esc(r.label)}</b> &rarr; ${esc(r.role_label)}`).join(" &middot; ")}</p>` : ""}`;
}

function drawAvailability() {
  const cal = S.board.calendar || {};
  const rows = (cal.availability || []).filter((a) => a.coordinator_id);
  const card = $("sync-availability-card");
  if (!rows.length) { card.hidden = true; return; }
  card.hidden = false;
  $("avail-title").textContent = cal.availability_month
    ? `Who is free in ${cal.availability_month}` : "Who is free this month";

  const days = rows[0].days || [];
  const anyUnknown = rows.some((r) => r.unknown_days > 0);

  const head = days.map((d) => {
    const dt = new Date(d.day + "T00:00:00");
    return `<th class="availhead${d.weekday > 4 ? " is-weekend" : ""}"
      title="${esc(d.day)}"><span>${DAY_INITIAL[d.weekday]}</span><b>${dt.getDate()}</b></th>`;
  }).join("");

  const body = rows.map((r) => `<tr>
      <th class="availname"><span class="swatch swatch-${esc(r.hue || "")}"></span>${esc(r.name)}</th>
      ${r.days.map((d) => `<td class="availcell is-${esc(d.state)}${d.weekday > 4 ? " is-weekend" : ""}"
          title="${esc(r.name)} — ${esc(d.day)}: ${esc(AVAIL_WORD[d.state] || d.state)}${
            d.items ? ", " + d.items + " item" + (d.items === 1 ? "" : "s") : ""
          }${d.truncated ? " (day cell was cut off)" : ""}"></td>`).join("")}
      <td class="availcount"><b>${r.open_working_days}</b></td>
    </tr>`).join("");

  $("sync-availability").innerHTML = `
    <div class="tablewrap"><table class="availtable">
      <thead><tr><th class="availname"></th>${head}<th class="availcount">Clear<br>weekdays</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <div class="availlegend">
      <span><i class="availkey is-open"></i>Clear</span>
      <span><i class="availkey is-light"></i>Some commitments</span>
      <span><i class="availkey is-busy"></i>Spoken for</span>
      <span><i class="availkey is-unknown"></i>Not visible</span>
    </div>
    ${anyUnknown ? `<p class="note" style="margin-top:.6rem">
      <b>Not visible</b> means that day's cell hit the month grid's row limit, so
      anything further down was cut off the page. It is not the same as free.</p>` : ""}`;
}

function drawSyncRoster() {
  $("syncgrid").innerHTML = S.board.roster.map((r) => {
    const mins = r.evidence_age_minutes;
    const state = mins == null ? "none" : mins <= 15 ? "fresh" : mins <= 60 ? "stale" : "old";
    const label = mins == null ? "No evidence yet"
      : mins < 60 ? `Synced ${mins} min ago` : `Synced ${Math.round(mins / 60)} h ago`;
    return `<div class="synccard is-${state}">
      <div class="synchead"><span class="avatar">${esc(r.initials)}</span>
        <div><div class="cand-name">${esc(r.name)}</div>
        <div class="cand-sub">${esc(label)}</div></div></div>
      <div class="syncstate">${state === "none"
        ? "Counts as <b>no evidence</b> — this person cannot be assigned until a calendar is synced."
        : state === "old" ? "Too old to trust. Re-sync before assigning."
        : state === "stale" ? "Usable, but any assignment stays provisional."
        : "Current."}</div>
      <div class="syncmeta">${r.blocks_reviewed || 0} of ${r.blocks_total || 0} detected blocks confirmed</div>
    </div>`;
  }).join("");
  $("sync-capture").innerHTML = "";
}

function drawUpload() {
  const box = $("sync-upload");
  if (STATIC) {
    box.innerHTML = `<div class="notice notice-warn"><span>&#9432;</span><span>
      Uploading runs the PDF reader on the machine hosting the board, so it works
      when you run <code>make serve</code> in the lab, not on this public copy.
      Everything below shows the evidence state the gates actually read.</span></div>`;
    return;
  }
  box.innerHTML = `
    <div class="uploadzone" id="dropzone">
      <input id="pdf-file" type="file" accept="application/pdf,.pdf" hidden>
      <div class="uploadzone-inner">
        <div class="uploadzone-title">Drop the Outlook PDF here</div>
        <div class="uploadzone-sub">or</div>
        <label class="btn btn-primary" for="pdf-file">Choose a PDF&hellip;</label>
        <div class="uploadzone-hint">Work Week or Day view gives schedulable times. Month view gives workload only.</div>
      </div>
    </div>`;

  const input = $("pdf-file");
  const zone = $("dropzone");
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) uploadPdf(input.files[0]);
  });
  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("is-over");
    }));
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("is-over");
    }));
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadPdf(file);
  });
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => {
      const out = String(reader.result || "");
      resolve(out.includes(",") ? out.split(",")[1] : out);
    };
    reader.readAsDataURL(file);
  });
}

async function uploadPdf(file) {
  const zone = $("dropzone");
  if (zone) zone.classList.add("is-busy");
  $("sync-result").innerHTML =
    `<div class="notice"><span>&#8987;</span><span>Reading ${esc(file.name)}&hellip;</span></div>`;
  try {
    const data = await readAsBase64(file);
    const out = await api("/api/calendar/upload", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, data }),
    });
    S.lastImport = out;
    await refresh();
    setSection("sync");
    toast(out.schedulable
      ? `Read ${out.block_count} blocks. Confirm them below.`
      : `Read ${out.entry_count} entries as a workload signal only.`,
      !out.schedulable);
  } catch (err) {
    $("sync-result").innerHTML =
      `<div class="notice notice-alert"><span>&#9888;</span><span>${esc(err.message)}</span></div>`;
    toast(err.message, true);
  } finally {
    if (zone) zone.classList.remove("is-busy");
  }
}

function drawImportResult() {
  const box = $("sync-result");
  if (!box) return;
  const imp = S.lastImport || (S.board.calendar && S.board.calendar.last_import);
  if (!imp) { box.innerHTML = ""; return; }

  const tier = imp.tier;
  const cls = tier === 2 ? "ok" : "warn";
  box.innerHTML = `
    <div class="importcard is-${cls}">
      <div class="importhead">
        <div>
          <div class="import-file">${esc(imp.source_file)}</div>
          <div class="cand-sub">${esc(imp.date_range || "date range not printed")}</div>
        </div>
        <span class="tierbadge is-t${tier}">${esc(TIER_WORD[tier] || "Unknown")}</span>
      </div>
      <div class="importstats">
        <div><b>${imp.entry_count}</b><span>entries read</span></div>
        <div><b>${imp.block_count}</b><span>bookable blocks</span></div>
        <div><b>${imp.pending_review}</b><span>awaiting review</span></div>
      </div>
      <div class="importverdict">${tier === 2
        ? "This export has real start and end times, so once you confirm the blocks below the board will schedule around them."
        : "This is a month grid. It shows how loaded each day looks, but it cannot say when anyone is free. <b>Re-print the same dates as Work Week</b> to make it schedulable."}</div>
      ${(imp.blockers || []).length ? `<ul class="importlist">${
        imp.blockers.map((b) => `<li class="is-blocker">${esc(b)}</li>`).join("")
      }</ul>` : ""}
      ${(imp.notes || []).length ? `<ul class="importlist">${
        imp.notes.map((n) => `<li>${esc(n)}</li>`).join("")
      }</ul>` : ""}
    </div>`;
}

function drawColorMatch() {
  const cal = S.board.calendar || {};
  const cmap = cal.color_map || {};
  const hues = Object.keys(cmap.hues_seen || {}).filter((h) => h !== "unknown");
  const card = $("sync-colors-card");
  const last = cal.last_import || {};
  // Outlook prints its own legend, so hand-matching is only for exports where
  // reading it failed. Showing it otherwise invites someone to override a fact
  // with a guess.
  if (STATIC || !hues.length || last.attribution_source === "legend") {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  const roster = cmap.roster || [];
  const rows = hues.map((hue) => {
    const chosen = (cmap.map || {})[hue] || "";
    return `<div class="colorrow">
      <span class="swatch swatch-${esc(hue)}" title="${esc(hue)}"></span>
      <div class="colorname">${esc(hue)}<span class="cand-sub">${cmap.hues_seen[hue]} ${cmap.hues_seen[hue] === 1 ? "entry" : "entries"}</span></div>
      <select class="select colorpick" data-hue="${esc(hue)}">
        <option value="">Not matched yet</option>
        ${roster.map((r) => `<option value="${esc(r.coordinator_id)}"${
          r.coordinator_id === chosen ? " selected" : ""
        }>${esc(r.name)}</option>`).join("")}
      </select>
    </div>`;
  }).join("");

  $("sync-colors").innerHTML = `
    ${cmap.confirmed ? `<div class="notice"><span>&#10003;</span><span>
      Confirmed by ${esc(cmap.confirmed_by || "someone")}${
        cmap.confirmed_at ? " on " + esc(String(cmap.confirmed_at).slice(0, 10)) : ""
      }. Change it below if the colours move.</span></div>`
      : `<div class="notice notice-warn"><span>&#9888;</span><span>
      Not confirmed yet, so no upload is attributed to anyone.</span></div>`}
    <div class="colorlist">${rows}</div>
    <div class="assign-row" style="margin-top:1rem">
      <input class="select" id="color-by" placeholder="Your name" style="min-width:16ch"
             value="${esc(cmap.confirmed_by || "")}">
      <button class="btn btn-primary" id="color-save" type="button">Confirm colours</button>
      <span class="note">The PDF cannot check this, so it is recorded against your name.</span>
    </div>`;

  $("color-save").addEventListener("click", saveColors);
}

async function saveColors() {
  const map = {};
  document.querySelectorAll(".colorpick").forEach((el) => {
    if (el.value) map[el.dataset.hue] = el.value;
  });
  const by = ($("color-by").value || "").trim();
  try {
    await api("/api/calendar/colors", {
      method: "POST",
      body: JSON.stringify({ map, confirmed_by: by }),
    });
    await refresh();
    setSection("sync");
    toast("Colours confirmed. Upload the PDF again to attribute it.");
  } catch (err) {
    toast(err.message, true);
  }
}

function drawReviewQueue() {
  const cal = S.board.calendar || {};
  const pending = cal.pending_review || [];
  const applied = cal.applied || [];
  const card = $("sync-review-card");
  if (STATIC || (!pending.length && !applied.length)) { card.hidden = true; return; }
  card.hidden = false;

  const row = (b, inEffect) => `
      <div class="reviewrow" data-block="${esc(b.block_id)}">
        <div>
          <div class="cand-name">${esc(b.coordinator)}</div>
          <div class="cand-sub">${esc(prettyBlock(b.start, b.end))}</div>
        </div>
        <div class="reviewacts">
          ${inEffect ? `<span class="inforce">In effect</span>` : ""}
          <button class="btn btn-ghost" data-act="reject" type="button">Not real</button>
          ${inEffect ? "" : `<button class="btn btn-primary" data-act="confirm" type="button">Confirm</button>`}
        </div>
      </div>`;

  $("sync-review").innerHTML = `
    <div class="reviewlist">
      ${pending.slice(0, 30).map((b) => row(b, false)).join("")}
      ${applied.slice(0, 30).map((b) => row(b, true)).join("")}
    </div>`;

  document.querySelectorAll(".reviewrow .btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".reviewrow");
      reviewBlock(row.dataset.block, btn.dataset.act === "confirm");
    });
  });
}

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

async function reviewBlock(blockId, confirmed) {
  try {
    await api("/api/calendar/review", {
      method: "POST",
      body: JSON.stringify({
        block_id: blockId,
        confirmed,
        reviewer: (($("color-by") || {}).value || "coordinator").trim(),
      }),
    });
    await refresh();
    setSection("sync");
  } catch (err) {
    toast(err.message, true);
  }
}

function drawImportHistory() {
  const cal = S.board.calendar || {};
  const rows = cal.imports || [];
  const card = $("sync-history-card");
  if (!rows.length) { card.hidden = true; return; }
  card.hidden = false;
  $("sync-history").innerHTML = `
    <div class="tablewrap"><table class="tbl">
      <thead><tr><th>Uploaded</th><th>File</th><th>View</th>
      <th>Entries</th><th>Blocks</th><th>Can schedule</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td>${esc(String(r.uploaded_at || "").replace("T", " ").slice(0, 16))}</td>
        <td>${esc(r.source_file)}</td>
        <td>${esc(TIER_WORD[r.tier] || r.view_type)}</td>
        <td>${r.entry_count}</td>
        <td>${r.block_count}</td>
        <td>${r.schedulable ? "Yes" : "No — workload only"}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
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

const EXPORTS = [
  ["assignments", "Assignment audit",
   "Every decision this board made: who was chosen, the reason string, the evidence used, and whether a hard rule overrode the ranking.",
   () => {
     const rows = [["visit_id", "family", "checkpoint", "protocol", "coordinator",
                    "was_override", "reason_code", "reason", "evidence", "route"]];
     S.board.queue.forEach((v) => {
       const a = v.assigned_id ? S.assignments[v.id] : null;
       rows.push([v.id, v.family_label, v.checkpoint, v.protocol,
                  v.assigned_to || "", v.was_override ? "yes" : "no",
                  (a && a.reason_code) || "", (a && a.reason) || "",
                  v.automated ? "ranked" : "routed", v.route || ""]);
     });
     return rows;
   }],
  ["next-visits", "Next visits due",
   "Every family's next checkpoint, its window, and how much of that window is left — the protocol clock as a file.",
   () => {
     const rows = [["family", "protocol", "next_checkpoint", "status",
                    "target_date", "window_start", "window_end",
                    "days_remaining", "pressure", "completed", "total",
                    "schedule_confirmed"]];
     ((S.board.schedule && S.board.schedule.rows) || []).forEach((r) => {
       rows.push([r.family_id, r.protocol, r.checkpoint || "", r.status,
                  r.target_date || "", r.window_start || "", r.window_end || "",
                  r.days_remaining == null ? "" : r.days_remaining,
                  r.urgency, r.completed, r.total,
                  r.confirmed_schedule ? "yes" : "provisional"]);
     });
     return rows;
   }],
  ["workload", "Workload and equity",
   "Visits, committed hours, out-of-hours tally and van training per coordinator — the periodic fairness review as a file.",
   () => {
     const rows = [["coordinator", "visits_this_week", "committed_hours",
                    "capacity_hours", "utilisation", "out_of_hours",
                    "van_trained", "tech_trained"]];
     S.board.fairness.rows.forEach((r) => {
       const p = S.board.roster.find((x) => x.id === r.id) || {};
       rows.push([r.name, r.visits, r.hours, r.capacity, r.utilization.toFixed(3),
                  p.out_of_hours || 0, p.van_trained ? "yes" : "no",
                  p.tech_trained ? "yes" : "no"]);
     });
     return rows;
   }],
  ["evidence", "Calendar evidence",
   "Per coordinator: how old the evidence is, how many detected blocks a human confirmed, and the resulting correction rate.",
   () => {
     const rows = [["coordinator", "evidence_age_minutes", "blocks_detected",
                    "blocks_confirmed", "correction_rate"]];
     S.board.roster.forEach((r) => {
       const total = r.blocks_total || 0, ok = r.blocks_reviewed || 0;
       rows.push([r.name, r.evidence_age_minutes == null ? "" : r.evidence_age_minutes,
                  total, ok, total ? ((total - ok) / total).toFixed(3) : ""]);
     });
     return rows;
   }],
];

function everythingReport() {
  /* One self-contained file rather than four downloads.

     A browser will not let a page hand over a zip without a library, and it
     blocks a burst of separate downloads as a popup. A single HTML document
     sidesteps both: it opens in a browser, Excel imports it as a table, and it
     carries the provenance -- which board, which clock, which caveats -- that a
     bare CSV loses the moment it is emailed on. */
  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
  const sched = S.board.schedule || {};
  const cal = S.board.calendar || {};

  const table = (rows) => `<table><thead><tr>${
    rows[0].map((h) => `<th>${esc(String(h).replace(/_/g, " "))}</th>`).join("")
  }</tr></thead><tbody>${
    rows.slice(1).map((r) => `<tr>${
      r.map((c) => `<td>${esc(String(c == null ? "" : c))}</td>`).join("")
    }</tr>`).join("")
  }</tbody></table>`;

  const sections = EXPORTS.map(([key, title, blurb, build]) => {
    let rows;
    try { rows = build(); } catch (e) { rows = [["error"], [e.message]]; }
    return `<section><h2>${esc(title)}</h2><p>${esc(blurb)}</p>${
      rows.length > 1 ? table(rows) : "<p><i>Nothing recorded.</i></p>"}</section>`;
  }).join("");

  const caveats = [];
  if (sched.confirmed === false) {
    caveats.push("Checkpoint due dates are <b>provisional</b>: the offsets were read "
      + "off the checkpoint names and the windows are a placeholder.");
  }
  if ((cal.unresolved_names || []).length) {
    caveats.push("Some absence notices could not be matched to a coordinator, so "
      + "those days are <b>not blocked</b>: "
      + cal.unresolved_names.map((u) => esc(u.name)).join(", ") + ".");
  }
  if (!STATIC) caveats.push("Exported from a live board; figures move as it is used.");
  caveats.push("Coordinator names are the real roster; every schedule, credential "
    + "and workload figure is synthetic demonstration data.");

  return `<!doctype html><html><head><meta charset="utf-8">
<title>ESD Visitboard export ${esc(stamp)}</title>
<style>
 body{font-family:'Libre Franklin',-apple-system,sans-serif;margin:2rem;color:#14141A;max-width:70rem}
 h1{font-size:1.6rem;margin:0 0 .2rem} h2{font-size:1.1rem;margin:2rem 0 .3rem}
 p{color:#55555F;font-size:.9rem;margin:.2rem 0 .8rem}
 table{border-collapse:collapse;width:100%;font-size:.82rem;margin-bottom:1rem}
 th{background:#3366FF;color:#fff;text-align:left;padding:.4rem .55rem;font-size:.7rem;
    text-transform:uppercase;letter-spacing:.04em}
 td{padding:.35rem .55rem;border-bottom:1px solid #DCE4F6}
 tr:nth-child(even) td{background:#F4F4F6}
 .caveats{background:#FFF8DC;color:#4A3B00;padding:.9rem 1.1rem;border-radius:12px;font-size:.85rem}
 .caveats li{margin-bottom:.35rem}
</style></head><body>
<h1>ESD Visitboard export</h1>
<p>${esc(stamp)} &middot; engine ${esc((S.board.health && S.board.health.engine_version) || "")}
 &middot; weights ${esc((S.board.health && S.board.health.weight_vector_id) || "")}</p>
<div class="caveats"><b>Read this first</b><ul>${
  caveats.map((c) => `<li>${c}</li>`).join("")}</ul></div>
${sections}
</body></html>`;
}

function drawData() {
  const stamp = new Date().toISOString().slice(0, 10);
  $("exports").innerHTML = EXPORTS.map(([key, title, blurb]) =>
    `<div class="exportcard">
       <h3>${esc(title)}</h3><p class="note">${esc(blurb)}</p>
       <button class="btn" type="button" data-export="${esc(key)}">Download CSV</button>
     </div>`).join("");
  $("exports").insertAdjacentHTML("afterbegin", `
    <div class="exportcard is-primary">
      <h3>Everything, one file</h3>
      <p class="note">Every table on this board in a single document, with the
        caveats attached so they travel with the numbers. Opens in a browser;
        Excel imports it directly.</p>
      <button class="btn btn-primary" type="button" id="export-all">Download report</button>
    </div>`);
  $("export-all").addEventListener("click", () => {
    download(`esd-visitboard-report-${stamp}.html`, everythingReport());
    toast("Full report downloaded.");
  });

  $("exports").querySelectorAll("[data-export]").forEach((b) =>
    b.addEventListener("click", () => {
      const spec = EXPORTS.find((e) => e[0] === b.dataset.export);
      download(`esd-visitboard-${spec[0]}-${stamp}.csv`, csv(spec[3]()));
      toast(`${spec[1]} downloaded.`);
    }));

  $("uploads").innerHTML = `
    <div class="exportcard">
      <h3>Visit roster (CSV)</h3>
      <p class="note">Matching the Access export columns already in use, so the lab
        can move off Access a slice at a time. Rows with an unreadable ideal date
        are rejected; a missing drive time is flagged, not rejected, because it
        only affects the offer window.</p>
      <button class="btn btn-ghost" type="button" disabled>Needs the local board</button>
    </div>
    <div class="exportcard">
      <h3>Reliability matrix</h3>
      <p class="note">Who is signed off on which assessment. Editing this file is
        how training gets recorded — no code change. Currently
        <b>${S.board.health.reliability_matrix_confirmed ? "confirmed" : "unconfirmed"}</b>,
        so reliability gates are not being enforced yet.</p>
      <button class="btn btn-ghost" type="button" disabled>Needs the local board</button>
    </div>`;
}

function drawActivity() {
  const rows = S.board.activity;
  $("activity").innerHTML = rows.length
    ? rows.map((a) => `<li class="arow"><span class="atime">${esc(a.at)}</span><span class="amsg">${esc(a.message)}</span></li>`).join("")
    : '<li class="note">Nothing yet.</li>';
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
  el.innerHTML = `<i></i>Calendars synced ${esc(label)}`;
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
  drawKpis(); drawQueue(); drawFairness(); drawActivity(); drawFooter(); drawSyncBadge();
  if (S.section === "week") drawCalendar();
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
  document.querySelectorAll(".navbtn").forEach((b) =>
    b.addEventListener("click", () => setSection(b.dataset.section)));
  $("btn-reset").addEventListener("click", async () => {
    if (!confirm("Reset the board? This clears the assignments made in this session.")) return;
    await api("/api/reset", { method: "POST" });
    S.assignments = {};
    await refresh();
    const first = S.board.queue[0];
    if (first) await selectVisit(first.id);
    toast("Board reset.");
  });

  window.addEventListener("popstate", () => { applyRoute(); });
  // Safari fires hashchange without popstate for a programmatic hash write.
  window.addEventListener("hashchange", () => { applyRoute(); });

  try {
    await refresh();
    const route = parseRoute();
    const known = S.board.queue.some((v) => v.id === route.visitId);
    const firstOpen = S.board.queue.find((v) => v.status !== "assigned")
      || S.board.queue[0];
    // A link to a visit that no longer exists should land somewhere sensible
    // rather than on an error, so fall back to the first open one.
    const target = known ? route.visitId : (firstOpen && firstOpen.id);
    if (target) await selectVisit(target, { silent: true });
    setSection(route.section, { fromRoute: true });
    syncRoute(true);
    if (route.visitId && !known) {
      toast(`Visit ${route.visitId} is not on this board.`, true);
    }
  } catch (err) {
    $("detail").innerHTML = `<div class="card empty">Could not reach the board.<br>
      <span class="note">${esc(err.message)}</span></div>`;
  }
}

document.addEventListener("DOMContentLoaded", boot);

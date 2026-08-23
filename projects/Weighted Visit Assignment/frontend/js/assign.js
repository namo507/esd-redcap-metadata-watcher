/* ESD Visitboard - assign.js

   Picking a visit and staffing it with a clinician and a tech.
*/

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
    rows.length === total ? `${total} ${total === 1 ? "visit" : "visits"}`
                          : `${rows.length} of ${total} visits`;

  $("queue").innerHTML = rows.length ? rows.map((v) => {
    const done = v.status === "assigned";
    const tag = done
      ? (v.provisional ? '<span class="tag tag-prov">Needs confirming</span>'
                       : '<span class="tag tag-done">Assigned</span>')
      : '<span class="tag tag-todo">To assign</span>';
    // One word about the protocol window, only when it is actually pressing.
    // A badge on every row would be noise; a badge on the late ones is why the
    // row is at the top.
    const due = !done && (v.due_status === "overdue" || v.due_status === "closing")
      ? `<span class="qitem-due is-${esc(v.due_status)}">${
          v.due_status === "overdue"
            ? `${Math.abs(v.days_remaining || 0)}d late`
            : `${v.days_remaining}d left`}</span>`
      : "";
    return `<li><button class="qitem ${v.id === S.selected ? "is-on" : ""} ${done ? "is-done" : ""}"
        type="button" data-visit="${esc(v.id)}">
      <span class="qitem-top"><span class="qitem-day">${esc(v.day_label)}</span>${tag}</span>
      <span class="qitem-fam">${esc(v.family_label)}</span>
      <span class="qitem-meta">${esc(v.title)}${due}</span>
      ${done ? `<span class="qitem-who">${esc(v.assigned_to)}</span>` : ""}
    </button></li>`;
  }).join("") : `<li class="note" style="padding:1rem">No visits match that filter.
    <button class="btn btn-quiet" type="button" id="clear-filters">Clear filters</button></li>`;

  $("queue").querySelectorAll("[data-visit]").forEach((b) =>
    b.addEventListener("click", () => selectVisit(b.dataset.visit)));
  if (S.section === "team") drawTeam();
  const clear = $("clear-filters");
  if (clear) clear.addEventListener("click", () => {
    S.search = ""; S.status = "all"; $("filter-search").value = "";
    $("filter-status").querySelectorAll(".chip").forEach((c) =>
      c.classList.toggle("is-on", c.dataset.status === "all"));
    drawQueue();
  });
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

function pairSection(d, assigned) {
  /* The manual staffs a visit with two people -- one clinician, one tech -- so
     the pair is the thing being chosen. The individual ranking is still there,
     folded away, because it explains how a pair got its score. */
  const pairs = d.pairs || [];
  const problems = d.pair_problems || [];
  if (!pairs.length) {
    return `<div class="card" style="margin-top:1.3rem">
      <div class="card-head"><div><p class="eyebrow">Staffing</p>
        <h2>No pair can cover this visit</h2></div></div>
      ${problems.map((x) => `<div class="notice notice-warn"><span>&#9888;</span>
        <span>${esc(x)}</span></div>`).join("")
        || `<p class="note">Nobody eligible is free alongside somebody else.</p>`}
    </div>`;
  }
  const best = pairs[0];
  return `<div class="card" style="margin-top:1.3rem">
    <div class="card-head"><div>
      <p class="eyebrow">Staffing (one clinician, one tech)</p>
      <h2>Who to send</h2>
    </div><span class="note">${pairs.length} workable pairing${pairs.length === 1 ? "" : "s"}</span></div>
    ${problems.map((x) => `<div class="notice notice-warn"><span>&#9888;</span>
      <span>${esc(x)}</span></div>`).join("")}
    <div class="pairlist">${pairs.slice(0, 6).map((p, i) => `
      <div class="pairrow ${i === 0 ? "is-best" : ""}">
        <div class="pairwho">
          <span class="pairrole">Clinician</span><b>${esc(p.clinician)}</b>
          <span class="pairrole">Tech</span><b>${esc(p.tech)}</b>
        </div>
        <div class="pairmeta">
          <span data-tip="Earliest slot both are free">${esc(p.slot || "none")}</span>
          ${p.van_capable ? '<span class="statchip is-pass" data-tip="Someone on this pair can drive the van">van</span>' : ""}
          <span class="statchip is-skip" data-tip="Combined score across the four criteria">${p.score.toFixed(3)}</span>
        </div>
        ${assigned ? "" : `<button class="btn ${i === 0 ? "btn-primary" : "btn-ghost"}"
           type="button" data-pair="${esc(p.clinician_id)}|${esc(p.tech_id)}">Send</button>`}
      </div>`).join("")}</div>
  </div>`;
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

    ${pairSection(d, assigned)}

    <details class="card details-card" style="margin-top:1.3rem">
      <summary><b>${d.close_call ? "Close call : two good options" : "Individual ranking"}</b>
        <span class="note"> . How each person scored on their own</span></summary>
      <div class="cands" style="margin-top:1rem">${d.candidates.map((c) => candidateCard(c, !assigned, d.recommended_id)).join("")
        || '<p class="note">Nobody passed the eligibility checks.</p>'}</div>
      ${excluded}
    </details>
    ${assignBlock}
    </div>`;

  $("detail").querySelectorAll("[data-assign]").forEach((b) =>
    b.addEventListener("click", () =>
      onAssignClick(b.dataset.assign, b.dataset.recommended === "1", b.dataset.name)));
  const undo = $("btn-undo");
  if (undo) undo.addEventListener("click", () => unassign(v.id));
}

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

/* ------------------------------------------------------------ adding a visit

   A board running on real data has to get its visits from somewhere. This is
   that somewhere: the smallest form that produces a schedulable visit, folded
   away under the queue because entering one is a weekly act and reading the
   queue is not. */

function drawModeNote() {
  const note = $("mode-note");
  if (!note) return;
  const live = S.board && S.board.health && S.board.health.mode === "live";
  const add = $("add-visit");
  if (add) add.hidden = false;
  if (!live) return;
  const src = (S.board.health.calendar_source || "none");
  note.innerHTML = src === "upload"
    ? `<b>Live.</b> Visits and availability on this page come from what you
       entered and the calendars you uploaded.`
    : `<b>Live.</b> No calendar has been read yet, so nobody counts as free.
       Upload a work week print under Calendars to start.`;
}

async function addVisit(form) {
  const msg = $("add-visit-msg");
  const data = Object.fromEntries(new FormData(form).entries());
  // A date input gives a bare day; the engine schedules against times, so the
  // window runs from the start of the first day to the end of the last.
  const body = {
    family_id: data.family_id,
    protocol: data.protocol,
    checkpoint: data.checkpoint,
    window_start: `${data.window_start}T09:00:00`,
    window_end: `${data.window_end}T17:00:00`,
    duration_hours: Number(data.duration_hours) || 2,
  };
  if (data.anchor_date) body.anchor_date = data.anchor_date;
  if (data.completed_through) body.completed_through = data.completed_through.trim();
  msg.textContent = "Adding…";
  try {
    const out = await api("/api/visits", {
      method: "POST", body: JSON.stringify(body),
    });
    await refresh();
    await selectVisit(out.visit.visit_id, { silent: true });
    form.reset();
    $("add-visit").open = false;
    msg.textContent = "";
    toast(`Visit ${out.visit.visit_id} added for ${out.visit.family_id}.`);
  } catch (err) {
    msg.textContent = err.message;
  }
}

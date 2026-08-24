/* ESD Visitboard - team.js

   The team view. One row per coordinator, one column per day.
*/

const DUE_TONE = {
  overdue: "attn", closing: "attn", open: "todo",
  upcoming: "route", unknown: "route", complete: "done",
};

function drawTeam() {
  /* One row per person, one column per day. The point of the view is
     comparison, so everything is on the same scale and lines up down the
     column: a scheduler is looking across the team for a gap, not reading one
     person's card at a time. */
  const t = S.board.coordinators;
  const box = $("team-table");
  if (!t || !t.rows || !t.rows.length) { box.innerHTML = ""; return; }

  $("team-key").innerHTML = `
    <span><i class="dot dot-done"></i>free</span>
    <span><i class="dot dot-todo"></i>busy or not checked</span>`;

  const head = t.days.map((d, i) =>
    `<th class="teamday" title="${esc(t.dates[i])}">${esc(d)}</th>`).join("");

  box.innerHTML = `
    <div class="tablewrap"><table class="teamtable">
      <thead><tr>
        <th class="teamwho">Coordinator</th>
        ${head}
        <th class="teamnum">Free hours</th>
        <th class="teamnum">Visits</th>
        <th class="teamcan">Can run</th>
      </tr></thead>
      <tbody>${t.rows.map((r, rowIndex) => `
        <tr>
          <th class="teamwho">
            <span class="avatar">${esc(r.initials)}</span>
            <span>
              <b>${esc(r.name)}</b>
              <span class="teamtags">
                ${(r.roles || []).map((role) => `<span class="statchip ${
                    role === "clinician" ? "is-pass" : "is-skip"}">${esc(role)}</span>`).join(" ")}
                ${r.solo_range ? `<span class="statchip is-pass"
                   data-tip="The visit ages this person can run on their own, from the manual's Visits Can Do Solo column">${esc(r.solo_range)}</span>` : ""}
                ${r.confirm_first ? '<span class="statchip" data-tip="Check with them before offering a visit on their time">ask first</span>' : ""}
                ${r.van_trained ? '<span class="statchip is-skip" data-tip="Can drive the van">van</span>' : ""}
                ${r.calendar_ok ? "" : '<span class="statchip is-fail" data-tip="No calendar uploaded yet">no calendar</span>'}
              </span>
            </span>
          </th>
          ${r.days.map((d, dayIndex) => `
            <td class="teamcell">
              <span class="strip" style="--delay:${(rowIndex * 5 + dayIndex) * 22}ms">
                ${d.slots.map((sl) => `<i class="hourbar is-${esc(sl.state)}"
                   data-tip="${esc(r.name)} ${esc(d.label)} ${esc(sl.label)}: ${
                     sl.state === "free" ? "free" : "not available"}"></i>`).join("")}
              </span>
              <span class="teamfree">${d.free}h</span>
            </td>`).join("")}
          <td class="teamnum"><b>${r.free_hours}</b></td>
          <td class="teamnum">${r.visits_this_week}</td>
          <td class="teamcan">${r.can_run.length
            ? r.can_run.map((a) => `<span class="statchip is-pass">${esc(a)}</span>`).join(" ")
            : '<span class="note">nothing signed off yet</span>'}
            ${r.learning.length
              ? `<div class="teamlearn">learning ${r.learning.map(esc).join(", ")}</div>`
              : ""}</td>
        </tr>`).join("")}</tbody>
    </table></div>`;
}

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

  /* Tiles, not a table. A seven-column row per family made the reader scan a
     grid of numbers to find the two that matter -- who is late, and by how
     much. A tile leads with those and keeps the rest one tap away. */
  const rows = sched.rows.filter((r) => r.status !== "complete");
  $("due-list").innerHTML = `
    <div class="tiles">${rows.map((r) => {
      const late = r.status === "overdue";
      const num = r.days_remaining == null ? "none"
        : late ? Math.abs(r.days_remaining) : r.days_remaining;
      const unit = r.days_remaining == null ? "no anchor date"
        : late ? (Math.abs(r.days_remaining) === 1 ? "day late" : "days late")
        : (r.days_remaining === 1 ? "day left" : "days left");
      const tip = r.window_end
        ? `${r.protocol} ${r.checkpoint} \u00B7 window ${r.window_start} to ${r.window_end}`
        : `${r.protocol} ${r.checkpoint} \u00B7 no anchor date recorded, so no due date`;
      return `<button class="tile is-${esc(r.status)} ${S.dueOpen === r.family_id ? "is-open" : ""}"
          type="button" data-due="${esc(r.family_id)}"
          data-tip="${esc(tip)}" aria-expanded="${S.dueOpen === r.family_id}">
        <span class="tile-top">
          <span class="tile-dot"></span>
          <span class="tile-study">${esc(r.protocol)}</span>
          <span class="tile-cp">${esc(r.checkpoint || "none")}</span>
        </span>
        <span class="tile-num">${num}</span>
        <span class="tile-unit">${esc(unit)}</span>
        <span class="tile-fam">Family ${esc(String(r.family_id).replace(/^F/, ""))}</span>
        <span class="tile-bar"><i style="width:${Math.round((r.urgency || 0) * 100)}%"></i></span>
      </button>`;
    }).join("")}</div>
    <div id="due-open"></div>
    ${sched.confirmed ? "" : `<p class="note" style="margin-top:.9rem">
      Dates are <b>provisional</b> until the study's real checkpoint windows are
      recorded in <code>config/protocol-schedule.json</code>.</p>`}`;

  $("due-list").querySelectorAll("[data-due]").forEach((b) =>
    b.addEventListener("click", () => {
      S.dueOpen = S.dueOpen === b.dataset.due ? null : b.dataset.due;
      drawDue();
    }));
  drawDueOpen(rows);
}

function drawDueOpen(rows) {
  const box = $("due-open");
  if (!box) return;
  const r = rows.find((x) => x.family_id === S.dueOpen);
  if (!r) { box.innerHTML = ""; return; }
  const visits = S.board.queue.filter((v) => v.family_id === r.family_id);
  box.innerHTML = `
    <div class="tile-detail">
      <div class="tile-detail-head">
        <h3>Family ${esc(String(r.family_id).replace(/^F/, ""))}</h3>
        <span class="statchip is-${r.status === "overdue" || r.status === "closing"
          ? "fail" : r.status === "open" ? "pass" : "skip"}">${esc(r.status_label)}</span>
      </div>
      <div class="facts">
        <div class="fact"><div class="fact-k">Study</div><div class="fact-v">${esc(r.protocol)}</div></div>
        <div class="fact"><div class="fact-k">Next checkpoint</div><div class="fact-v">${esc(r.checkpoint || "none")}</div></div>
        <div class="fact"><div class="fact-k">Window</div><div class="fact-v">${
          r.window_start ? esc(r.window_start) + " to " + esc(r.window_end) : "none"}</div></div>
        <div class="fact"><div class="fact-k">Progress</div><div class="fact-v">${r.completed} of ${r.total} done</div></div>
      </div>
      ${visits.length ? `<div class="tile-links">${visits.map((v) =>
        `<button class="btn btn-quiet" type="button" data-goto="${esc(v.id)}">
           Open ${esc(v.title)} &rarr;</button>`).join("")}</div>`
        : `<p class="note">No visit is on the board for this family yet.</p>`}
    </div>`;
  box.querySelectorAll("[data-goto]").forEach((b) =>
    b.addEventListener("click", async () => {
      await selectVisit(b.dataset.goto, { silent: true });
      setSection("assign");
    }));
}

function urgencyBar(u) {
  const pct = Math.round(Math.max(0, Math.min(1, u || 0)) * 100);
  return `<span class="ubar" title="${pct}% of the window used">
    <i style="width:${pct}%"></i></span>`;
}

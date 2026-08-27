/* ESD Visitboard - calendars.js

   Uploading calendars, reading what came back, and exports.
*/

/* -----------------------------------------------------------------------
   SCREEN C  --  UPLOAD A CALENDAR
  
     before  somebody printed an Outlook week
     here    the upload, what came back as a table of overlaid calendars,
             and the dropdowns for anything the board could not name
     after   confirming redraws every section from the board's own answer
  
     worked example
       one row per overlaid calendar, because that is what an export is
       rows needing a decision lead the table and carry a dropdown
       redrawEverything() is the single place that knows what 'everything'
         is -- a mapping change moves whose time is whose, which moves the
         availability, which moves the ranking
   ----------------------------------------------------------------------- */

const TIER_WORD = {
  1: "Live Outlook feed",
  2: "Timed export",
  3: "Month grid",
  4: "Screenshot",
};

/* One card, four views, instead of eight cards stacked down the page. The
   information is the same; showing all of it at once buried the two things a
   coordinator actually opens this section for -- who is free, and who is out. */

const SYNC_TABS = [
  // First, and only present when something is actually waiting. An image
  // import cannot take effect until these are settled, so burying it would
  // leave the upload looking finished when it is not.
  ["review", "To confirm", (c) => (c.pending_review || []).length],
  ["slots", "Free slots", () => (S.board.availability || {}).week],
  ["coverage", "Whose calendar", () => (S.board.availability || {}).coverage],
  ["availability", "Monthly load", (c) => (c.availability || []).some((a) => a.coordinator_id)],
  ["absences", "Who is out", (c) => (c.unavailable || []).length || (c.unresolved_names || []).length],
  ["filters", "Lab rules", (c) => (c.filters || []).length],
  ["evidence", "Evidence", () => true],
];

function drawSync() {
  drawUpload();
  drawImportResult();
  drawSyncTabs();
  drawColorMatch();
  drawImportHistory();
}

function drawSyncTabs() {
  const cal = S.board.calendar || {};
  const available = SYNC_TABS.filter(([, , has]) => has(cal));
  const card = $("sync-detail-card");
  if (!available.length) { card.hidden = true; return; }
  card.hidden = false;

  if (!available.some(([key]) => key === S.syncTab)) S.syncTab = available[0][0];

  $("sync-tabs").innerHTML = available.map(([key, label]) =>
    `<button class="seg ${key === S.syncTab ? "is-on" : ""}" role="tab"
       aria-selected="${key === S.syncTab}" data-tab="${esc(key)}"
       type="button">${esc(label)}</button>`).join("");
  $("sync-tabs").querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => { S.syncTab = b.dataset.tab; drawSyncTabs(); }));

  const title = (available.find(([k]) => k === S.syncTab) || [])[1] || "";
  $("sync-detail-title").textContent = title;

  const panel = $("sync-panel");
  panel.innerHTML = "";
  if (S.syncTab === "availability") drawAvailability(panel);
  if (S.syncTab === "absences") drawAbsences(panel);
  if (S.syncTab === "filters") drawFilters(panel);
  if (S.syncTab === "evidence") drawEvidence(panel);
  if (S.syncTab === "review") drawReview(panel);
  if (S.syncTab === "slots") drawSlots(panel);
  if (S.syncTab === "coverage") drawCoverage(panel);
}

const AVAIL_WORD = {
  busy: "Spoken for", light: "Some commitments", open: "Clear", unknown: "Not visible",
};

const DAY_INITIAL = ["M", "T", "W", "T", "F", "S", "S"];

const FILTER_ICON = { offered_window: "\u25F7", clinician_shift: "\u271A", lab_space: "\u25A3" };

function drawAbsences(panel) {
  const cal = S.board.calendar || {};
  const rows = cal.unavailable || [];
  const unresolved = cal.unresolved_names || [];

  const byDay = {};
  rows.forEach((r) => { (byDay[r.day] = byDay[r.day] || []).push(r); });

  panel.innerHTML = `
    <p class="note">Whole-day notices read off the all-day banners. These days are blocked outright.</p>
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

function drawFilters(panel) {
  const cal = S.board.calendar || {};
  const filters = cal.filters || [];
  if (!filters.length) { panel.innerHTML = ""; return; }

  const roles = (cal.roles || []).filter((r) => r.role !== "coordinator");
  panel.innerHTML = `
    <p class="note">From the lab's own calendars. Two say when a visit <b>may</b> happen; one says when the room is <b>taken</b>.</p>
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

function drawAvailability(panel) {
  const cal = S.board.calendar || {};
  const rows = (cal.availability || []).filter((a) => a.coordinator_id);
  if (!rows.length) { panel.innerHTML = ""; return; }

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
          title="${esc(r.name)}, ${esc(d.day)}: ${esc(AVAIL_WORD[d.state] || d.state)}${
            d.items ? ", " + d.items + " item" + (d.items === 1 ? "" : "s") : ""
          }${d.truncated ? " (day cell was cut off)" : ""}"></td>`).join("")}
      <td class="availcount"><b>${r.open_working_days}</b></td>
    </tr>`).join("");

  panel.innerHTML = `
    <p class="note">${esc(cal.availability_month
      ? "Day-level load for " + cal.availability_month + ". A month print shows how many commitments each person has, not the gaps between them."
      : "Day-level load from the uploaded month print.")}</p>
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

function drawSlots(panel) {
  /* The join every per-coordinator upload exists to produce: for each slot,
     how many people could actually go. Shown as a heat strip per day rather
     than a table of names -- the number is what a scheduler scans for, and the
     names are one hover away. */
  const av = S.board.availability || {};
  const week = av.week || [];
  if (!week.length) { panel.innerHTML = ""; return; }
  const team = (S.board.roster || []).length || 1;

  panel.innerHTML = `
    <p class="note">Who could take a visit in each ${av.slot_minutes || 30}-minute
      slot, once every calendar is in. A coordinator the board has not synced counts
      as <b>unknown</b>, never free.</p>
    <div class="slotwrap">${week.map((d) => `
      <div class="slotday">
        <div class="slotday-head">${esc(d.label)}<span>${d.best} free at best</span></div>
        <div class="slotrow">${d.slots.map((s) => {
          const share = s.n_free / team;
          const tone = s.n_free === 0 ? "none" : share >= 0.6 ? "many"
            : share >= 0.3 ? "some" : "few";
          const who = s.free.length ? s.free.join(", ") : "nobody free";
          return `<span class="slot is-${tone}"
             data-tip="${esc(s.label)}: ${esc(who)}"><i>${s.n_free}</i></span>`;
        }).join("")}</div>
      </div>`).join("")}</div>
    <div class="availlegend">
      <span><i class="availkey is-open"></i>most of the team</span>
      <span><i class="availkey is-light"></i>some</span>
      <span><i class="availkey is-busy"></i>one or two</span>
      <span><i class="availkey is-unknown"></i>nobody</span>
    </div>`;
}

function drawCoverage(panel) {
  const cov = (S.board.availability || {}).coverage;
  if (!cov) { panel.innerHTML = ""; return; }
  panel.innerHTML = `
    <p class="note">Whose calendar the board actually holds. Anyone missing shows
      as unavailable in every slot, so a partial sync reads as a busy team unless
      it is named.</p>
    ${cov.complete
      ? `<div class="notice"><span>&#10003;</span><span>Every coordinator's calendar is current.</span></div>`
      : `<div class="notice notice-warn"><span>&#9888;</span><span>Still waiting on
         <b>${cov.outstanding.map(esc).join(", ")}</b>. Until their calendars are
         uploaded they count as unavailable everywhere.</span></div>`}
    <div class="tiles" style="margin-top:1rem">${cov.rows.map((r) => `
      <div class="tile is-${r.state === "current" ? "open"
        : r.state === "stale" ? "closing" : "overdue"}"
        data-tip="${esc(r.name)}: ${r.state === "missing" ? "no calendar uploaded"
          : r.age_minutes + " min old, " + r.blocks + " blocks"}">
        <span class="tile-top"><span class="tile-dot"></span>
          <span class="tile-study">${esc(r.state)}</span></span>
        <span class="tile-num">${r.state === "missing" ? "none" : r.blocks}</span>
        <span class="tile-unit">${r.state === "missing" ? "no calendar" : "blocks"}</span>
        <span class="tile-fam">${esc(r.name)}</span>
      </div>`).join("")}</div>`;
}

function drawReview(panel) {
  const pending = (S.board.calendar || {}).pending_review || [];
  if (!pending.length) { panel.innerHTML = ""; return; }

  panel.innerHTML = `
    <p class="note">Read from an image, so these are approximate. Check them
      against the calendar you uploaded. Until they are confirmed they block
      nobody.</p>
    <div class="assign-row" style="margin:.9rem 0">
      <input class="select" id="review-by" placeholder="Your name" style="min-width:15ch">
      <button class="btn btn-primary" id="review-all-ok" type="button">
        Confirm all ${pending.length}</button>
      <button class="btn btn-ghost" id="review-all-no" type="button">Reject all</button>
    </div>
    <div class="reviewlist">${pending.slice(0, 60).map((b) => `
      <div class="reviewrow" data-block="${esc(b.block_id)}">
        <div>
          <div class="cand-name">${esc(b.coordinator)}</div>
          <div class="cand-sub">${esc(prettyBlock(b.start, b.end))}</div>
        </div>
        <div class="reviewacts">
          <button class="btn btn-ghost" data-act="reject" type="button">Not real</button>
          <button class="btn btn-primary" data-act="confirm" type="button">Confirm</button>
        </div>
      </div>`).join("")}</div>
    ${pending.length > 60 ? `<p class="note">${pending.length - 60} more below the first 60.</p>` : ""}`;

  panel.querySelectorAll(".reviewrow .btn").forEach((btn) =>
    btn.addEventListener("click", () =>
      reviewBlock(btn.closest(".reviewrow").dataset.block,
                  btn.dataset.act === "confirm")));
  const who = () => ($("review-by").value || "coordinator").trim();
  $("review-all-ok").addEventListener("click", () => reviewAll(true, who()));
  $("review-all-no").addEventListener("click", () => reviewAll(false, who()));
}

async function reviewAll(confirmed, reviewer) {
  try {
    const out = await api("/api/calendar/review-all", {
      method: "POST",
      body: JSON.stringify({ confirmed, reviewer }),
    });
    await refresh();
    setSection("sync");
    toast(`${out.settled} block${out.settled === 1 ? "" : "s"} ${
      confirmed ? "confirmed" : "rejected"}.`);
  } catch (err) {
    toast(err.message, true);
  }
}

function drawEvidence(panel) {
  panel.innerHTML = `<div class="syncgrid">${S.board.roster.map((r) => {
    const mins = r.evidence_age_minutes;
    const state = mins == null ? "none" : mins <= 15 ? "fresh" : mins <= 60 ? "stale" : "old";
    const label = mins == null ? "No evidence yet"
      : mins < 60 ? `Synced ${mins} min ago` : `Synced ${Math.round(mins / 60)} h ago`;
    return `<div class="synccard is-${state}">
      <div class="synchead"><span class="avatar">${esc(r.initials)}</span>
        <div><div class="cand-name">${esc(r.name)}</div>
        <div class="cand-sub">${esc(label)}</div></div></div>
      <div class="syncstate">${state === "none"
        ? "Counts as <b>no evidence</b>. This person cannot be assigned until a calendar is uploaded."
        : state === "old" ? "Too old to trust. Re-sync before assigning."
        : state === "stale" ? "Usable, but any assignment stays provisional."
        : "Current."}</div>
      <div class="syncmeta">${r.blocks_reviewed || 0} of ${r.blocks_total || 0} detected blocks confirmed</div>
    </div>`;
  }).join("")}</div>`;
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
      <input id="pdf-file" type="file" multiple accept="application/pdf,.pdf,image/png,image/jpeg" hidden>
      <div class="uploadzone-inner">
        <div class="uploadzone-title">Drop the Outlook calendars here</div>
        <div class="uploadzone-sub">or</div>
        <label class="btn btn-primary" for="pdf-file">Choose a file&hellip;</label>
        <div class="uploadzone-hint" data-tip="A PDF is read from the file exactly. A screenshot is measured in pixels, cannot show a block another calendar was drawn over, and is held for confirmation unless the lab turns that off">
          One file per coordinator, or several at once. A <b>Work Week</b> PDF is read
          exactly . A screenshot is approximate.</div>
      </div>
    </div>`;

  const input = $("pdf-file");
  const zone = $("dropzone");
  input.addEventListener("change", () => {
    if (input.files && input.files.length) uploadBatch([...input.files]);
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
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) uploadBatch([...files]);
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

async function uploadBatch(files) {
  /* One print per coordinator is the shape the lab actually produces, so the
     zone takes a batch. Sequential rather than parallel: each import writes to
     the same audit store, and a progress line that names the file being read
     is more use than a spinner over the whole set. */
  if (files.length === 1) { await uploadPdf(files[0]); return; }

  const done = [];
  for (let i = 0; i < files.length; i++) {
    $("sync-result").innerHTML = `<div class="notice"><span>&#8987;</span><span>
      Reading ${esc(files[i].name)} (${i + 1} of ${files.length})</span></div>`;
    try {
      const data = await readAsBase64(files[i]);
      const out = await api("/api/calendar/upload", {
        method: "POST",
        body: JSON.stringify({ filename: files[i].name, data }),
      });
      done.push({ name: files[i].name, out });
    } catch (err) {
      done.push({ name: files[i].name, error: err.message });
    }
  }
  S.batch = done;
  S.lastImport = null;
  await refresh();
  setSection("sync");
  drawReadTable();
  const ok = done.filter((d) => !d.error).length;
  toast(`${ok} of ${done.length} calendars read.`, ok < done.length);
}

async function uploadPdf(file) {
  S.batch = null;
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
    drawReadTable();
    /* What to say is decided by what the board did with the upload, not by
       what this file assumes the setting is. Both are real states, and the
       lab can switch between them from the tuning controls. */
    toast(!out.schedulable
      ? `Read ${out.entry_count} entries as a workload signal only.`
      : out.pending_review
      ? `Read ${out.block_count} blocks. ${out.pending_review} to confirm below.`
      : `Read ${out.block_count} blocks. They are in effect now.`,
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
  if (S.batch) { drawBatchResult(box); return; }
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
      <div class="importverdict">${tier === 4
        ? `Read from an image, so the times are measured in pixels rather than read off the file. ${imp.pending_review ? "Every block below has to be confirmed before it counts." : "They are in effect already."} An image also cannot show a block another calendar was drawn over, and a block nobody saw reads as free time. The same calendar printed to PDF is read from the file exactly.`
        : tier === 2
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

  $("sync-colors-card").hidden = false;
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
  if (!rows.length) { $("sync-history").innerHTML = ""; return; }
  $("sync-history").innerHTML = `
    <h3 style="margin-top:1.1rem">Uploads</h3>
    <div class="tablewrap"><table class="tbl">
      <thead><tr><th>Uploaded</th><th>File</th><th>View</th>
      <th>Entries</th><th>Blocks</th><th>Can schedule</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td>${esc(String(r.uploaded_at || "").replace("T", " ").slice(0, 16))}</td>
        <td>${esc(r.source_file)}</td>
        <td>${esc(TIER_WORD[r.tier] || r.view_type)}</td>
        <td>${r.entry_count}</td>
        <td>${r.block_count}</td>
        <td>${r.schedulable ? "Yes" : "No, workload only"}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
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
   "Every family's next checkpoint, its window, and how much of that window is left.",
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
   "Visits, committed hours, out-of-hours tally and van training per coordinator.",
   () => {
     const rows = [["coordinator", "visits_this_week", "committed_hours",
                    "capacity_hours", "how full their week is", "out_of_hours",
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

function drawBatchResult(box) {
  /* One row per file. Which coordinator each print turned out to be for is the
     thing worth showing: that is what says the batch covered the team, and it
     is read from the print rather than the filename. */
  const rows = S.batch.map((d) => {
    if (d.error) {
      return `<tr><td>${esc(d.name)}</td><td colspan="3" class="over">${esc(d.error)}</td></tr>`;
    }
    const who = (d.out.coordinators_touched || []).length
      ? d.out.coordinators_touched
          .map((id) => esc(nameFor(id))).join(", ")
      : "<i>nobody the roster recognises</i>";
    return `<tr>
      <td>${esc(d.name)}</td>
      <td>${who}</td>
      <td class="num">${d.out.block_count}</td>
      <td>${d.out.pending_review ? `${d.out.pending_review} to confirm` : "in effect"}</td>
    </tr>`;
  }).join("");

  box.innerHTML = `
    <div class="importcard is-ok">
      <div class="importhead"><div>
        <div class="import-file">${S.batch.length} calendars read</div>
        <div class="cand-sub">One print per coordinator</div>
      </div></div>
      <div class="tablewrap" style="margin-top:.9rem"><table class="tbl">
        <thead><tr><th>File</th><th>Whose calendar</th><th class="num">Blocks</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`;
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
        how training gets recorded, with no code change. Currently
        <b>${S.board.health.reliability_matrix_confirmed ? "confirmed" : "unconfirmed"}</b>,
        so reliability gates are not being enforced yet.</p>
      <button class="btn btn-ghost" type="button" disabled>Needs the local board</button>
    </div>`;
}

/* ------------------------------------------------------- what the read found

   The step between dropping a print on the board and trusting what it says.
   One row per calendar the export overlaid, because that is the unit the
   print itself is built from: Outlook stacks several calendars, gives each a
   colour, and names them in the header.

   Two things can go wrong and both are visible here. The board may not know
   what a calendar is, or it may know it is a person and not which person.
   Those rows carry a dropdown of the roster and sort to the top. Everything
   else is shown for checking and needs no action.

   The dropdown is built from the roster on every draw, so adding a
   coordinator makes them selectable without touching this file. */

async function drawReadTable() {
  const card = $("read-table-card");
  if (!card) return;
  let table;
  try {
    table = await api("/api/calendar/read-table");
  } catch (err) {
    card.hidden = true;
    return;
  }
  const rows = table.rows || [];
  if (!rows.length) { card.hidden = true; return; }
  card.hidden = false;

  const unsettled = table.needs_mapping || 0;
  $("read-table-title").textContent = unsettled
    ? `${unsettled} calendar${unsettled === 1 ? "" : "s"} to identify`
    : "What was read";
  $("read-table-note").innerHTML = unsettled
    ? `The board could not tell whose calendar these are. Point each one at a
       person, or leave it alone if it is not a person at all. Nothing here is
       guessed from a colour.`
    : `${esc(table.source_file || "")} &middot; ${esc(table.view_type || "")}
       &middot; covers ${esc(table.date_range || "an unstated range")}.
       Every calendar in the print was recognised.`;

  const options = table.options || [];
  $("read-table").innerHTML = `
    <table class="readtable">
      <thead><tr>
        <th>Calendar as printed</th><th>Read as</th>
        <th>Whose</th><th class="num">Blocks</th>
      </tr></thead>
      <tbody>
        ${rows.map((r) => {
          /* Three states, and they are not the same thing. A row needing a
             decision leads the table. A person the lab is not scheduling is
             shown greyed with their name still on it -- the board knows who
             they are, it just will not offer them, and hiding that would
             read as "unidentified". Everything else is simply settled. */
          const offRoster = r.is_person && r.coordinator_id && !r.scheduled;
          return `
          <tr class="${r.needs_mapping ? "is-unsettled" : offRoster ? "is-offroster" : ""}">
            <td><b>${esc(r.label)}</b>
              ${r.needs_mapping ? '<span class="statchip is-fail">identify</span>' : ""}
              ${offRoster ? '<span class="statchip">not scheduled</span>' : ""}
              <span class="note">${esc(r.meaning || "")}</span></td>
            <td>${esc(r.role_label || "")}</td>
            <td>${r.is_person ? `
              <select class="input select mapsel" data-label="${esc(r.label)}"
                      data-hue="${esc(r.hue || "")}">
                <option value="">not a person</option>
                ${options.map((o) => `<option value="${esc(o.id)}"
                   ${o.id === r.coordinator_id ? "selected" : ""}>${
                   esc(o.name)}${o.alias ? ` (${esc(o.alias)})` : ""}${
                   o.scheduled === false ? " \u2014 not scheduled" : ""
                   }</option>`).join("")}
              </select>` : '<span class="note">not a person</span>'}</td>
            <td class="num">${r.blocks === null ? "" : r.blocks}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;

  $("read-table-apply").onclick = applyReadTable;
}

async function applyReadTable() {
  /* Save the corrections, then redraw every section from the board's own
     answer rather than patching the page in place. A mapping change moves
     whose time is whose, which moves availability, which moves the ranking,
     so nothing downstream can be left holding the old view. */
  const map = {};
  document.querySelectorAll("#read-table .mapsel").forEach((sel) => {
    const hue = sel.dataset.hue;
    if (hue && sel.value) map[hue] = sel.value;
  });
  const button = $("read-table-apply");
  button.disabled = true;
  button.textContent = "Updating…";
  try {
    if (Object.keys(map).length) {
      await api("/api/calendar/colors", {
        method: "POST",
        body: JSON.stringify({ map, confirmed_by: "board" }),
      });
    }
    await refresh();
    redrawEverything();
    toast("Updated. Every section now reflects that mapping.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Confirm and update";
  }
}

function redrawEverything() {
  /* One place that knows what "everything" is. A section added later has to
     be listed here or it will quietly keep showing the previous read. */
  drawKpis();
  drawQueue();
  drawSyncBadge();
  drawModeNote();
  drawTeam();
  drawDue();
  drawSync();
  drawData();
  drawReadTable();
  drawSettings();
  if (S.selected) selectVisit(S.selected, { silent: true });
}

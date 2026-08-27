/* ESD Visitboard - mindmap.js

   The visit window, opened one level at a time.

   The board used to answer a question nobody had asked yet: pick a visit and
   it printed the ranked pairs, the individual ranking and the exclusions all
   at once, so the recommendation arrived before the reasoning and every visit
   looked the same height. This draws the same data as a tree you walk.

   Root is the window. It has three branches -- the pairs that work, who can
   go, and who cannot -- and each opens to its own leaves, and a leaf opens to
   its detail. Nothing below the level you have opened is on screen, so the
   card stays one screen tall whatever the visit looks like.

   The columns scroll sideways inside the card rather than growing the page.
   A deep tree should not turn the document into something you scroll past.
*/

/* -----------------------------------------------------------------------
   SCREEN E  --  OPEN THE WINDOW
  
     before  assign.js has the ranked pairs and the per-rule eligibility
     here    the same answer as a tree you walk: the window, then its
             branches, then a leaf's detail. Only the level you opened is
             on screen
     after   nothing -- this is the end of the path a coordinator follows
  
     worked example
       Family 5901  ->  Pairs that work  4
                        Who can go       4
                        Ruled out        0
         -> Lauren Puttock + Sanjana Oak  0.263  Mon 9:00 AM  best match
       clicking an open branch closes it: a tree you can only open ends up
       fully open, which is the layout this replaced
   ----------------------------------------------------------------------- */

/* Which branch and which leaf are open. Reset whenever the visit changes,
   because "the second pair" means nothing once you are looking at a different
   family. */
function resetMindmap() {
  S.mm = { branch: null, leaf: null };
}

function mmPeople(d) {
  /* One row per person, carrying the seats they passed for.

     The eligibility payload lists clinicians and techs separately, and the
     same person can be in both. Showing them twice would read as two people
     -- the board has made that mistake with a roster before. */
  const seen = new Map();
  for (const seat of ["clinicians", "techs"]) {
    for (const row of (d.eligibility || {})[seat] || []) {
      if (!row.eligible) continue;
      const at = seen.get(row.coordinator_id) || { ...row, seats: [] };
      at.seats.push(seat === "clinicians" ? "clinician" : "tech");
      seen.set(row.coordinator_id, at);
    }
  }
  return [...seen.values()];
}

function mmBlocked(d) {
  /* Only people who cannot go at all.

     The payload has two lists and they overlap. `excluded` carries reasons a
     person cannot take the *clinician* seat -- "not signed off on Orientation
     1-3m" -- and `clinician_blocked` carries the same kind of no. Listing
     both under "ruled out" put Morgan Soto and Margaret Bell on screen twice,
     while they were also sitting under "who can go" as techs, which is
     nonsense: they can go, they just cannot run it.

     So anyone still eligible for a seat is not ruled out. Their limit shows
     where it is useful -- on their own row, which says which seats they
     passed for, and in their detail. */
  const usable = new Set(mmPeople(d).map((p) => p.coordinator_id));
  const byId = new Map();
  const add = (id, name, reason) => {
    if (!id || usable.has(id)) return;
    const at = byId.get(id) || { id, name, reasons: [] };
    at.name = at.name || name;
    if (reason && !at.reasons.includes(reason)) at.reasons.push(reason);
    byId.set(id, at);
  };
  for (const e of d.excluded || []) add(e.id, e.name, e.reason);
  for (const e of (d.eligibility || {}).clinician_blocked || []) {
    add(e.coordinator_id, e.name,
        e.reason || `Blocked by ${e.failed_rule_label || e.failed_rule}.`);
  }
  return [...byId.values()];
}

function mmClinicianLimit(d, coordinatorId) {
  /* Why somebody who can go still cannot be the clinician, or "".

     Being signed off on an assessment and being able to run the visit are
     separate questions, and this is the second one's answer. */
  const row = ((d.eligibility || {}).clinician_blocked || [])
    .find((e) => e.coordinator_id === coordinatorId);
  if (!row) return "";
  return row.reason || `Blocked by ${
    row.failed_rule_label || row.failed_rule || "a rule with no reason recorded"}.`;
}

function mmBranches(d) {
  const people = mmPeople(d);
  const blocked = mmBlocked(d);
  return [
    { id: "pairs", label: "Pairs that work", count: (d.pairs || []).length,
      hint: "One clinician, one tech, ranked by the model" },
    { id: "who", label: "Who can go", count: people.length,
      hint: "Passed every hard rule for this visit" },
    { id: "out", label: "Ruled out", count: blocked.length,
      hint: "And the rule that stopped them" },
  ];
}

function drawMindmap(d, assigned) {
  const branches = mmBranches(d);
  const open = S.mm.branch;
  const v = d.visit;

  return `
    <div class="card mmcard">
      <div class="card-head"><div>
        <p class="eyebrow">The decision</p>
        <h2>Open the window</h2>
        <p class="note">Nothing is chosen for you until you look. Each level
          opens the one below it.</p>
      </div></div>
      <div class="mm" id="mindmap">
        <div class="mm-col">
          <button class="mm-node is-root${open ? " is-open" : ""}" type="button"
                  data-mm-root="1">
            <b>${esc(v.family_label)}</b>
            <span class="mm-sub">${esc(v.title)}</span>
            <span class="mm-sub">${esc(v.window)}</span>
          </button>
        </div>

        <div class="mm-col has-parent">
          ${branches.map((b) => `
            <button class="mm-node${open === b.id ? " is-open" : ""}${
              b.count ? "" : " is-empty"}" type="button" data-mm-branch="${b.id}">
              <b>${esc(b.label)}</b>
              <span class="mm-count">${b.count}</span>
              <span class="mm-sub">${esc(b.hint)}</span>
            </button>`).join("")}
        </div>

        ${open ? `<div class="mm-col has-parent">${mmLeaves(d, open, assigned)}</div>` : ""}
        ${open && S.mm.leaf ? `<div class="mm-col has-parent mm-detailcol">${
          mmDetail(d, open, assigned)}</div>` : ""}
      </div>
    </div>`;
}

function mmLeaves(d, branch, assigned) {
  const pick = S.mm.leaf;
  if (branch === "pairs") {
    const pairs = d.pairs || [];
    if (!pairs.length) {
      /* The reasons matter more than the fact. "Nobody is free alongside
         anybody else" and "no clinician is signed off on this assessment"
         send a scheduler to different places. */
      const problems = d.pair_problems || [];
      return `<p class="mm-none">No pair passes every rule for this window,
        so this visit needs scheduling by hand.</p>`
        + problems.map((x) => `<p class="mm-none is-why">${esc(x)}</p>`).join("");
    }
    return pairs.map((p, i) => {
      const top = p.clinician_id === d.recommended_id;
      return `
        <button class="mm-node${pick === "pair:" + i ? " is-open" : ""}" type="button"
                data-mm-leaf="pair:${i}">
          <b>${esc(p.clinician)} + ${esc(p.tech)}</b>
          <span class="mm-score">${(p.score).toFixed(3)}</span>
          <span class="mm-sub">${esc(p.slot || "no slot found")}</span>
          ${top ? '<span class="mm-tag">best match</span>' : ""}
          ${i === 1 && d.close_call ? '<span class="mm-tag is-warn">within the tie band</span>' : ""}
        </button>`;
    }).join("")
      + (d.pair_problems || []).map((x) =>
          `<p class="mm-none is-why">${esc(x)}</p>`).join("");
  }
  if (branch === "who") {
    const people = mmPeople(d);
    if (!people.length) return `<p class="mm-none">Nobody passed the hard rules.</p>`;
    return people.map((p) => `
      <button class="mm-node${pick === "who:" + p.coordinator_id ? " is-open" : ""}"
              type="button" data-mm-leaf="who:${esc(p.coordinator_id)}">
        <b>${esc(p.name)}</b>
        <span class="mm-sub">${p.seats.join(" and ")}${
          mmClinicianLimit(d, p.coordinator_id) ? " &middot; cannot run it" : ""}</span>
      </button>`).join("");
  }
  const blocked = mmBlocked(d);
  if (!blocked.length) {
    return `<p class="mm-none">Nobody is ruled out of this visit. Some can go
      without being able to run it &mdash; that shows under "Who can go".</p>`;
  }
  return blocked.map((b) => `
    <button class="mm-node${pick === "out:" + b.id ? " is-open" : ""}" type="button"
            data-mm-leaf="out:${esc(b.id)}">
      <b>${esc(b.name)}</b>
      <span class="mm-sub">cannot go at all</span>
    </button>`).join("");
}

function mmDetail(d, branch, assigned) {
  const key = S.mm.leaf || "";
  const [kind, rest] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];

  if (kind === "pair") {
    const p = (d.pairs || [])[Number(rest)];
    if (!p) return "";
    const top = p.clinician_id === d.recommended_id;
    /* The sum, in the order the weights are written. A bar per criterion, so
       the one carrying the pair is visible without reading four numbers. */
    const parts = ["phi", "psi", "omega", "p"].map((k) => {
      const c = (p.contributions || {})[k] || 0;
      const raw = (p.components || {})[k] || 0;
      return `
        <div class="mm-bar">
          <span class="mm-bark">${esc(MM_CRITERION[k] || k)}</span>
          <span class="mm-bart"><i style="width:${Math.max(0, Math.min(1, c / 0.5)) * 100}%"></i></span>
          <span class="mm-barv">${raw.toFixed(2)} &rarr; ${c.toFixed(3)}</span>
        </div>`;
    }).join("");
    return `
      <div class="mm-detail">
        <p class="eyebrow">${top ? "Best match" : "An alternative"}</p>
        <h3>${esc(p.clinician)} <span class="mm-seat">clinician</span><br>
            ${esc(p.tech)} <span class="mm-seat">tech</span></h3>
        <div class="mm-bars">${parts}</div>
        <p class="mm-total">Total <b>${p.score.toFixed(3)}</b></p>
        <dl class="mm-facts">
          <div><dt>Slot</dt><dd>${esc(p.slot || "none found")}</dd></div>
          <div><dt>Vehicle</dt><dd>${esc(p.vehicle || "-")}${
            p.vehicle_reason ? ` <span class="note">${esc(p.vehicle_reason)}</span>` : ""}</dd></div>
          <div><dt>Hours</dt><dd>${p.out_of_hours
            ? "Out of hours, which puts them in the rotation" : "Within 9 to 5"}</dd></div>
        </dl>
        ${(p.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join("")}
        ${assigned ? "" : `
          <button class="btn mm-go" type="button" data-assign="${esc(p.clinician_id)}"
                  data-recommended="${top ? "1" : "0"}"
                  data-name="${esc(p.clinician)}">${
            top ? "Assign this pair" : `Choose ${esc(p.clinician)} instead`}</button>`}
      </div>`;
  }

  if (kind === "who") {
    const p = mmPeople(d).find((x) => x.coordinator_id === rest);
    if (!p) return "";
    const checks = p.checks || {};
    return `
      <div class="mm-detail">
        <p class="eyebrow">Cleared for this visit</p>
        <h3>${esc(p.name)}</h3>
        <p class="note">Can be ${p.seats.join(" and ")} here.</p>
        ${(() => {
          const limit = mmClinicianLimit(d, p.coordinator_id);
          return limit ? `<p class="mm-reason"><b>Cannot run this visit.</b>
            ${esc(limit)}</p>` : "";
        })()}
        <ul class="mm-checks">
          ${Object.keys(checks).map((rule) => `
            <li class="${checks[rule] ? "is-pass" : "is-fail"}">
              <span>${checks[rule] ? "&#10003;" : "&#10007;"}</span>
              ${esc(MM_RULE[rule] || rule)}
            </li>`).join("")}
        </ul>
      </div>`;
  }

  const b = mmBlocked(d).find((x) => x.id === rest);
  if (!b) return "";
  return `
    <div class="mm-detail">
      <p class="eyebrow">Cannot go at all</p>
      <h3>${esc(b.name)}</h3>
      ${b.reasons.map((r) => `<p class="mm-reason">${esc(r)}</p>`).join("")
        || '<p class="mm-reason">No reason was recorded.</p>'}
    </div>`;
}

const MM_CRITERION = {
  phi: "Knows the family", psi: "Burden relief",
  omega: "Family's choice", p: "Same rater as last time",
};

const MM_RULE = {
  role: "Holds the role the seat needs",
  solo_range: "The manual prints a solo range covering this age",
  assessments: "Signed off on every assessment this visit needs",
  special_rule: "No protocol or population rule stops them",
  pairing: "No pairing restriction stops them",
  availability: "Their calendar is free, and recent enough to trust",
};

function bindMindmap() {
  const root = $("mindmap");
  if (!root) return;
  root.querySelectorAll("[data-mm-branch]").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.mmBranch;
      /* Clicking the open branch closes it. A tree you can only open is a
         tree that ends up fully open, which is the layout this replaced. */
      S.mm = { branch: S.mm.branch === id ? null : id, leaf: null };
      drawDetail();
    }));
  root.querySelectorAll("[data-mm-leaf]").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.mmLeaf;
      S.mm.leaf = S.mm.leaf === id ? null : id;
      drawDetail();
    }));
  const rootBtn = root.querySelector("[data-mm-root]");
  if (rootBtn) rootBtn.addEventListener("click", () => { resetMindmap(); drawDetail(); });
}

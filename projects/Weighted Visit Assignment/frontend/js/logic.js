/* ESD Visitboard - logic.js

   The decision map: how a calendar becomes two named people.
*/

/* -----------------------------------------------------------------------
   SCREEN F  --  SHOW THE WORKINGS
  
     before  a decision exists
     here    the pipeline as a diagram, and the arithmetic behind the top
             choice
     after   settings.js, below it, lets the numbers be changed
  
     worked example
       every step is a button; tapping one shows what it did
       the worked example is the board's own arithmetic, not a retelling:
         0.000 + 0.075 + 0.200 + 0.000 = 0.275
   ----------------------------------------------------------------------- */

const LOGIC_NODES = [
  { id: "upload", art: "assets/icons/upload.png",   x: 20,  y: 16,  w: 250, h: 62, title: "Upload a calendar",
    sub: "Outlook PDF or image", icon: "\u2601" },
  { id: "read", art: "assets/icons/read.png",     x: 20,  y: 118, w: 250, h: 62, title: "Read the file",
    sub: "Times, colours, banners", icon: "\u25A6" },
  { id: "who", art: "assets/icons/who.png",      x: 20,  y: 220, w: 250, h: 62, title: "Work out who is who",
    sub: "From the printed legend", icon: "\u25D1" },
  { id: "clock", art: "assets/icons/clock.png",    x: 470, y: 16,  w: 250, h: 62, title: "Protocol clock",
    sub: "Anchor + checkpoint offset", icon: "\u25F7" },
  { id: "due", art: "assets/icons/due.png",      x: 470, y: 118, w: 250, h: 62, title: "Which visit is next",
    sub: "Ordered by window left", icon: "\u2691" },
  { id: "gates", art: "assets/icons/gates.png",    x: 195, y: 334, w: 350, h: 72, title: "Can they go?",
    sub: "8 hard gates, first failure wins", icon: "\u26D4" },
  { id: "score", art: "assets/icons/score.png",    x: 195, y: 448, w: 350, h: 72, title: "How good a fit?",
    sub: "4 criteria \u00D7 weights", icon: "\u2211" },
  { id: "rank", art: "assets/icons/rank.png",     x: 195, y: 562, w: 350, h: 62, title: "Rank, flag close calls",
    sub: "Close call", icon: "\u2263" },
  { id: "assign", art: "assets/icons/assign.png",   x: 245, y: 664, w: 250, h: 62, title: "Assign, or override",
    sub: "A reason is recorded", icon: "\u2713" },
];

const LOGIC_EDGES = [
  ["upload", "read"], ["read", "who"], ["clock", "due"],
  ["who", "gates"], ["due", "gates"],
  ["gates", "score"], ["score", "rank"], ["rank", "assign"],
];

function logicNode(id) { return LOGIC_NODES.find((n) => n.id === id); }

function drawLogic() {
  const L = S.board.logic || {};
  const W = 740, H = 748;

  const edges = LOGIC_EDGES.map(([a, b]) => {
    const from = logicNode(a), to = logicNode(b);
    const x1 = from.x + from.w / 2, y1 = from.y + from.h;
    const x2 = to.x + to.w / 2, y2 = to.y;
    // Straight where the boxes line up, a soft elbow where they do not. An
    // arrow that wanders is harder to follow than one that turns once.
    const d = Math.abs(x1 - x2) < 2
      ? `M${x1} ${y1} L${x2} ${y2 - 7}`
      : `M${x1} ${y1} L${x1} ${(y1 + y2) / 2} L${x2} ${(y1 + y2) / 2} L${x2} ${y2 - 7}`;
    return `<path class="lx" d="${d}" marker-end="url(#lhead)"/>`;
  }).join("");

  const nodes = LOGIC_NODES.map((n) => `
    <g class="lnode ${S.logicNode === n.id ? "is-on" : ""}" data-node="${esc(n.id)}"
       role="button" tabindex="0" aria-label="${esc(n.title)}">
      <rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="18"/>
      <image href="${esc(n.art)}" x="${n.x + 14}" y="${n.y + n.h / 2 - 15}"
             width="30" height="30" preserveAspectRatio="xMidYMid meet"/>
      <text class="ltitle" x="${n.x + 54}" y="${n.y + n.h / 2 - 3}">${esc(n.title)}</text>
      <text class="lsub" x="${n.x + 54}" y="${n.y + n.h / 2 + 15}">${esc(n.sub)}</text>
    </g>`).join("");

  $("logic-map").innerHTML = `
    <div class="logicwrap">
      <svg viewBox="0 0 ${W} ${H}" class="logicmap" role="img"
           aria-label="How the board turns a calendar into a decision">
        <defs>
          <marker id="lhead" viewBox="0 0 10 10" refX="8" refY="5"
                  markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--esd-science-blue)"/>
          </marker>
        </defs>
        ${edges}${nodes}
      </svg>
    </div>`;

  $("logic-map").querySelectorAll("[data-node]").forEach((g) => {
    const pick = () => { S.logicNode = g.dataset.node; drawLogic(); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
  });

  drawLogicDetail(L);
  drawLogicExample(L);
}

function drawLogicDetail(L) {
  const id = S.logicNode;
  const box = $("logic-detail");
  if (!id) {
    box.innerHTML = `<p class="note" style="margin-top:.9rem">
      Every step is a button. Tap one to see exactly what it does and the numbers
      it uses.</p>`;
    return;
  }
  const n = logicNode(id);
  const body = {
    upload: () => `<p>An Outlook print, or a photo of one. The file type decides what
      it is worth: a <b>Work Week</b> or <b>Day</b> print carries real start and end
      times; a <b>Month</b> grid carries neither, so it can only say how loaded a day
      looks. An image is read by shape rather than text, so it always needs a human
      to confirm it.</p>`,
    read: () => `<p>Event boxes are vector rectangles and the hour column is vector
      text, so times are read exactly rather than guessed. A constant few-minute
      offset from Outlook insetting each box is measured off the page and removed,
      which is why every time lands on a real appointment boundary.</p>`,
    who: () => `<p>Outlook prints each calendar's name in that calendar's own colour.
      That hidden legend is what tells the board who is who, with nothing to set up.
      A colour it cannot match is attributed to <b>nobody</b> rather than guessed 
      a wrong guess moves the wrong person's workload.</p>`,
    clock: () => `<p>Each family's next checkpoint is due at
      <code>anchor date + offset</code>, and counts as in-window for a tolerance either
      side. No anchor date means no due date: the board reports <b>unknown</b> rather
      than inventing lateness.</p>`,
    due: () => `<p>Visits are ordered by pressure, not by id:
      ${(L.priority_tiers || []).map((t) => `<span class="statchip is-skip">${esc(t)}</span>`).join(" ")}
      then already assigned. Inside a tier the raw day count decides, because
      pressure saturates the moment a window closes.</p>`,
    gates: () => `${certTable(L)}<p>Eight hard rules, checked in this order. The first one to fail is
      the reason shown, so the same situation always gets the same explanation.
      Nothing here is a score . A rule cannot be outweighed by a good score.</p>
      <ol class="gatelist">${(L.gates || []).map((g) =>
        `<li><b>${esc(g.label)}</b><span>${esc(g.why)}</span></li>`).join("")}</ol>`,
    gates_cert: () => "",
    score: () => `<p>Only candidates that passed every gate are scored. Four things are measured,
      each between 0 and 1, multiplied by a weight and added up:</p>
      ${weightTiles(L)}
      <p class="note">Weights sum to ${L.weight_total}. They are analyst-assigned and
      still to be validated against the lab's own choices.</p>`,
    rank: () => `<p>Highest total wins. If the top two are within
      <b>${L.review_band}</b> of each other the board says so rather than pretending
      the ranking is decisive . That gap is smaller than the noise in the numbers behind it.</p>`,
    assign: () => `<p>Assigning the recommended person records the decision. Choosing
      anyone else records a reason as well, which is what separates "the data was
      wrong" from "I disagreed" when the weights are reviewed.</p>`,
  }[id];

  box.innerHTML = `<div class="logicdetail">
    <div class="logicdetail-head">
      <span class="lico-badge"><img src="${esc(n.art)}" alt="" width="20" height="20"></span>
      <h3>${esc(n.title)}</h3></div>
    ${body ? body() : ""}
  </div>`;
}

function certTable(L) {
  /* Who may run what, straight from the manual's reliability chart. This is the
     rule the manual calls non-negotiable, so it belongs beside the gate that
     enforces it rather than in a settings page. */
  const cert = L.certifications;
  if (!cert || !cert.rows) return "";
  return `
    <h4 style="margin:.2rem 0 .5rem">Who is signed off on what</h4>
    <div class="tablewrap"><table class="tbl">
      <thead><tr><th>Coordinator</th><th>Fully reliable</th><th>In training</th></tr></thead>
      <tbody>${cert.rows.map((r) => `<tr>
        <td>${esc(r.name)}${r.clinician ? "" : ' <span class="statchip is-skip">tech</span>'}</td>
        <td>${r.reliable.length
          ? r.reliable.map((a) => `<span class="statchip is-pass">${esc(a)}</span>`).join(" ")
          : "<i>none listed</i>"}</td>
        <td>${r.training.length
          ? r.training.map((a) => `<span class="statchip is-skip">${esc(a)}</span>`).join(" ")
          : ""}</td>
      </tr>`).join("")}</tbody>
    </table></div>
    <p class="note">${cert.confirmed
      ? "From the lab's Clinical Assessment Reliability chart."
      : "Not yet confirmed against the manual."}
      ${cert.recency_recorded ? "" :
        "The chart records current status only , with no dates for them, so " +
        "recency is <b>unknown</b> rather than estimated."}</p>`;
}

function weightTiles(L) {
  return `<div class="wtiles">${(L.weights || []).map((w) => `
    <div class="wtile" title="${esc(w.label)} carries ${Math.round(w.weight * 100)}% of the score">
      <b>${Math.round(w.weight * 100)}%</b><span>${esc(w.label)}</span>
    </div>`).join("")}</div>`;
}

function drawLogicExample(L) {
  const d = S.detail;
  const box = $("logic-example");
  const top = d && d.candidates && d.candidates[0];
  if (!top) {
    box.innerHTML = `<p class="note">Pick a visit under <b>Assign a visit</b> and the
      arithmetic for its top choice appears here.</p>`;
    return;
  }
  $("logic-example-title").textContent = `Why ${top.name} came first`;
  const rows = (top.contributions || []).map((c) => `
    <tr>
      <td>${esc(c.label)}</td>
      <td class="num" title="${esc(c.help)}">${c.value.toFixed(3)}</td>
      <td class="num">&times; ${c.weight}</td>
      <td class="num"><b>${c.contribution.toFixed(3)}</b></td>
    </tr>`).join("");
  box.innerHTML = `
    <p class="note">${esc(d.visit.family_label)} &middot; ${esc(d.visit.title)}.
      Each measure scores 0 to 1, is multiplied by its weight, and the four add up.</p>
    <div class="tablewrap"><table class="tbl calc">
      <thead><tr><th>What is measured</th><th class="num">Score</th>
        <th class="num">Weight</th><th class="num">Contributes</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr><td colspan="3">Total</td>
        <td class="num"><b>${top.score.toFixed(3)}</b></td></tr></tfoot>
    </table></div>
    <p class="note">${top.review_band
      ? `The next candidate is only <b>${(top.gap_to_next || 0).toFixed(3)}</b> behind, inside the
         close-call margin, so the board calls this too close to call.`
      : `The next candidate is <b>${(top.gap_to_next || 0).toFixed(3)}</b> behind, clear of the
         close-call margin.`}</p>`;
}

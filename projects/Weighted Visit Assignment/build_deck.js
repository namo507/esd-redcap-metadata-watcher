/* ESD Lab - Weighted Visit Assignment deck
 *
 * Pipeline:
 *   1. python3 render_math.py        writes math/*.png + math_dims.json
 *   2. node build_deck.js out.pptx   builds the 15 slide deck
 *
 * Two paths to set for your machine:
 *   ESD_BUILD  directory holding math/ and math_dims.json (defaults to this file's dir)
 *   ESD_ASSETS the esd-lab skill's assets/ directory
 */
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

const ROOT = process.env.ESD_BUILD || __dirname;
const ASSETS = process.env.ESD_ASSETS || "/sessions/keen-gracious-keller/mnt/.claude/skills/esd-lab/assets";
const MATH = path.join(ROOT, "math");
const DIMS = JSON.parse(fs.readFileSync(path.join(ROOT, "math_dims.json"), "utf8"));
const OUT = process.argv[2] || path.join(ROOT, "esd-weighted-visit-assignment.pptx");

const C = {
  discovery: "3366FF",
  science: "91BAF4",
  coolBlue: "E6EEFC",
  coolWhite: "F4F4F6",
  jet: "000000",
  ink: "000000",
  orange: "F57F00",
  red: "D74E2D",
  yellow: "F4DA26",
  pink: "F8B2B1",
  white: "FFFFFF",
};
const FH = "Libre Franklin";
const FB = "Libre Franklin Medium";

const W = 13.333;
const H = 7.5;
const M = 0.62; // left margin

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "The Early Social Development Lab at UofSC";
pres.title = "Weighted Visit Assignment";

const img = (n) => path.join(ASSETS, n);
const mimg = (k) => path.join(MATH, k + ".png");

// place a rendered formula, sized by height, optionally centred on cx
function math(slide, key, opts) {
  const d = DIMS[key];
  if (!d) throw new Error("no dims for " + key);
  const h = opts.h;
  const w = (h * d[0]) / d[1];
  const x = opts.cx !== undefined ? opts.cx - w / 2 : opts.x;
  slide.addImage({ path: mimg(key), x, y: opts.y, w, h });
  return { x, y: opts.y, w, h };
}

function footer(slide, num) {
  slide.addText("The Early Social Development Lab at UofSC", {
    x: M, y: 6.98, w: 6, h: 0.28, fontFace: FB, fontSize: 9, color: C.science,
    align: "left", valign: "middle", margin: 0,
  });
  slide.addText(String(num), {
    x: W - M - 0.7, y: 6.98, w: 0.7, h: 0.28, fontFace: FB, fontSize: 9,
    color: C.science, align: "right", valign: "middle", margin: 0,
  });
}

// standard content slide chrome, returns the slide
function content(eyebrow, title, num, bg) {
  const s = pres.addSlide();
  s.background = { color: bg || C.white };
  s.addText(eyebrow.toUpperCase(), {
    x: M, y: 0.42, w: 11, h: 0.26, fontFace: FH, bold: true, fontSize: 11,
    color: C.science, charSpacing: 1.4, margin: 0, valign: "middle",
  });
  s.addText(title, {
    x: M, y: 0.72, w: 12.1, h: 0.62, fontFace: FH, bold: true, fontSize: 30,
    color: C.discovery, charSpacing: -0.2, margin: 0, valign: "middle",
  });
  footer(s, num);
  return s;
}

function card(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.14,
    fill: { color: o.fill || C.coolBlue }, line: { type: "none" },
  });
}

/* ------------------------------------------------------------------ 1 TITLE */
{
  const s = pres.addSlide();
  s.background = { color: C.coolBlue };

  s.addImage({ path: img("logos/logo-horizontal-discovery-blue.png"), x: M, y: 0.5, w: 1.17, h: 0.55 });
  s.addImage({ path: img("logos/uofsc-horizontal-garnet.png"), x: M + 1.42, y: 0.5, w: 3.72, h: 0.55 });

  s.addImage({ path: img("icons/sunburst-discovery-blue.png"), x: 9.55, y: 1.55, w: 3.4, h: 4.13, transparency: 62 });

  s.addText("SCHEDULING ARCHITECTURE", {
    x: M, y: 2.28, w: 8, h: 0.3, fontFace: FH, bold: true, fontSize: 13,
    color: C.discovery, charSpacing: 2.2, margin: 0, valign: "middle",
  });
  s.addText("Weighted Visit\nAssignment", {
    x: M, y: 2.62, w: 8.6, h: 1.9, fontFace: FH, bold: true, fontSize: 54,
    color: C.discovery, charSpacing: -0.6, lineSpacing: 58, margin: 0, valign: "top",
  });
  s.addText("How the system decides who goes on each family visit", {
    x: M, y: 4.62, w: 8.4, h: 0.5, fontFace: FB, fontSize: 18, color: C.ink,
    margin: 0, valign: "middle",
  });
  s.addShape(pres.ShapeType.rect, { x: M, y: 5.35, w: 1.5, h: 0.045, fill: { color: C.discovery }, line: { type: "none" } });
  s.addText("Three layers, five weighted terms, one auditable number", {
    x: M, y: 5.58, w: 8.4, h: 0.34, fontFace: FB, fontSize: 13, color: C.discovery, margin: 0, valign: "middle",
  });
  s.addText("Prepared for the lab team and PI  •  August 2026", {
    x: M, y: 6.05, w: 8.4, h: 0.34, fontFace: FB, fontSize: 12, color: C.ink, margin: 0, valign: "middle",
  });
  s.addNotes("Goal of this deck: make the visit assignment rule explicit enough that any coordinator can reproduce it by hand, and any pick can be explained to the PI or to a family.");
}

/* ------------------------------------------------- 2 THREE QUESTIONS */
{
  const s = content("Why a written rule", "Every assignment answers three questions", 2);
  const qs = [
    ["1", "Who can go?", "Some people are ruled out before anyone compares them. An open calendar slot, the right certification, no conflict with the family, and a way to get there.", "icons/suite/icon-checklist-science-blue.png"],
    ["2", "Who fits best?", "Fair rotation, family history, drive time, and current workload all matter at once. A number lets us weigh them together instead of one at a time.", "icons/suite/icon-growth-chart-science-blue.png"],
    ["3", "Who decides?", "Close calls and sensitive family situations stop the automatic pick and go to the coordinator or the PI.", "icons/suite/icon-hands-pair-science-blue.png"],
  ];
  const cw = 3.83, gap = 0.42;
  qs.forEach((q, k) => {
    const x = M + k * (cw + gap);
    card(s, { x, y: 1.82, w: cw, h: 3.55 });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.34, y: 2.16, w: 0.62, h: 0.62, fill: { color: C.discovery }, line: { type: "none" } });
    s.addText(q[0], { x: x + 0.34, y: 2.16, w: 0.62, h: 0.62, fontFace: FH, bold: true, fontSize: 22, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addImage({ path: img(q[3]), x: x + cw - 1.16, y: 2.1, w: 0.74, h: 0.74 });
    s.addText(q[1], { x: x + 0.34, y: 3.06, w: cw - 0.68, h: 0.44, fontFace: FH, bold: true, fontSize: 20, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(q[2], { x: x + 0.34, y: 3.62, w: cw - 0.68, h: 1.5, fontFace: FB, fontSize: 13, color: C.ink, lineSpacing: 19, margin: 0, valign: "top" });
  });
  s.addText("The three questions map onto three layers. A layer is never skipped, and a later layer never undoes an earlier one.", {
    x: M, y: 5.72, w: 12.1, h: 0.4, fontFace: FB, fontSize: 13.5, color: C.discovery, margin: 0, valign: "middle",
  });
  s.addNotes("Layer 1 answers question one, Layer 2 answers question two, Layer 3 answers question three.");
}

/* ------------------------------------------------- 3 THREE LAYERS FLOW */
{
  const s = content("How it runs", "The system runs in three fixed layers", 3);
  const boxes = [
    ["LAYER 1", "Filter", "Remove anyone who cannot go.\nPass or fail, no partial credit.", C.discovery, C.coolWhite],
    ["LAYER 2", "Score", "Give everyone left a single\nnumber to rank on.", C.science, C.jet],
    ["LAYER 3", "Decide", "Break close calls, then flag\nsensitive cases for a person.", C.coolBlue, C.discovery],
  ];
  const bw = 3.55, gap = 0.86;
  boxes.forEach((b, k) => {
    const x = M + k * (bw + gap);
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.05, w: bw, h: 2.3, rectRadius: 0.16, fill: { color: b[3] }, line: { type: "none" } });
    s.addText(b[0], { x: x + 0.32, y: 2.3, w: bw - 0.64, h: 0.26, fontFace: FH, bold: true, fontSize: 11, color: b[4], charSpacing: 1.8, margin: 0, valign: "middle" });
    s.addText(b[1], { x: x + 0.32, y: 2.6, w: bw - 0.64, h: 0.62, fontFace: FH, bold: true, fontSize: 27, color: b[4], margin: 0, valign: "middle" });
    s.addText(b[2], { x: x + 0.32, y: 3.28, w: bw - 0.64, h: 0.85, fontFace: FB, fontSize: 13, color: b[4], lineSpacing: 18, margin: 0, valign: "top" });
    if (k < 2) {
      s.addShape(pres.ShapeType.rightArrow, {
        x: x + bw + 0.2, y: 3.02, w: 0.46, h: 0.36,
        fill: { color: C.discovery }, line: { type: "none" },
      });
    }
  });

  const notes = [
    ["Pass or fail", "A person either clears all four hard checks or is out of the pool for this visit."],
    ["One number", "Five things we care about are folded into a single score, so they are compared together."],
    ["Human override", "A high score can never let someone through Layer 1, and never overrules a family concern."],
  ];
  notes.forEach((n, k) => {
    const x = M + k * (bw + gap);
    s.addShape(pres.ShapeType.ellipse, { x, y: 4.85, w: 0.19, h: 0.19, fill: { color: C.orange }, line: { type: "none" } });
    s.addText(n[0], { x: x + 0.31, y: 4.79, w: bw - 0.31, h: 0.3, fontFace: FH, bold: true, fontSize: 13, color: C.jet, margin: 0, valign: "middle" });
    s.addText(n[1], { x, y: 5.14, w: bw, h: 1.0, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });
  s.addNotes("The arrows are one directional. Nothing loops back.");
}

/* ------------------------------------------------- 4 NOTATION */
{
  const s = content("Reading the symbols", "What each symbol stands for", 4, C.white);
  const rows = [
    ["sym_i", "One person who could take the visit"],
    ["sym_v", "One visit request that needs filling"],
    ["sym_dt", "Days since that person's last assignment"],
    ["sym_n", "Visits that person has already done with this family"],
    ["sym_sigma", "Family flag: minus one wants a new face, plus one wants the same face"],
    ["sym_d", "Round trip drive distance in miles"],
    ["sym_E", "The pool of people who cleared Layer 1"],
    ["sym_S", "The final score for that person on that visit"],
    ["sym_eps", "How close two scores have to be to count as a tie"],
    ["sym_gamma", "The small bonus for having done the previous checkpoint"],
  ];
  const colW = 5.95, gapX = 0.3;
  rows.forEach((r, k) => {
    const col = k < 5 ? 0 : 1;
    const i = k % 5;
    const x = M + col * (colW + gapX);
    const y = 1.85 + i * 0.98;
    s.addShape(pres.ShapeType.roundRect, { x, y, w: colW, h: 0.82, rectRadius: 0.1, fill: { color: C.coolBlue }, line: { type: "none" } });
    math(s, r[0], { cx: x + 0.9, y: y + 0.24, h: 0.34 });
    s.addShape(pres.ShapeType.rect, { x: x + 1.8, y: y + 0.16, w: 0.014, h: 0.5, fill: { color: C.science }, line: { type: "none" } });
    s.addText(r[1], { x: x + 1.98, y: y + 0.06, w: colW - 2.2, h: 0.7, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 16, margin: 0, valign: "middle" });
  });
  s.addNotes("Subscript i is the person, f is the family, v is the visit.");
}

/* ------------------------------------------------- 5 LAYER 1 */
{
  const s = content("Layer 1", "Four checks, and all four must pass", 5);
  const checks = [
    ["A", "Open slot", "The person has time free inside the requested date window.", C.discovery, C.coolWhite],
    ["E", "No conflict", "The person is not on this family's do not send list.", C.red, C.coolWhite],
    ["R", "Certified", "The person holds the credential this visit type requires.", C.orange, C.coolWhite],
    ["G", "Can get there", "Transport works, including the van when the visit needs it.", C.science, C.jet],
  ];
  const cw = 2.86, gap = 0.30;
  checks.forEach((c, k) => {
    const x = M + k * (cw + gap);
    card(s, { x, y: 1.8, w: cw, h: 2.35 });
    s.addShape(pres.ShapeType.roundRect, { x: x + 0.3, y: 2.08, w: 0.56, h: 0.56, rectRadius: 0.12, fill: { color: c[3] }, line: { type: "none" } });
    s.addText(c[0], { x: x + 0.3, y: 2.08, w: 0.56, h: 0.56, fontFace: FH, bold: true, fontSize: 21, color: c[4], align: "center", valign: "middle", margin: 0 });
    s.addText(c[1], { x: x + 0.3, y: 2.8, w: cw - 0.6, h: 0.36, fontFace: FH, bold: true, fontSize: 17, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(c[2], { x: x + 0.3, y: 3.2, w: cw - 0.6, h: 0.85, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.38, w: 12.1, h: 1.44, rectRadius: 0.14, fill: { color: C.coolWhite }, line: { color: C.science, width: 1 } });
  math(s, "eligible", { cx: W / 2, y: 4.66, h: 0.42 });
  s.addText("The wedge means and. The hooked bar means not. All four have to be true at the same time.\nR carries a cert label here so it is not confused with the recency score in Layer 2.", {
    x: M + 0.3, y: 5.16, w: 11.5, h: 0.56, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, align: "center", margin: 0, valign: "middle",
  });
  s.addText("If nobody clears all four, the visit goes straight to the coordinator for manual rescheduling. The score is never used to rescue an ineligible person.", {
    x: M, y: 5.98, w: 12.1, h: 0.6, fontFace: FB, fontSize: 13.5, color: C.discovery, lineSpacing: 19, margin: 0, valign: "top",
  });
  s.addNotes("These four are non negotiable and cannot be traded off against each other.");
}

/* ------------------------------------------------- 6 TWINS CASE */
{
  const s = content("Layer 1 in practice", "A real case: two twin visits, one pool", 6);
  const head = ["Person", "Open slot", "No conflict", "Certified", "Transport", "Result"];
  const body = [
    ["Staff 1", "Yes", "On the list", "Yes", "Yes", "Out"],
    ["Staff 2", "Yes", "Clear", "Yes", "Yes", "Stays in"],
    ["Staff 3", "No", "Clear", "Yes", "Yes", "Out"],
  ];
  const rows = [
    head.map((h) => ({ text: h, options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 13, align: "center", valign: "middle" } })),
  ];
  body.forEach((r, ri) => {
    rows.push(r.map((cell, ci) => {
      const pass = cell === "Stays in";
      const fail = cell === "Out" || cell === "On the list" || (ci > 0 && ci < 5 && cell === "No");
      return {
        text: cell,
        options: {
          fontFace: ci === 0 ? FH : FB,
          bold: ci === 0 || ci === 5,
          fontSize: 13,
          color: pass ? C.discovery : fail ? C.red : C.ink,
          fill: { color: ri % 2 === 0 ? C.coolWhite : C.white },
          align: ci === 0 ? "left" : "center",
          valign: "middle",
        },
      };
    }));
  });
  s.addTable(rows, {
    x: M, y: 1.85, w: 12.1, colW: [2.9, 1.84, 1.84, 1.84, 1.84, 1.84],
    rowH: [0.46, 0.5, 0.5, 0.5], border: { type: "solid", color: C.science, pt: 0.75 },
    margin: [4, 8, 4, 8],
  });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.2, w: 5.9, h: 1.55, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { type: "none" } });
  s.addText("What happened", { x: M + 0.32, y: 4.44, w: 5.3, h: 0.32, fontFace: FH, bold: true, fontSize: 15, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("One person was on the family's exclusion list. One had no open slot. That left a single eligible person, who took both twin visits.", {
    x: M + 0.32, y: 4.8, w: 5.3, h: 0.85, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });

  s.addShape(pres.ShapeType.roundRect, { x: M + 6.2, y: 4.2, w: 5.9, h: 1.55, rectRadius: 0.14, fill: { color: C.coolWhite }, line: { color: C.orange, width: 1.25 } });
  s.addText("Why it matters", { x: M + 6.52, y: 4.44, w: 5.3, h: 0.32, fontFace: FH, bold: true, fontSize: 15, color: C.orange, margin: 0, valign: "middle" });
  s.addText("The pool collapsed to one before any scoring ran. Layer 1 did all the work, and the outcome matched what the team decided by hand.", {
    x: M + 6.52, y: 4.8, w: 5.3, h: 0.85, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });

  s.addText("Names are replaced with labels. The pattern is what matters, not who was involved.", {
    x: M, y: 5.95, w: 12.1, h: 0.34, fontFace: FB, fontSize: 11.5, color: C.ink, italic: true, margin: 0, valign: "middle" });
  s.addNotes("This case is the reason the exclusion list is a hard filter and not a scoring penalty.");
}

/* ------------------------------------------------- 7 THE SCORE */
{
  const s = content("Layer 2", "One score, built from five parts", 7);
  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.78, w: 12.1, h: 1.15, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { type: "none" } });
  math(s, "score", { cx: W / 2, y: 2.06, h: 0.55 });

  const parts = [
    ["w₁", "0.30", "Recency", "Longer since the last visit scores higher"],
    ["w₂", "0.25", "Family history", "Past visits with this family, read the way the family prefers"],
    ["w₃", "0.20", "Travel", "Shorter round trip scores higher"],
    ["w₄", "0.15", "Workload", "Fewer visits this period scores higher"],
    ["w₅", "0.10", "Continuity", "Small bonus for doing the previous checkpoint"],
  ];
  const pw = 2.26, gap = 0.24;
  parts.forEach((p, k) => {
    const x = M + k * (pw + gap);
    s.addShape(pres.ShapeType.roundRect, { x, y: 3.15, w: pw, h: 2.05, rectRadius: 0.12, fill: { color: C.white }, line: { color: C.science, width: 1 } });
    s.addShape(pres.ShapeType.roundRect, { x, y: 3.15, w: pw, h: 0.5, rectRadius: 0.12, fill: { color: C.discovery }, line: { type: "none" } });
    s.addText(p[0] + "  =  " + p[1], { x: x + 0.14, y: 3.15, w: pw - 0.28, h: 0.5, fontFace: FH, bold: true, fontSize: 14, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(p[2], { x: x + 0.18, y: 3.76, w: pw - 0.36, h: 0.5, fontFace: FH, bold: true, fontSize: 15, color: C.discovery, margin: 0, valign: "top" });
    s.addText(p[3], { x: x + 0.18, y: 4.26, w: pw - 0.36, h: 0.85, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 15, margin: 0, valign: "top" });
  });

  math(s, "weights", { x: M + 0.15, y: 5.5, h: 0.62 });
  math(s, "argmax", { x: 7.55, y: 5.55, h: 0.55 });
  s.addText("Weights add to one and every part is scaled to a common range, so no single part can dominate through its units.", {
    x: M, y: 6.32, w: 6.6, h: 0.5, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 15, margin: 0, valign: "top" });
  s.addText("Pick the eligible person with the highest score. Weights live in a settings table, so the PI can retune the policy without touching code.", {
    x: 7.55, y: 6.32, w: 5.2, h: 0.5, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 15, margin: 0, valign: "top" });
  s.addNotes("Every one of the five parts is normalised, so no single part can dominate through units.");
}

/* ------------------------------------------------- 8 PARTS 1 AND 2 */
{
  const s = content("The five parts, one and two", "Fair rotation and family history", 8);
  const blocks = [
    {
      no: "1", name: "Recency", key: "recency", mh: 0.72, wt: "weight 0.30",
      plain: "Counts days since that person's last assignment, capped at a ceiling such as 90 days.",
      read: "Someone who worked yesterday scores near zero. Someone free for three months scores one. This is what stops the same person being picked over and over.",
      color: C.discovery,
    },
    {
      no: "2", name: "Family history", key: "family", mh: 0.72, wt: "weight 0.25",
      plain: "Counts past visits with this family, then flips sign based on what the family prefers.",
      read: "Set the flag to plus one and a familiar person scores higher. Set it to minus one and a fresh person scores higher. The family decides, not the model.",
      color: C.orange,
    },
  ];
  blocks.forEach((b, k) => {
    const x = M + k * 6.2;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.8, w: 5.9, h: 4.55, rectRadius: 0.16, fill: { color: C.coolBlue }, line: { type: "none" } });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fill: { color: b.color }, line: { type: "none" } });
    s.addText(b.no, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fontFace: FH, bold: true, fontSize: 21, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(b.name, { x: x + 1.1, y: 2.12, w: 3.2, h: 0.58, fontFace: FH, bold: true, fontSize: 22, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(b.wt, { x: x + 5.9 - 1.7, y: 2.12, w: 1.34, h: 0.58, fontFace: FH, bold: true, fontSize: 12, color: b.color, align: "right", valign: "middle", margin: 0 });

    s.addShape(pres.ShapeType.roundRect, { x: x + 0.36, y: 2.92, w: 5.18, h: 1.2, rectRadius: 0.1, fill: { color: C.white }, line: { type: "none" } });
    math(s, b.key, { cx: x + 2.95, y: 3.16, h: b.mh });

    s.addText(b.plain, { x: x + 0.36, y: 4.3, w: 5.18, h: 0.62, fontFace: FH, bold: true, fontSize: 13.5, color: C.jet, lineSpacing: 18, margin: 0, valign: "top" });
    s.addText(b.read, { x: x + 0.36, y: 5.02, w: 5.18, h: 1.15, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });
  s.addNotes("Family history is the only term that can go negative, and only when the family asks for a new face.");
}

/* ------------------------------------------------- 9 PARTS 3 4 5 */
{
  const s = content("The five parts, three to five", "Travel, workload, and continuity", 9);
  const blocks = [
    ["3", "Travel", "distance", 0.62, "weight 0.20", "Compares this drive to the longest drive in the eligible pool.", "The shortest trip scores one. The longest scores zero. Everyone else sits in between.", C.discovery, C.coolWhite],
    ["4", "Workload", "workload", 0.72, "weight 0.15", "Compares visits done this period to the busiest person on the team.", "The busiest person scores zero, so the load spreads out over a review period rather than a single week.", C.jet, C.coolWhite],
    ["5", "Continuity", "continuity", 0.80, "weight 0.10", "A small fixed bonus, about 0.05, for doing the last checkpoint.", "Deliberately small. It nudges toward a familiar face without ever overturning a fair rotation on its own.", C.orange, C.coolWhite],
  ];
  const cw = 3.86, gap = 0.3;
  blocks.forEach((b, k) => {
    const x = M + k * (cw + gap);
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.8, w: cw, h: 4.55, rectRadius: 0.16, fill: { color: C.coolBlue }, line: { type: "none" } });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.3, y: 2.08, w: 0.52, h: 0.52, fill: { color: b[7] }, line: { type: "none" } });
    s.addText(b[0], { x: x + 0.3, y: 2.08, w: 0.52, h: 0.52, fontFace: FH, bold: true, fontSize: 19, color: b[8], align: "center", valign: "middle", margin: 0 });
    s.addText(b[1], { x: x + 0.94, y: 2.08, w: cw - 1.24, h: 0.52, fontFace: FH, bold: true, fontSize: 19, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(b[4], { x: x + 0.3, y: 2.62, w: cw - 0.6, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: b[7], charSpacing: 0.8, margin: 0, valign: "middle" });

    s.addShape(pres.ShapeType.roundRect, { x: x + 0.3, y: 2.98, w: cw - 0.6, h: 1.1, rectRadius: 0.1, fill: { color: C.white }, line: { type: "none" } });
    math(s, b[2], { cx: x + cw / 2, y: 2.98 + (1.1 - b[3]) / 2, h: b[3] });

    s.addText(b[5], { x: x + 0.3, y: 4.24, w: cw - 0.6, h: 0.75, fontFace: FH, bold: true, fontSize: 13, color: C.jet, lineSpacing: 17, margin: 0, valign: "top" });
    s.addText(b[6], { x: x + 0.3, y: 5.06, w: cw - 0.6, h: 1.1, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
  });
  s.addNotes("Travel and workload are both relative to the current pool, so they rescale automatically as the team changes.");
}

/* ------------------------------------------------- 10 EXAMPLE INPUTS */
{
  const s = content("Worked example, step one", "Two eligible people, side by side", 10);
  const rows = [
    [
      { text: "What we measure", options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, valign: "middle" } },
      { text: "Candidate A", options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, align: "center", valign: "middle" } },
      { text: "Candidate B", options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, align: "center", valign: "middle" } },
      { text: "Who it favours", options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, align: "center", valign: "middle" } },
    ],
  ];
  const data = [
    ["Days since last assignment", "5", "20", "B"],
    ["Recency score", "0.056", "0.222", "B"],
    ["Past visits with this family", "1", "0", "A"],
    ["Family history score (flag is plus one)", "0.500", "1.000", "B"],
    ["Round trip miles", "8", "22", "A"],
    ["Travel score", "0.636", "0.000", "A"],
    ["Visits already this period", "6", "3", "B"],
    ["Workload score", "0.000", "0.500", "B"],
    ["Did the previous checkpoint", "Yes", "No", "A"],
    ["Continuity bonus", "0.050", "0.000", "A"],
  ];
  data.forEach((r, i) => {
    const shaded = i % 2 === 0;
    const isScore = r[0].toLowerCase().indexOf("score") >= 0 || r[0].indexOf("bonus") >= 0;
    rows.push([
      { text: r[0], options: { fontFace: isScore ? FH : FB, bold: isScore, fontSize: 11.5, color: isScore ? C.discovery : C.ink, fill: { color: shaded ? C.coolWhite : C.white }, valign: "middle" } },
      { text: r[1], options: { fontFace: FB, bold: isScore, fontSize: 11.5, color: C.ink, fill: { color: shaded ? C.coolWhite : C.white }, align: "center", valign: "middle" } },
      { text: r[2], options: { fontFace: FB, bold: isScore, fontSize: 11.5, color: C.ink, fill: { color: shaded ? C.coolWhite : C.white }, align: "center", valign: "middle" } },
      { text: r[3], options: { fontFace: FH, bold: true, fontSize: 11.5, color: r[3] === "B" ? C.discovery : C.orange, fill: { color: shaded ? C.coolWhite : C.white }, align: "center", valign: "middle" } },
    ]);
  });
  s.addTable(rows, {
    x: M, y: 1.8, w: 8.2, colW: [3.7, 1.5, 1.5, 1.5],
    rowH: [0.4].concat(new Array(10).fill(0.355)),
    border: { type: "solid", color: C.science, pt: 0.7 }, margin: [2, 7, 2, 7],
  });

  s.addShape(pres.ShapeType.roundRect, { x: 9.15, y: 1.8, w: 3.55, h: 1.75, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { type: "none" } });
  s.addText("The weights in force", { x: 9.42, y: 2.0, w: 3.0, h: 0.3, fontFace: FH, bold: true, fontSize: 13.5, color: C.discovery, margin: 0, valign: "middle" });
  math(s, "example_weights", { cx: 10.925, y: 2.44, h: 0.22 });
  s.addText("Read left to right: recency, family history, travel, workload, continuity.", {
    x: 9.42, y: 2.76, w: 3.0, h: 0.62, fontFace: FB, fontSize: 11, color: C.ink, lineSpacing: 14, margin: 0, valign: "top" });

  s.addShape(pres.ShapeType.roundRect, { x: 9.15, y: 3.72, w: 3.55, h: 2.63, rectRadius: 0.14, fill: { color: C.coolWhite }, line: { color: C.orange, width: 1.25 } });
  s.addText("Split decision", { x: 9.42, y: 3.94, w: 3.0, h: 0.3, fontFace: FH, bold: true, fontSize: 13.5, color: C.orange, margin: 0, valign: "middle" });
  s.addText("A wins on travel and continuity. B wins on recency, family history, and workload. Nobody wins outright, which is exactly the case a single number is built for.",
    { x: 9.42, y: 4.3, w: 3.0, h: 1.4, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
  s.addText("Illustration only, not a real assignment.", { x: 9.42, y: 5.86, w: 3.0, h: 0.3, fontFace: FB, italic: true, fontSize: 10.5, color: C.ink, margin: 0, valign: "middle" });
  s.addText("Rows in blue are the five part scores. Everything above them is the raw measurement they come from.", {
    x: M, y: 5.9, w: 8.2, h: 0.4, fontFace: FB, fontSize: 12, color: C.discovery, margin: 0, valign: "middle" });
  s.addNotes("Numbers here are taken straight from the architecture document, with names replaced.");
}

/* ------------------------------------------------- 11 EXAMPLE MATH */
{
  const s = content("Worked example, step two", "Multiply, add, compare", 11);

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.85, w: 12.1, h: 1.28, rectRadius: 0.14, fill: { color: C.coolWhite }, line: { color: C.science, width: 1 } });
  s.addText("CANDIDATE A", { x: M + 0.34, y: 2.02, w: 3, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: C.ink, charSpacing: 1.6, margin: 0, valign: "middle" });
  math(s, "example_a", { x: M + 0.34, y: 2.42, h: 0.32 });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.32, w: 12.1, h: 1.28, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { color: C.discovery, width: 1.5 } });
  s.addText("CANDIDATE B", { x: M + 0.34, y: 3.49, w: 3, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: C.discovery, charSpacing: 1.6, margin: 0, valign: "middle" });
  math(s, "example_b", { x: M + 0.34, y: 3.89, h: 0.32 });
  s.addShape(pres.ShapeType.roundRect, { x: 11.05, y: 3.55, w: 1.35, h: 0.42, rectRadius: 0.1, fill: { color: C.discovery }, line: { type: "none" } });
  s.addText("SELECTED", { x: 11.05, y: 3.55, w: 1.35, h: 0.42, fontFace: FH, bold: true, fontSize: 10.5, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.85, w: 12.1, h: 1.45, rectRadius: 0.14, fill: { color: C.white }, line: { color: C.science, width: 1 } });
  s.addShape(pres.ShapeType.ellipse, { x: M + 0.34, y: 5.16, w: 0.2, h: 0.2, fill: { color: C.orange }, line: { type: "none" } });
  s.addText("What this shows", { x: M + 0.66, y: 5.1, w: 4, h: 0.32, fontFace: FH, bold: true, fontSize: 14, color: C.jet, margin: 0, valign: "middle" });
  s.addText("Candidate B takes it by 0.118. A longer gap since the last visit, a lighter caseload, and a clean slate with this family together outweigh B's much longer drive. Continuity alone was never going to carry Candidate A, and that is the point of scoring all five parts at once instead of ranking them one after another.",
    { x: M + 0.66, y: 5.46, w: 11.1, h: 0.75, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("If the family had a strong comfort preference, that belongs in Layer 1 as a hard override, not as a bigger continuity weight.");
}

/* ------------------------------------------------- 12 TIE BREAK */
{
  const s = content("Layer 3", "What happens when two scores are almost equal", 12);

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.8, w: 5.6, h: 1.5, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { type: "none" } });
  s.addText("STEP ONE  •  DETECT", { x: M + 0.32, y: 1.98, w: 4.9, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: C.discovery, charSpacing: 1.4, margin: 0, valign: "middle" });
  math(s, "tie", { cx: M + 2.8, y: 2.48, h: 0.4 });

  s.addShape(pres.ShapeType.roundRect, { x: M + 5.95, y: 1.8, w: 6.15, h: 1.5, rectRadius: 0.14, fill: { color: C.coolWhite }, line: { color: C.science, width: 1 } });
  s.addText("WHAT IT MEANS", { x: M + 6.27, y: 1.98, w: 5.5, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: C.ink, charSpacing: 1.4, margin: 0, valign: "middle" });
  s.addText("If the top two scores sit inside the tolerance, the ranking is not meaningful and the model stops pretending it is.",
    { x: M + 6.27, y: 2.32, w: 5.5, h: 0.8, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.5, w: 12.1, h: 1.72, rectRadius: 0.14, fill: { color: C.coolBlue }, line: { type: "none" } });
  s.addText("STEP TWO  •  RESOLVE ON FAMILY HISTORY", { x: M + 0.32, y: 3.66, w: 6, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: C.discovery, charSpacing: 1.4, margin: 0, valign: "middle" });
  math(s, "winner", { cx: W / 2, y: 4.06, h: 0.98 });

  const outs = [
    ["Family wants a new face", "The person with the fewest past visits wins the tie.", C.discovery],
    ["Family wants the same face", "The person with the most past visits wins the tie.", C.jet],
    ["Close but not tied", "Still assign, but flag it so a coordinator can look before it is confirmed.", C.orange],
  ];
  const cw = 3.86, gap = 0.3;
  outs.forEach((o, k) => {
    const x = M + k * (cw + gap);
    s.addShape(pres.ShapeType.roundRect, { x, y: 5.42, w: cw, h: 1.28, rectRadius: 0.12, fill: { color: C.white }, line: { color: o[2], width: 1.25 } });
    s.addText(o[0], { x: x + 0.26, y: 5.58, w: cw - 0.52, h: 0.3, fontFace: FH, bold: true, fontSize: 13, color: o[2], margin: 0, valign: "middle" });
    s.addText(o[1], { x: x + 0.26, y: 5.92, w: cw - 0.52, h: 0.7, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 15, margin: 0, valign: "top" });
  });
  s.addNotes("The tolerance is a setting. Start it small, then widen it if coordinators keep disagreeing with close picks.");
}

/* ------------------------------------------------- 13 ESCALATION */
{
  const s = content("Safety stops", "When a person decides instead of the score", 13);
  const steps = [
    ["Does the family have a dual relationship flag?", "YES", "Remove that person from the pool. No score is computed for them at all.", C.red],
    ["Is there a PI hold on the family record?", "YES", "Pause the automatic pick and route the visit to the PI for a manual decision.", C.orange],
    ["Neither flag is set", "GO", "Score the eligible pool, pick the top person, and confirm automatically.", C.discovery],
  ];
  steps.forEach((st, k) => {
    const y = 1.85 + k * 1.42;
    s.addShape(pres.ShapeType.roundRect, { x: M, y, w: 12.1, h: 1.2, rectRadius: 0.14, fill: { color: k === 2 ? C.coolBlue : C.coolWhite }, line: { color: st[3], width: 1.25 } });
    s.addShape(pres.ShapeType.roundRect, { x: M + 0.3, y: y + 0.36, w: 0.78, h: 0.48, rectRadius: 0.1, fill: { color: st[3] }, line: { type: "none" } });
    s.addText(st[1], { x: M + 0.3, y: y + 0.36, w: 0.78, h: 0.48, fontFace: FH, bold: true, fontSize: 12, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(st[0], { x: M + 1.28, y: y + 0.16, w: 4.9, h: 0.88, fontFace: FH, bold: true, fontSize: 15, color: C.jet, lineSpacing: 19, margin: 0, valign: "middle" });
    s.addShape(pres.ShapeType.rect, { x: M + 6.3, y: y + 0.3, w: 0.016, h: 0.6, fill: { color: st[3] }, line: { type: "none" } });
    s.addText(st[2], { x: M + 6.6, y: y + 0.16, w: 5.2, h: 0.88, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "middle" });
  });
  s.addShape(pres.ShapeType.roundRect, { x: M, y: 6.12, w: 12.1, h: 0.66, rectRadius: 0.12, fill: { color: C.discovery }, line: { type: "none" } });
  s.addText("These checks run before scoring, never after. A family concern is a stop sign, not a penalty term.", {
    x: M + 0.34, y: 6.12, w: 11.4, h: 0.66, fontFace: FH, bold: true, fontSize: 13.5, color: C.coolWhite, margin: 0, valign: "middle" });
  s.addNotes("This mirrors the real case where a relationship concern required explicit human sign off before the visit was finalised.");
}

/* ------------------------------------------------- 14 DATA MODEL */
{
  const s = content("What it needs to run", "Eight tables feed the whole model", 14);
  const head = ["Table", "Key fields", "Feeds"];
  const data = [
    ["availability", "candidate id, date start, date end", "Layer 1 open slot check"],
    ["family exclusions", "family id, excluded candidate id, reason", "Layer 1 conflict check"],
    ["certifications", "candidate id, visit type, certified", "Layer 1 credential check"],
    ["visit history", "visit id, family id, candidate id, date, visit type", "Recency, family history, continuity"],
    ["distance matrix", "candidate id, family id, distance miles", "Travel score"],
    ["workload period", "candidate id, period, visit count", "Workload score"],
    ["family continuity flag", "family id, sigma, preferred candidate id", "Family preference and overrides"],
    ["scoring weights", "w1 to w5, effective date", "Versioned policy settings"],
  ];
  const rows = [head.map((h) => ({ text: h, options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, valign: "middle" } }))];
  data.forEach((r, i) => {
    const shaded = i % 2 === 0;
    rows.push([
      { text: r[0], options: { fontFace: FH, bold: true, fontSize: 12, color: C.discovery, fill: { color: shaded ? C.coolWhite : C.white }, valign: "middle" } },
      { text: r[1], options: { fontFace: FB, fontSize: 11.5, color: C.ink, fill: { color: shaded ? C.coolWhite : C.white }, valign: "middle" } },
      { text: r[2], options: { fontFace: FB, fontSize: 11.5, color: C.ink, fill: { color: shaded ? C.coolWhite : C.white }, valign: "middle" } },
    ]);
  });
  s.addTable(rows, {
    x: M, y: 1.8, w: 12.1, colW: [2.9, 5.2, 4.0],
    rowH: [0.42].concat(new Array(8).fill(0.44)),
    border: { type: "solid", color: C.science, pt: 0.7 }, margin: [3, 8, 3, 8],
  });
  s.addText("Only the last two tables are new policy. The first six are records the lab already keeps, which is why this can be built without changing how anyone works day to day.", {
    x: M, y: 6.0, w: 12.1, h: 0.5, fontFace: FB, fontSize: 13, color: C.discovery, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("Weights are versioned by effective date so a past assignment can always be reproduced with the weights in force at the time.");
}

/* ------------------------------------------------- 15 CLOSING */
{
  const s = pres.addSlide();
  s.background = { color: C.discovery };
  s.addImage({ path: img("patterns/pattern-icon-band-white.png"), x: 0, y: 6.62, w: 13.333, h: 0.88, transparency: 86, sizing: { type: "cover", w: 13.333, h: 0.88 } });

  s.addImage({ path: img("logos/logo-horizontal-cool-white.png"), x: M, y: 0.5, w: 1.28, h: 0.6 });

  s.addText("THE WHOLE RULE, IN ONE LINE", {
    x: M, y: 1.5, w: 9, h: 0.3, fontFace: FH, bold: true, fontSize: 12, color: C.science, charSpacing: 2.2, margin: 0, valign: "middle" });
  s.addText("No feasible pool, no assignment.\nNo assignment without a score you can read.", {
    x: M, y: 1.9, w: 11.5, h: 1.1, fontFace: FH, bold: true, fontSize: 28, color: C.coolWhite, lineSpacing: 36, charSpacing: -0.2, margin: 0, valign: "top" });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.15, w: 12.1, h: 1.5, rectRadius: 0.16, fill: { color: C.coolWhite }, line: { type: "none" } });
  math(s, "final", { cx: W / 2, y: 3.45, h: 0.9 });

  const take = [
    ["Explainable", "Every pick reduces to five numbers and five weights that anyone can check by hand."],
    ["Tunable", "The PI changes the weights in a settings table. No code release, and old picks stay reproducible."],
    ["Honest", "Close calls and family concerns come back to a person, by design rather than by accident."],
  ];
  const cw = 3.86, gap = 0.3;
  take.forEach((t, k) => {
    const x = M + k * (cw + gap);
    s.addText(t[0], { x, y: 4.9, w: cw, h: 0.34, fontFace: FH, bold: true, fontSize: 16, color: C.coolWhite, margin: 0, valign: "middle" });
    s.addShape(pres.ShapeType.rect, { x, y: 5.27, w: 0.7, h: 0.035, fill: { color: C.science }, line: { type: "none" } });
    s.addText(t[1], { x, y: 5.42, w: cw, h: 0.95, fontFace: FB, fontSize: 12, color: C.coolWhite, lineSpacing: 16, margin: 0, valign: "top" });
  });
  s.addNotes("Next step: confirm the starting weights and the tie tolerance with the PI, then wire the two new tables.");
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));

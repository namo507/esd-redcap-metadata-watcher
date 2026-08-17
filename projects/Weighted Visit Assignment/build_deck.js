/* ESD Lab - Visit Scheduling Scoring System, v2
 *
 * Pipeline:
 *   1. python3 render_math.py       writes math/*.png + math_dims.json
 *   2. node build_deck.js out.pptx  builds the 19 slide deck
 *
 * Paths to set for your machine:
 *   ESD_BUILD   directory holding math/ and math_dims.json (defaults to this file's dir)
 *   ESD_ASSETS  the esd-lab skill's assets/ directory
 */
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

const ROOT = process.env.ESD_BUILD || __dirname;
const ASSETS = process.env.ESD_ASSETS || "/sessions/keen-gracious-keller/mnt/.claude/skills/esd-lab/assets";
const MATH = path.join(ROOT, "math");
const DIMS = JSON.parse(fs.readFileSync(path.join(ROOT, "math_dims.json"), "utf8"));
const OUT = process.argv[2] || path.join(ROOT, "esd-visit-scheduling-v2.pptx");

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
const M = 0.62;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "The Early Social Development Lab at UofSC";
pres.title = "Visit Scheduling Scoring System";

const img = (n) => path.join(ASSETS, n);

function math(slide, key, o) {
  const d = DIMS[key];
  if (!d) throw new Error("no dims for " + key);
  const w = (o.h * d[0]) / d[1];
  const x = o.cx !== undefined ? o.cx - w / 2 : o.x;
  slide.addImage({ path: path.join(MATH, key + ".png"), x, y: o.y, w, h: o.h });
  return { x, w };
}

// place a formula at its natural relative size (all renders share one DPI and point size)
const RENDER_DPI = 420;
function mathNat(slide, key, o) {
  const d = DIMS[key];
  const h = (d[1] / RENDER_DPI) * (o.scale || 2.15);
  return math(slide, key, { cx: o.cx, y: o.cy - h / 2, h });
}

// fit a formula inside a box: cap height so width never exceeds maxW
function mathFit(slide, key, o) {
  const d = DIMS[key];
  const h = Math.min(o.h, (o.maxW * d[1]) / d[0]);
  return math(slide, key, { cx: o.cx, x: o.x, y: o.y + (o.h - h) / 2, h });
}

function footer(slide, num) {
  slide.addText("The Early Social Development Lab at UofSC", {
    x: M, y: 6.98, w: 6, h: 0.28, fontFace: FB, fontSize: 9, color: C.science, valign: "middle", margin: 0,
  });
  slide.addText(String(num), {
    x: W - M - 0.7, y: 6.98, w: 0.7, h: 0.28, fontFace: FB, fontSize: 9, color: C.science,
    align: "right", valign: "middle", margin: 0,
  });
}

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

const rr = (s, o) => s.addShape(pres.ShapeType.roundRect, {
  x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: o.r || 0.14,
  fill: { color: o.fill || C.coolBlue },
  line: o.line ? { color: o.line, width: o.lw || 1.25 } : { type: "none" },
});

/* =============================================================== 1  TITLE */
{
  const s = pres.addSlide();
  s.background = { color: C.coolBlue };
  s.addImage({ path: img("logos/logo-horizontal-discovery-blue.png"), x: M, y: 0.5, w: 1.17, h: 0.55 });
  s.addImage({ path: img("logos/uofsc-horizontal-garnet.png"), x: M + 1.42, y: 0.5, w: 3.72, h: 0.55 });
  s.addImage({ path: img("icons/sunburst-discovery-blue.png"), x: 9.55, y: 1.55, w: 3.4, h: 4.13, transparency: 62 });

  s.addText("VISIT SCHEDULING  •  VERSION 2", {
    x: M, y: 2.24, w: 8, h: 0.3, fontFace: FH, bold: true, fontSize: 13, color: C.discovery, charSpacing: 2.2, margin: 0, valign: "middle" });
  s.addText("Who should we\noffer this visit to?", {
    x: M, y: 2.58, w: 8.6, h: 1.9, fontFace: FH, bold: true, fontSize: 48, color: C.discovery,
    charSpacing: -0.6, lineSpacing: 52, margin: 0, valign: "top" });
  s.addText("A scoring rule that ranks coordinators for every open home visit", {
    x: M, y: 4.5, w: 8.4, h: 0.5, fontFace: FB, fontSize: 17, color: C.ink, margin: 0, valign: "middle" });
  s.addShape(pres.ShapeType.rect, { x: M, y: 5.2, w: 1.5, h: 0.045, fill: { color: C.discovery }, line: { type: "none" } });
  s.addText("Fair rotation, family continuity, and honest workload, in one number", {
    x: M, y: 5.42, w: 8.4, h: 0.34, fontFace: FB, fontSize: 13, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("For lab team and PI review  •  August 2026", {
    x: M, y: 5.88, w: 8.4, h: 0.34, fontFace: FB, fontSize: 12, color: C.ink, margin: 0, valign: "middle" });
  s.addNotes("Version 2 reweights the model so family history carries the most weight, corrects the family history formula, and returns a ranked list instead of a single pick.");
}

/* ================================================== 2  WHAT WE OPTIMIZE */
{
  const s = content("The goal", "Fair and efficient, at the same time", 2);
  const goals = [
    ["Fair", "No coordinator carries a heavier share than the rest, once travel and visit length are counted honestly.", C.discovery],
    ["Efficient", "Visits get filled quickly with someone the family already trusts, without a chain of phone calls.", C.orange],
  ];
  goals.forEach((g, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 1.8, w: 5.9, h: 1.62 });
    s.addText(g[0], { x: x + 0.34, y: 2.0, w: 5.2, h: 0.4, fontFace: FH, bold: true, fontSize: 20, color: g[2], margin: 0, valign: "middle" });
    s.addText(g[1], { x: x + 0.34, y: 2.44, w: 5.2, h: 0.8, fontFace: FB, fontSize: 13, color: C.ink, lineSpacing: 18, margin: 0, valign: "top" });
  });

  s.addText("FOUR THINGS THE RULE LOOKS AT", {
    x: M, y: 3.72, w: 8, h: 0.3, fontFace: FH, bold: true, fontSize: 11, color: C.science, charSpacing: 1.6, margin: 0, valign: "middle" });

  const inputs = [
    ["Availability", "Open slot inside the date window, and no clash on the calendar.", "icons/suite/icon-checklist-science-blue.png"],
    ["Visit history", "How many times this coordinator has already seen this family.", "icons/suite/icon-footprints-science-blue.png"],
    ["Workload", "Scheduled hours this period, plus round trip travel time.", "icons/suite/icon-growth-chart-science-blue.png"],
    ["Family preference", "Whether the family wants a familiar face or a fresh one.", "icons/suite/icon-hands-pair-science-blue.png"],
  ];
  const cw = 2.86, gap = 0.30;
  inputs.forEach((n, k) => {
    const x = M + k * (cw + gap);
    rr(s, { x, y: 4.1, w: cw, h: 2.1 });
    s.addImage({ path: img(n[2]), x: x + 0.28, y: 4.34, w: 0.6, h: 0.6 });
    s.addText(n[0], { x: x + 0.28, y: 5.02, w: cw - 0.56, h: 0.34, fontFace: FH, bold: true, fontSize: 16, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(n[1], { x: x + 0.28, y: 5.4, w: cw - 0.56, h: 0.72, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
  });
  s.addNotes("Fairness and efficiency pull against each other. The weights are where we choose the trade.");
}

/* ================================================== 3  THREE LAYERS */
{
  const s = content("How it runs", "Filter, then score, then rank", 3);
  const boxes = [
    ["LAYER 1", "Filter", "Drop anyone who cannot take\nthis visit. Pass or fail.", C.discovery, C.coolWhite],
    ["LAYER 2", "Score", "Give everyone left one number\nbetween 0 and 1.", C.science, C.jet],
    ["LAYER 3", "Rank", "Return an ordered short list,\nnot a single name.", C.coolBlue, C.discovery],
  ];
  const bw = 3.55, gap = 0.86;
  boxes.forEach((b, k) => {
    const x = M + k * (bw + gap);
    rr(s, { x, y: 2.05, w: bw, h: 2.3, r: 0.16, fill: b[3] });
    s.addText(b[0], { x: x + 0.32, y: 2.3, w: bw - 0.64, h: 0.26, fontFace: FH, bold: true, fontSize: 11, color: b[4], charSpacing: 1.8, margin: 0, valign: "middle" });
    s.addText(b[1], { x: x + 0.32, y: 2.6, w: bw - 0.64, h: 0.62, fontFace: FH, bold: true, fontSize: 27, color: b[4], margin: 0, valign: "middle" });
    s.addText(b[2], { x: x + 0.32, y: 3.28, w: bw - 0.64, h: 0.85, fontFace: FB, fontSize: 13, color: b[4], lineSpacing: 18, margin: 0, valign: "top" });
    if (k < 2) s.addShape(pres.ShapeType.rightArrow, { x: x + bw + 0.2, y: 3.02, w: 0.46, h: 0.36, fill: { color: C.discovery }, line: { type: "none" } });
  });
  const notes = [
    ["Nothing is traded", "Eligibility is not a penalty. A missing credential cannot be outscored by a short drive."],
    ["Everything at once", "Five things we care about are folded into one number so they can be compared directly."],
    ["A person still chooses", "The coordinator sees the top few with reasons, and picks. Close calls get flagged."],
  ];
  notes.forEach((n, k) => {
    const x = M + k * (bw + gap);
    s.addShape(pres.ShapeType.ellipse, { x, y: 4.85, w: 0.19, h: 0.19, fill: { color: C.orange }, line: { type: "none" } });
    s.addText(n[0], { x: x + 0.31, y: 4.79, w: bw - 0.31, h: 0.3, fontFace: FH, bold: true, fontSize: 13, color: C.jet, margin: 0, valign: "middle" });
    s.addText(n[1], { x, y: 5.14, w: bw, h: 1.0, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });
  s.addNotes("The change from version 1 is Layer 3. It used to pick one person. It now returns a ranked short list.");
}

/* ================================================== 4  SYMBOLS */
{
  const s = content("Reading the symbols", "What each symbol stands for", 4);
  const rows = [
    ["sym_i", "One coordinator who could take the visit"],
    ["sym_v", "One visit request that needs filling"],
    ["sym_n", "Visits this coordinator has done with this family"],
    ["sym_k", "How many visits it takes to feel familiar, currently 2"],
    ["sym_sigma", "Family flag: plus one wants a familiar face, minus one wants a fresh one"],
    ["sym_dt", "Days since this coordinator's last visit of any kind"],
    ["sym_h", "Hours already scheduled this period"],
    ["sym_tau", "Round trip travel time in minutes"],
    ["sym_E", "The pool of coordinators who cleared Layer 1"],
    ["sym_S", "The final score, always between 0 and 1"],
    ["sym_delta", "Score gap below which the top two get flagged for review"],
    ["sym_gamma", "The small bonus for having done the previous checkpoint"],
  ];
  const colW = 5.95, gapX = 0.3, pitch = 0.805;
  rows.forEach((r, k) => {
    const col = k < 6 ? 0 : 1;
    const x = M + col * (colW + gapX);
    const y = 1.8 + (k % 6) * pitch;
    rr(s, { x, y, w: colW, h: 0.69, r: 0.1 });
    mathNat(s, r[0], { cx: x + 1.06, cy: y + 0.345 });
    s.addShape(pres.ShapeType.rect, { x: x + 2.16, y: y + 0.13, w: 0.014, h: 0.43, fill: { color: C.science }, line: { type: "none" } });
    s.addText(r[1], { x: x + 2.34, y: y + 0.02, w: colW - 2.54, h: 0.65, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 14, margin: 0, valign: "middle" });
  });
  s.addNotes("Subscript i is the coordinator, f the family, v the visit.");
}

/* ================================================== 5  LAYER 1 */
{
  const s = content("Layer 1", "Four eligibility checks, all must pass", 5);
  const checks = [
    ["A", "Open slot", "A free block inside the requested date window.", C.discovery, C.coolWhite],
    ["X", "No clash", "Nothing already booked over that block on their calendar.", C.red, C.coolWhite],
    ["E", "No conflict", "Not on this family's do not send list.", C.orange, C.coolWhite],
    ["K", "Credentialed", "Holds every credential this study type requires.", C.science, C.jet],
  ];
  const cw = 2.86, gap = 0.30;
  checks.forEach((c, k) => {
    const x = M + k * (cw + gap);
    rr(s, { x, y: 1.8, w: cw, h: 2.2 });
    rr(s, { x: x + 0.3, y: 2.06, w: 0.56, h: 0.56, r: 0.12, fill: c[3] });
    s.addText(c[0], { x: x + 0.3, y: 2.06, w: 0.56, h: 0.56, fontFace: FH, bold: true, fontSize: 21, color: c[4], align: "center", valign: "middle", margin: 0 });
    s.addText(c[1], { x: x + 0.3, y: 2.76, w: cw - 0.6, h: 0.36, fontFace: FH, bold: true, fontSize: 17, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(c[2], { x: x + 0.3, y: 3.16, w: cw - 0.6, h: 0.75, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });

  rr(s, { x: M, y: 4.26, w: 12.1, h: 1.32, r: 0.14, fill: C.coolWhite, line: C.science, lw: 1 });
  math(s, "eligible", { cx: W / 2, y: 4.54, h: 0.4 });
  s.addText("The wedge means and. The hooked bar means not. All four have to hold at the same time.\nAvailability and clash come from the calendar, so both are only as fresh as the last sync.", {
    x: M + 0.3, y: 5.02, w: 11.5, h: 0.5, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, align: "center", margin: 0, valign: "middle" });

  s.addText("If nobody clears all four, the visit goes to the coordinator to reschedule. The score is never used to rescue an ineligible person.", {
    x: M, y: 5.78, w: 12.1, h: 0.6, fontFace: FB, fontSize: 13.5, color: C.discovery, lineSpacing: 19, margin: 0, valign: "top" });
  s.addNotes("X is new in version 2. Version 1 assumed availability was already conflict free.");
}

/* ================================================== 6  STUDY TYPE */
{
  const s = content("Layer 1, the credential check", "Study type decides what a coordinator needs", 6);
  rr(s, { x: M, y: 1.8, w: 12.1, h: 1.1, r: 0.14, fill: C.coolBlue });
  math(s, "credential", { cx: W / 2, y: 2.06, h: 0.44 });

  s.addText("Read it as: the visit type's required credential list has to be a subset of what this coordinator holds.", {
    x: M, y: 3.02, w: 12.1, h: 0.36, fontFace: FB, fontSize: 13, color: C.ink, margin: 0, valign: "middle" });

  const cols = [
    ["NICO", "Study protocol", "Required list lives in the protocol record. Nothing about it is hard coded.", C.discovery],
    ["NANO", "Study protocol", "Same lookup, different list. Adding a third study means adding a row, not editing code.", C.orange],
  ];
  cols.forEach((c, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 3.55, w: 5.9, h: 1.44, r: 0.14, fill: C.white, line: c[3] });
    s.addText(c[0], { x: x + 0.34, y: 3.76, w: 3, h: 0.4, fontFace: FH, bold: true, fontSize: 21, color: c[3], charSpacing: 1.2, margin: 0, valign: "middle" });
    s.addText(c[1], { x: x + 5.9 - 2.0, y: 3.76, w: 1.66, h: 0.4, fontFace: FB, fontSize: 11, color: C.ink, align: "right", valign: "middle", margin: 0 });
    s.addText(c[2], { x: x + 0.34, y: 4.24, w: 5.2, h: 0.85, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });

  rr(s, { x: M, y: 5.22, w: 12.1, h: 1.0, r: 0.14, fill: C.coolWhite, line: C.science, lw: 1 });
  s.addText("Why this matters", { x: M + 0.34, y: 5.36, w: 3, h: 0.28, fontFace: FH, bold: true, fontSize: 13, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("Nico and Nano differ on things like ADOS administration and who can take consent. Keeping that in a lookup table means the two studies never need two versions of the scoring code, and a protocol change is a data edit.", {
    x: M + 0.34, y: 5.64, w: 11.4, h: 0.5, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("Fill the required credential lists with the PI before the pilot. The model does not care what is in them.");
}

/* ================================================== 7  THE SCORE */
{
  const s = content("Layer 2", "One score, five parts, new weights", 7);
  rr(s, { x: M, y: 1.75, w: 12.1, h: 1.05, r: 0.14, fill: C.coolBlue });
  math(s, "score", { cx: W / 2, y: 2.0, h: 0.52 });

  const parts = [
    ["w₁", "0.30", "Family history", "How well this coordinator already knows the family"],
    ["w₂", "0.25", "Recency", "How long since their last visit of any kind"],
    ["w₃", "0.20", "Workload", "Hours already booked this period"],
    ["w₄", "0.15", "Travel", "Round trip travel time for this visit"],
    ["w₅", "0.10", "Continuity", "Bonus for having done the previous checkpoint"],
  ];
  const pw = 2.26, gap = 0.24;
  parts.forEach((p, k) => {
    const x = M + k * (pw + gap);
    rr(s, { x, y: 3.02, w: pw, h: 1.95, r: 0.12, fill: C.white, line: C.science, lw: 1 });
    rr(s, { x, y: 3.02, w: pw, h: 0.5, r: 0.12, fill: k === 0 ? C.discovery : C.science });
    s.addText(p[0] + "  =  " + p[1], {
      x: x + 0.14, y: 3.02, w: pw - 0.28, h: 0.5, fontFace: FH, bold: true, fontSize: 14,
      color: k === 0 ? C.coolWhite : C.jet, align: "center", valign: "middle", margin: 0 });
    s.addText(p[2], { x: x + 0.18, y: 3.62, w: pw - 0.36, h: 0.5, fontFace: FH, bold: true, fontSize: 14.5, color: C.discovery, margin: 0, valign: "top" });
    s.addText(p[3], { x: x + 0.18, y: 4.14, w: pw - 0.36, h: 0.75, fontFace: FB, fontSize: 11.5, color: C.ink, lineSpacing: 15, margin: 0, valign: "top" });
  });

  rr(s, { x: M, y: 5.2, w: 5.9, h: 1.02, r: 0.12, fill: C.coolWhite, line: C.science, lw: 1 });
  mathFit(s, "weights", { cx: M + 2.95, y: 5.2, h: 0.62, maxW: 5.3 });

  rr(s, { x: M + 6.2, y: 5.2, w: 5.9, h: 1.02, r: 0.12, fill: C.coolBlue });
  mathFit(s, "range", { cx: M + 6.2 + 2.95, y: 5.2, h: 0.4, maxW: 5.3 });

  s.addText("Family history now carries the most weight, and every part is scaled to the same range, so the score itself is always between 0 and 1.", {
    x: M, y: 6.36, w: 12.1, h: 0.4, fontFace: FB, fontSize: 12.5, color: C.discovery, margin: 0, valign: "middle" });
  s.addNotes("The bounded range is only true because the family history term was rewritten. See the next slide.");
}

/* ================================================== 8  FAMILY HISTORY REWRITE */
{
  const s = content("The one real fix", "The family history term was backwards", 8);

  rr(s, { x: M, y: 1.78, w: 5.9, h: 2.42, r: 0.16, fill: C.coolWhite, line: C.red });
  s.addText("VERSION 1", { x: M + 0.34, y: 1.96, w: 3, h: 0.28, fontFace: FH, bold: true, fontSize: 11, color: C.red, charSpacing: 1.6, margin: 0, valign: "middle" });
  math(s, "family_old", { cx: M + 2.95, y: 2.36, h: 0.62 });
  s.addText("With the flag set to plus one, meaning the family wants a familiar face, this score falls as prior visits rise. The sign and the shape disagreed, so the term did the opposite of its label.", {
    x: M + 0.34, y: 3.14, w: 5.2, h: 0.95, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });

  rr(s, { x: M + 6.2, y: 1.78, w: 5.9, h: 2.42, r: 0.16, fill: C.coolBlue });
  s.addText("VERSION 2", { x: M + 6.54, y: 1.96, w: 3, h: 0.28, fontFace: FH, bold: true, fontSize: 11, color: C.discovery, charSpacing: 1.6, margin: 0, valign: "middle" });
  math(s, "familiarity", { x: M + 6.9, y: 2.3, h: 0.66 });
  math(s, "family", { x: M + 8.85, y: 2.28, h: 0.7 });
  s.addText("Familiarity climbs from 0 toward 1 as visits add up. The flag then picks the direction: keep it for a familiar face, invert it for a fresh one. No negative scores.", {
    x: M + 6.54, y: 3.14, w: 5.2, h: 0.95, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });

  s.addText("WHAT THE NUMBERS LOOK LIKE, WITH k = 2", {
    x: M, y: 4.42, w: 8, h: 0.28, fontFace: FH, bold: true, fontSize: 11, color: C.science, charSpacing: 1.6, margin: 0, valign: "middle" });

  const head = ["Prior visits with this family", "0", "1", "2", "4", "8"];
  const body = [
    ["Familiarity", "0.000", "0.333", "0.500", "0.667", "0.800"],
    ["Score, family wants a familiar face", "0.000", "0.333", "0.500", "0.667", "0.800"],
    ["Score, family wants a fresh face", "1.000", "0.667", "0.500", "0.333", "0.200"],
  ];
  const rows = [head.map((h, i) => ({
    text: h,
    options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12, align: i ? "center" : "left", valign: "middle" },
  }))];
  body.forEach((r, ri) => rows.push(r.map((cell, ci) => ({
    text: cell,
    options: {
      fontFace: ci === 0 ? FH : FB, bold: ci === 0, fontSize: 12, color: C.ink,
      fill: { color: ri % 2 === 0 ? C.coolWhite : C.white },
      align: ci ? "center" : "left", valign: "middle",
    },
  }))));
  s.addTable(rows, {
    x: M, y: 4.76, w: 12.1, colW: [4.9, 1.44, 1.44, 1.44, 1.44, 1.44],
    rowH: [0.4, 0.38, 0.38, 0.38], border: { type: "solid", color: C.science, pt: 0.7 }, margin: [2, 8, 2, 8],
  });
  s.addText("A coordinator who has never met the family now scores 0 on continuity, not 1. That was the bug worth catching before this term became the heaviest one.", {
    x: M, y: 6.36, w: 12.1, h: 0.4, fontFace: FB, fontSize: 12.5, color: C.discovery, margin: 0, valign: "middle" });
  s.addNotes("This inconsistency was in the source document: the prose said more prior visits should raise the score, the formula lowered it.");
}

/* ================================================== 9  RECENCY + WORKLOAD */
{
  const s = content("Parts two and three", "Recency and workload", 9);
  const blocks = [
    { no: "2", name: "Recency", key: "recency", mh: 0.72, wt: "weight 0.25", color: C.discovery,
      plain: "Days since this coordinator's last visit, capped at a ceiling such as 90 days.",
      read: "Someone who worked yesterday scores near 0. Someone free for three months scores 1. This is what keeps the rotation moving." },
    { no: "3", name: "Workload", key: "workload", mh: 0.72, wt: "weight 0.20", color: C.orange,
      plain: "Hours already scheduled this period, against the busiest person on the team.",
      read: "Counting hours instead of visit counts is the change here. A morning of long assessments no longer looks the same as two short check ins." },
  ];
  blocks.forEach((b, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 1.8, w: 5.9, h: 4.5, r: 0.16 });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fill: { color: b.color }, line: { type: "none" } });
    s.addText(b.no, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fontFace: FH, bold: true, fontSize: 21, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(b.name, { x: x + 1.1, y: 2.12, w: 3.2, h: 0.58, fontFace: FH, bold: true, fontSize: 22, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(b.wt, { x: x + 4.2, y: 2.12, w: 1.34, h: 0.58, fontFace: FH, bold: true, fontSize: 12, color: b.color, align: "right", valign: "middle", margin: 0 });
    rr(s, { x: x + 0.36, y: 2.92, w: 5.18, h: 1.2, r: 0.1, fill: C.white });
    mathFit(s, b.key, { cx: x + 2.95, y: 2.92, h: b.mh, maxW: 4.6 });
    s.addText(b.plain, { x: x + 0.36, y: 4.3, w: 5.18, h: 0.62, fontFace: FH, bold: true, fontSize: 13.5, color: C.jet, lineSpacing: 18, margin: 0, valign: "top" });
    s.addText(b.read, { x: x + 0.36, y: 5.02, w: 5.18, h: 1.15, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });
  s.addNotes("Hours per period needs a definition: scheduled contact hours, or contact plus documentation. Decide before the pilot.");
}

/* ================================================== 10  TRAVEL + CONTINUITY */
{
  const s = content("Parts four and five", "Travel and continuity", 10);
  const blocks = [
    { no: "4", name: "Travel", key: "travel", mh: 0.68, wt: "weight 0.15", color: C.discovery,
      plain: "Round trip travel time, against the longest trip in the eligible pool.",
      read: "Minutes replaced miles. Travel time and scheduled hours are now in the same unit, so the two together describe the real burden of a visit." },
    { no: "5", name: "Continuity", key: "continuity", mh: 0.78, wt: "weight 0.10", color: C.orange,
      plain: "A small fixed bonus, about 0.05, for doing the last checkpoint.",
      read: "Kept deliberately small. Family history already carries continuity at weight 0.30, so this only breaks near ties in favour of the last familiar face." },
  ];
  blocks.forEach((b, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 1.8, w: 5.9, h: 4.5, r: 0.16 });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fill: { color: b.color }, line: { type: "none" } });
    s.addText(b.no, { x: x + 0.36, y: 2.12, w: 0.58, h: 0.58, fontFace: FH, bold: true, fontSize: 21, color: C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(b.name, { x: x + 1.1, y: 2.12, w: 3.2, h: 0.58, fontFace: FH, bold: true, fontSize: 22, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(b.wt, { x: x + 4.2, y: 2.12, w: 1.34, h: 0.58, fontFace: FH, bold: true, fontSize: 12, color: b.color, align: "right", valign: "middle", margin: 0 });
    rr(s, { x: x + 0.36, y: 2.92, w: 5.18, h: 1.2, r: 0.1, fill: C.white });
    mathFit(s, b.key, { cx: x + 2.95, y: 2.92, h: b.mh, maxW: 4.6 });
    s.addText(b.plain, { x: x + 0.36, y: 4.3, w: 5.18, h: 0.62, fontFace: FH, bold: true, fontSize: 13.5, color: C.jet, lineSpacing: 18, margin: 0, valign: "top" });
    s.addText(b.read, { x: x + 0.36, y: 5.02, w: 5.18, h: 1.15, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });
  s.addNotes("Travel time should come from a routing lookup cached per coordinator and family, not computed live on every scoring run.");
}

/* ================================================== 11  FAMILY PREFERENCE */
{
  const s = content("A common question", "Where does family preference actually live?", 11);
  s.addText("It is not a sixth term. It shows up in three places, each with a different amount of force.", {
    x: M, y: 1.72, w: 12.1, h: 0.36, fontFace: FB, fontSize: 14, color: C.ink, margin: 0, valign: "middle" });

  const places = [
    ["STRONGEST", "A hard override in Layer 1", "A named request or an exclusion removes people from the pool outright. No score can undo it.", C.red],
    ["MIDDLE", "The direction of the family history term", "The flag decides whether familiarity helps or hurts, at the heaviest weight in the model.", C.discovery],
    ["LIGHTEST", "The tie break in Layer 3", "When the top two are effectively level, prior visits with the family settle it.", C.orange],
  ];
  const cw = 3.86, gap = 0.3;
  places.forEach((p, k) => {
    const x = M + k * (cw + gap);
    rr(s, { x, y: 2.28, w: cw, h: 2.85, r: 0.16, fill: C.white, line: p[3] });
    rr(s, { x, y: 2.28, w: cw, h: 0.46, r: 0.16, fill: p[3] });
    s.addText(p[0], { x, y: 2.28, w: cw, h: 0.46, fontFace: FH, bold: true, fontSize: 11, color: C.coolWhite, charSpacing: 1.6, align: "center", valign: "middle", margin: 0 });
    s.addText(p[1], { x: x + 0.3, y: 2.86, w: cw - 0.6, h: 0.9, fontFace: FH, bold: true, fontSize: 16, color: C.discovery, lineSpacing: 21, margin: 0, valign: "middle" });
    s.addText(p[2], { x: x + 0.3, y: 3.86, w: cw - 0.6, h: 1.1, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  });

  rr(s, { x: M, y: 5.4, w: 12.1, h: 1.0, r: 0.14, fill: C.coolBlue });
  s.addText("The reason for the split", { x: M + 0.34, y: 5.54, w: 4, h: 0.28, fontFace: FH, bold: true, fontSize: 13, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("A comfort concern is a stop sign, not a penalty. Folding it into the score would let a short drive and a light caseload quietly outvote it, which is exactly what we do not want to happen.", {
    x: M + 0.34, y: 5.82, w: 11.4, h: 0.5, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("This slide exists because the question comes up every time someone new reads the model.");
}

/* ================================================== 12  EXAMPLE INPUTS */
{
  const s = content("Worked example, step one", "Three eligible coordinators, one visit", 12);
  const head = ["What we measure", "A", "B", "C"];
  const data = [
    ["Prior visits with this family", "3", "0", "1", 0],
    ["Familiarity, k = 2", "0.600", "0.000", "0.333", 1],
    ["Family history score", "0.600", "0.000", "0.333", 2],
    ["Days since last visit", "5", "20", "40", 0],
    ["Recency score", "0.056", "0.222", "0.444", 2],
    ["Hours booked this period", "18", "9", "12", 0],
    ["Workload score", "0.000", "0.500", "0.333", 2],
    ["Round trip minutes", "30", "75", "45", 0],
    ["Travel score", "0.600", "0.000", "0.400", 2],
    ["Did the last checkpoint", "Yes", "No", "No", 0],
    ["Continuity bonus", "0.050", "0.000", "0.000", 2],
  ];
  const rows = [head.map((h, i) => ({
    text: h, options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12.5, align: i ? "center" : "left", valign: "middle" },
  }))];
  data.forEach((r, i) => {
    const shaded = i % 2 === 0;
    const isScore = r[4] === 2;
    rows.push([0, 1, 2, 3].map((ci) => ({
      text: r[ci],
      options: {
        fontFace: ci === 0 ? (isScore ? FH : FB) : FB,
        bold: isScore, fontSize: 11.5,
        color: isScore && ci === 0 ? C.discovery : C.ink,
        fill: { color: shaded ? C.coolWhite : C.white },
        align: ci ? "center" : "left", valign: "middle",
      },
    })));
  });
  s.addTable(rows, {
    x: M, y: 1.8, w: 7.5, colW: [3.6, 1.3, 1.3, 1.3],
    rowH: [0.4].concat(new Array(11).fill(0.345)),
    border: { type: "solid", color: C.science, pt: 0.7 }, margin: [2, 7, 2, 7],
  });

  rr(s, { x: 8.45, y: 1.8, w: 4.25, h: 1.9, r: 0.14, fill: C.coolBlue });
  s.addText("The setup", { x: 8.75, y: 1.98, w: 3.6, h: 0.3, fontFace: FH, bold: true, fontSize: 14, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("This family wants a familiar face, so the flag is plus one. Every score in blue is computed from the raw row directly above it.", {
    x: 8.75, y: 2.34, w: 3.6, h: 1.2, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });

  const who = [
    ["A", "The veteran", "Knows the family best and lives closest, but is fully booked and worked five days ago.", C.discovery],
    ["B", "The newcomer", "Lightest caseload on the team, but has never met this family and faces the longest drive.", C.orange],
    ["C", "The middle", "Nothing outstanding anywhere, nothing weak anywhere.", C.science],
  ];
  who.forEach((w, k) => {
    const y = 3.94 + k * 0.9;
    rr(s, { x: 8.45, y, w: 4.25, h: 0.8, r: 0.12, fill: C.white, line: w[3], lw: 1 });
    s.addShape(pres.ShapeType.ellipse, { x: 8.62, y: y + 0.19, w: 0.42, h: 0.42, fill: { color: w[3] }, line: { type: "none" } });
    s.addText(w[0], { x: 8.62, y: y + 0.19, w: 0.42, h: 0.42, fontFace: FH, bold: true, fontSize: 15, color: w[3] === C.science ? C.jet : C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(w[1], { x: 9.2, y: y + 0.06, w: 3.3, h: 0.28, fontFace: FH, bold: true, fontSize: 12.5, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(w[2], { x: 9.2, y: y + 0.32, w: 3.3, h: 0.44, fontFace: FB, fontSize: 10.5, color: C.ink, lineSpacing: 13, margin: 0, valign: "top" });
  });
  s.addText("Illustration only. Real numbers come out of the pilot.", {
    x: M, y: 6.42, w: 7.5, h: 0.3, fontFace: FB, italic: true, fontSize: 11, color: C.ink, margin: 0, valign: "middle" });
  s.addNotes("Note that no candidate wins on more than two of the five parts.");
}

/* ================================================== 13  EXAMPLE ARITHMETIC */
{
  const s = content("Worked example, step two", "Multiply, add, then rank", 13);
  const sums = [
    ["ex_a", "A", "0.289", C.coolWhite, C.science, 2],
    ["ex_b", "B", "0.156", C.coolWhite, C.science, 3],
    ["ex_c", "C", "0.338", C.coolBlue, C.discovery, 1],
  ];
  sums.forEach((q, k) => {
    const y = 1.8 + k * 1.16;
    rr(s, { x: M, y, w: 12.1, h: 1.0, r: 0.14, fill: q[3], line: q[4], lw: q[5] === 1 ? 1.5 : 1 });
    s.addText("COORDINATOR " + q[1], { x: M + 0.34, y: y + 0.12, w: 3, h: 0.26, fontFace: FH, bold: true, fontSize: 10.5, color: q[5] === 1 ? C.discovery : C.ink, charSpacing: 1.6, margin: 0, valign: "middle" });
    mathFit(s, q[0], { x: M + 0.34, y: y + 0.44, h: 0.3, maxW: 9.6 });
    rr(s, { x: 11.15, y: y + 0.26, w: 1.25, h: 0.48, r: 0.1, fill: q[5] === 1 ? C.discovery : C.white, line: q[5] === 1 ? null : C.science, lw: 1 });
    s.addText("RANK " + q[5], { x: 11.15, y: y + 0.26, w: 1.25, h: 0.48, fontFace: FH, bold: true, fontSize: 11, color: q[5] === 1 ? C.coolWhite : C.ink, align: "center", valign: "middle", margin: 0 });
  });

  rr(s, { x: M, y: 5.4, w: 12.1, h: 1.28, r: 0.14, fill: C.white, line: C.science, lw: 1 });
  s.addShape(pres.ShapeType.ellipse, { x: M + 0.34, y: 5.66, w: 0.2, h: 0.2, fill: { color: C.orange }, line: { type: "none" } });
  s.addText("What this shows", { x: M + 0.66, y: 5.6, w: 4, h: 0.32, fontFace: FH, bold: true, fontSize: 14, color: C.jet, margin: 0, valign: "middle" });
  s.addText("C takes the top slot without leading on a single part. A has the deepest history and the shortest drive but is fully booked and worked last week, which zeroes out two terms. The gap between C and A is 0.049, inside the 0.05 review band, so the pair is flagged for a coordinator to look at rather than confirmed silently.", {
    x: M + 0.66, y: 5.96, w: 11.1, h: 0.62, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("Under version 1 weights and the old family term, B would have won this. The reweighting is what moved the answer.");
}

/* ================================================== 14  LAYER 3 */
{
  const s = content("Layer 3", "A ranked short list, not a single name", 14);
  rr(s, { x: M, y: 1.78, w: 12.1, h: 0.82, r: 0.14, fill: C.coolBlue });
  mathFit(s, "rank", { cx: W / 2, y: 1.78, h: 0.42, maxW: 11.4 });

  const steps = [
    ["Offer the top K", "The coordinator sees the best few with their scores and the reason each one ranked where it did. Currently K is 3.", C.discovery],
    ["Flag a thin margin", "If the first and second are closer than the review band, the pair goes to a human before anything is confirmed.", C.orange],
    ["Break a true tie", "If the gap is smaller still, prior visits with the family settle it in whichever direction the family flag points.", C.science],
  ];
  const cw = 3.86, gap = 0.3;
  steps.forEach((st, k) => {
    const x = M + k * (cw + gap);
    rr(s, { x, y: 2.86, w: cw, h: 1.78, r: 0.14, fill: C.white, line: st[2] });
    s.addText(st[0], { x: x + 0.3, y: 3.06, w: cw - 0.6, h: 0.34, fontFace: FH, bold: true, fontSize: 15, color: C.discovery, margin: 0, valign: "middle" });
    s.addText(st[1], { x: x + 0.3, y: 3.46, w: cw - 0.6, h: 1.05, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
  });

  rr(s, { x: M, y: 4.9, w: 5.9, h: 0.8, r: 0.12, fill: C.coolWhite, line: C.science, lw: 1 });
  mathFit(s, "flag", { cx: M + 2.95, y: 4.9, h: 0.34, maxW: 5.3 });
  rr(s, { x: M + 6.2, y: 4.9, w: 5.9, h: 0.8, r: 0.12, fill: C.coolWhite, line: C.science, lw: 1 });
  mathFit(s, "tie", { cx: M + 6.2 + 2.95, y: 4.9, h: 0.4, maxW: 5.3 });

  s.addText("Ranking rather than picking is the honest move. The score separates a strong candidate from a weak one reliably, but it cannot tell 0.338 apart from 0.289 in any way a person should trust blindly.", {
    x: M, y: 5.9, w: 12.1, h: 0.6, fontFace: FB, fontSize: 12.5, color: C.discovery, lineSpacing: 18, margin: 0, valign: "top" });
  s.addNotes("Set the review band and the tie tolerance from pilot data, not from intuition.");
}

/* ================================================== 15  COLD START */
{
  const s = content("Open problem", "A brand new coordinator breaks three of the five parts", 15);

  const head = ["Part", "What a new hire looks like", "What the score does", "Right or wrong"];
  const data = [
    ["Family history", "No prior visits anywhere", "0 for a family wanting continuity", "Right"],
    ["Recency", "No last visit on record", "Undefined, or treated as a very long gap and scored 1", "Wrong"],
    ["Workload", "Nothing booked yet", "1, the maximum", "Wrong"],
    ["Travel", "Distance is known from their address", "Scores normally", "Right"],
    ["Continuity", "Did not do the last checkpoint", "0", "Right"],
  ];
  const rows = [head.map((h, i) => ({
    text: h, options: { bold: true, color: C.coolWhite, fill: { color: C.discovery }, fontFace: FH, fontSize: 12, align: i === 3 ? "center" : "left", valign: "middle" },
  }))];
  data.forEach((r, i) => {
    const shaded = i % 2 === 0;
    rows.push(r.map((cell, ci) => ({
      text: cell,
      options: {
        fontFace: ci === 0 ? FH : FB, bold: ci === 0 || ci === 3, fontSize: 11.5,
        color: ci === 3 ? (cell === "Wrong" ? C.red : C.ink) : ci === 0 ? C.discovery : C.ink,
        fill: { color: shaded ? C.coolWhite : C.white },
        align: ci === 3 ? "center" : "left", valign: "middle",
      },
    })));
  });
  s.addTable(rows, {
    x: M, y: 1.78, w: 12.1, colW: [2.5, 4.2, 3.8, 1.6],
    rowH: [0.4, 0.44, 0.44, 0.44, 0.44, 0.44],
    border: { type: "solid", color: C.science, pt: 0.7 }, margin: [2, 8, 2, 8],
  });

  rr(s, { x: M, y: 4.62, w: 12.1, h: 0.92, r: 0.14, fill: C.coolBlue });
  s.addText("PROPOSED FIX", { x: M + 0.34, y: 4.76, w: 3, h: 0.26, fontFace: FH, bold: true, fontSize: 11, color: C.discovery, charSpacing: 1.6, margin: 0, valign: "middle" });
  mathFit(s, "coldstart", { cx: W / 2, y: 5.0, h: 0.32, maxW: 11.4 });

  s.addText("Until a coordinator has a handful of visits on record, borrow the team median for recency and workload instead of letting an empty record read as maximum availability. A new hire then starts in the middle of the pack and earns their own numbers, rather than being handed every visit in week one.", {
    x: M, y: 5.74, w: 12.1, h: 0.68, fontFace: FB, fontSize: 12.5, color: C.ink, lineSpacing: 17, margin: 0, valign: "top" });
  s.addNotes("Pick N_min with the team. Five completed visits is a reasonable starting guess, not a validated number.");
}

/* ================================================== 16  JOINT SCHEDULING */
{
  const s = content("Open problem", "Filling visits one at a time leaves value on the table", 16);
  s.addText("Scoring one visit against one pool is greedy. Two harder questions sit behind it, and they are different problems with different tools.", {
    x: M, y: 1.7, w: 12.1, h: 0.36, fontFace: FB, fontSize: 13.5, color: C.ink, margin: 0, valign: "middle" });

  const probs = [
    { tag: "ONE PERSON, ONE DAY", title: "Which set of visits fits?",
      key: "dp", mh: 0.42,
      body: "Pick the highest scoring set of non overlapping slots for a single coordinator. This is weighted interval scheduling, and dynamic programming does solve it: sort by end time, and for each visit take either the best answer without it or its score plus the best answer ending before it starts.",
      note: "Runs in about n log n time, dominated by the sort.", color: C.discovery },
    { tag: "WHOLE TEAM, WHOLE WEEK", title: "Who takes which visit?",
      key: "assign", mh: 0.52,
      body: "Maximise total score across all open visits while respecting each person's capacity. This is an assignment problem, not a dynamic programming one. The standard tools are the Hungarian algorithm for one visit each, or minimum cost flow once people can take several.",
      note: "I am stating a standard result here. Worth confirming against a reference before implementation.", color: C.orange },
  ];
  probs.forEach((p, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 2.2, w: 5.9, h: 4.1, r: 0.16, fill: C.white, line: p.color });
    rr(s, { x, y: 2.2, w: 5.9, h: 0.46, r: 0.16, fill: p.color });
    s.addText(p.tag, { x: x + 0.34, y: 2.2, w: 5.2, h: 0.46, fontFace: FH, bold: true, fontSize: 11, color: C.coolWhite, charSpacing: 1.6, margin: 0, valign: "middle" });
    s.addText(p.title, { x: x + 0.34, y: 2.8, w: 5.2, h: 0.4, fontFace: FH, bold: true, fontSize: 19, color: C.discovery, margin: 0, valign: "middle" });
    rr(s, { x: x + 0.34, y: 3.3, w: 5.22, h: 0.85, r: 0.1, fill: C.coolBlue });
    mathFit(s, p.key, { cx: x + 2.95, y: 3.3, h: p.mh, maxW: 4.7 });
    s.addText(p.body, { x: x + 0.34, y: 4.3, w: 5.2, h: 1.5, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
    s.addText(p.note, { x: x + 0.34, y: 5.78, w: 5.2, h: 0.44, fontFace: FB, italic: true, fontSize: 11, color: C.ink, lineSpacing: 14, margin: 0, valign: "top" });
  });
  s.addNotes("Neither is needed for the pilot. Greedy per visit is fine while the weekly volume is small.");
}

/* ================================================== 17  CALENDAR */
{
  const s = content("The blocker", "Availability has to come from the calendar", 17);
  s.addText("Layer 1 cannot run on a spreadsheet of stated availability. Both checks, open slot and no clash, need live free and busy data.", {
    x: M, y: 1.7, w: 12.1, h: 0.36, fontFace: FB, fontSize: 13.5, color: C.ink, margin: 0, valign: "middle" });

  const apis = [
    { name: "Outlook, via Microsoft Graph", call: "POST /users/{id}/calendar/getSchedule",
      lines: ["Least privileged permission is Calendars.Read", "Works delegated or app only with admin consent", "Up to 20 calendars per call, window under 62 days"], color: C.discovery },
    { name: "Google Calendar", call: "POST /calendar/v3/freeBusy",
      lines: ["Scope calendar.readonly is enough", "Returns busy blocks per calendar", "Needs timeMin, timeMax, and a list of calendar ids"], color: C.orange },
  ];
  apis.forEach((a, k) => {
    const x = M + k * 6.2;
    rr(s, { x, y: 2.2, w: 5.9, h: 2.5, r: 0.16, fill: C.coolBlue });
    s.addText(a.name, { x: x + 0.34, y: 2.4, w: 5.2, h: 0.36, fontFace: FH, bold: true, fontSize: 16, color: a.color, margin: 0, valign: "middle" });
    rr(s, { x: x + 0.34, y: 2.84, w: 5.22, h: 0.44, r: 0.08, fill: C.white });
    s.addText(a.call, { x: x + 0.48, y: 2.84, w: 4.94, h: 0.44, fontFace: "Courier New", fontSize: 11.5, color: C.jet, margin: 0, valign: "middle" });
    a.lines.forEach((l, i) => {
      s.addShape(pres.ShapeType.ellipse, { x: x + 0.38, y: 3.46 + i * 0.38, w: 0.13, h: 0.13, fill: { color: a.color }, line: { type: "none" } });
      s.addText(l, { x: x + 0.64, y: 3.38 + i * 0.38, w: 4.9, h: 0.3, fontFace: FB, fontSize: 12, color: C.ink, margin: 0, valign: "middle" });
    });
  });

  rr(s, { x: M, y: 4.94, w: 5.9, h: 1.4, r: 0.14, fill: C.coolWhite, line: C.science, lw: 1 });
  s.addText("What we get back", { x: M + 0.34, y: 5.1, w: 5.2, h: 0.3, fontFace: FH, bold: true, fontSize: 14, color: C.discovery, margin: 0, valign: "middle" });
  s.addText("Busy blocks, not event details. That is all Layer 1 needs, and it keeps personal calendar content out of the system entirely.", {
    x: M + 0.34, y: 5.44, w: 5.2, h: 0.78, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });

  rr(s, { x: M + 6.2, y: 4.94, w: 5.9, h: 1.4, r: 0.14, fill: C.coolWhite, line: C.orange, lw: 1.25 });
  s.addText("Before anyone builds this", { x: M + 6.54, y: 5.1, w: 5.2, h: 0.3, fontFace: FH, bold: true, fontSize: 14, color: C.orange, margin: 0, valign: "middle" });
  s.addText("Endpoints and scopes were checked in August 2026 and both vendors change them. Confirm against current documentation, and settle the IRB position on reading staff calendars first.", {
    x: M + 6.54, y: 5.44, w: 5.2, h: 0.78, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "top" });
  s.addNotes("Sources: Microsoft Learn calendar getSchedule reference, and Google Workspace Calendar API freebusy query reference.");
}

/* ================================================== 18  PILOT PLAN */
{
  const s = content("Next steps", "What the pilot has to answer", 18);
  const steps = [
    ["1", "Wire the calendar read", "Get free and busy data flowing for the whole team on a read only scope. Nothing else can be tested until Layer 1 is real.", "Blocked on IRB and IT", C.discovery],
    ["2", "Replay past visits", "Run the model over assignments already made and compare its ranking to what the team actually chose. Disagreements are the interesting cases.", "Needs six months of history", C.orange],
    ["3", "Tune the weights", "Adjust from the replay, not from opinion. Record the version and effective date so any past decision stays reproducible.", "One afternoon with the PI", C.science],
    ["4", "Debrief the surprises", "Every case where the model and the team disagreed gets read out loud. Some will be model bugs, some will be undocumented rules worth writing down.", "Standing item, first month", C.red],
  ];
  steps.forEach((st, k) => {
    const y = 1.8 + k * 1.19;
    rr(s, { x: M, y, w: 12.1, h: 1.02, r: 0.14, fill: k % 2 === 0 ? C.coolBlue : C.coolWhite, line: k % 2 === 0 ? null : C.science, lw: 1 });
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.3, y: y + 0.27, w: 0.48, h: 0.48, fill: { color: st[4] }, line: { type: "none" } });
    s.addText(st[0], { x: M + 0.3, y: y + 0.27, w: 0.48, h: 0.48, fontFace: FH, bold: true, fontSize: 17, color: st[4] === C.science ? C.jet : C.coolWhite, align: "center", valign: "middle", margin: 0 });
    s.addText(st[1], { x: M + 0.98, y: y + 0.12, w: 3.5, h: 0.78, fontFace: FH, bold: true, fontSize: 15, color: C.discovery, lineSpacing: 19, margin: 0, valign: "middle" });
    s.addShape(pres.ShapeType.rect, { x: M + 4.62, y: y + 0.22, w: 0.016, h: 0.58, fill: { color: st[4] }, line: { type: "none" } });
    s.addText(st[2], { x: M + 4.9, y: y + 0.12, w: 5.1, h: 0.78, fontFace: FB, fontSize: 12, color: C.ink, lineSpacing: 16, margin: 0, valign: "middle" });
    s.addText(st[3], { x: M + 10.2, y: y + 0.12, w: 1.6, h: 0.78, fontFace: FH, bold: true, fontSize: 10.5, color: st[4] === C.science ? C.jet : st[4], align: "right", valign: "middle", margin: 0 });
  });
  s.addText("The pilot is a comparison, not a rollout. Nothing gets assigned automatically until the replay stops surprising us.", {
    x: M, y: 6.55, w: 12.1, h: 0.36, fontFace: FB, fontSize: 12.5, color: C.discovery, margin: 0, valign: "middle" });
  s.addNotes("Step 2 is the one that earns trust with the team. Lead with it.");
}

/* ================================================== 19  CLOSING */
{
  const s = pres.addSlide();
  s.background = { color: C.discovery };
  s.addImage({ path: img("patterns/pattern-icon-band-white.png"), x: 0, y: 6.62, w: 13.333, h: 0.88, transparency: 86, sizing: { type: "cover", w: 13.333, h: 0.88 } });
  s.addImage({ path: img("logos/logo-horizontal-cool-white.png"), x: M, y: 0.5, w: 1.28, h: 0.6 });

  s.addText("WHERE VERSION 2 LANDS", { x: M, y: 1.5, w: 9, h: 0.3, fontFace: FH, bold: true, fontSize: 12, color: C.science, charSpacing: 2.2, margin: 0, valign: "middle" });
  s.addText("Rank a few good options.\nLet a person choose between them.", {
    x: M, y: 1.9, w: 11.5, h: 1.1, fontFace: FH, bold: true, fontSize: 27, color: C.coolWhite, lineSpacing: 35, charSpacing: -0.2, margin: 0, valign: "top" });

  rr(s, { x: M, y: 3.2, w: 12.1, h: 1.3, r: 0.16, fill: C.coolWhite });
  mathFit(s, "final", { cx: W / 2, y: 3.2, h: 0.86, maxW: 11.4 });

  const take = [
    ["Reweighted", "Family history now leads at 0.30, and the term behind it was rewritten so it finally matches its own label."],
    ["Honest about burden", "Hours and travel minutes instead of visit counts and miles, so a long assessment day counts as one."],
    ["Still unfinished", "Calendar access, new hire cold start, and joint scheduling are open. The pilot exists to close them."],
  ];
  const cw = 3.86, gap = 0.3;
  take.forEach((t, k) => {
    const x = M + k * (cw + gap);
    s.addText(t[0], { x, y: 4.9, w: cw, h: 0.34, fontFace: FH, bold: true, fontSize: 16, color: C.coolWhite, margin: 0, valign: "middle" });
    s.addShape(pres.ShapeType.rect, { x, y: 5.27, w: 0.7, h: 0.035, fill: { color: C.science }, line: { type: "none" } });
    s.addText(t[1], { x, y: 5.42, w: cw, h: 1.0, fontFace: FB, fontSize: 12, color: C.coolWhite, lineSpacing: 16, margin: 0, valign: "top" });
  });
  s.addNotes("Ask for two decisions today: the starting weights, and whether IRB allows reading staff calendars.");
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));

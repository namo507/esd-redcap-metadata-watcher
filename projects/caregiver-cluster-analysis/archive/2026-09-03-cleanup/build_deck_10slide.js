const pptxgen = require("pptxgenjs");
const path = require("path");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.author = "Early Social Development Lab";
pres.company = "University of South Carolina";
pres.title = "Caregiver Acceptability and 4-Month Autism Screening";

const C = {
  discovery: "3366FF",
  science: "91BAF4",
  coolBlue: "E6EEFC",
  coolWhite: "F4F4F6",
  jet: "000000",
  orange: "F57F00",
  grey: "52525B",
  greyLight: "71717A",
  white: "FFFFFF",
  peach: "FDF0E0",
};
const FH = "Libre Franklin";
const FB = "Libre Franklin Medium";

const SK = "/sessions/happy-eloquent-pascal/mnt/.claude/skills/esd-lab/assets";
const OUT = "/sessions/happy-eloquent-pascal/mnt/caregiver-cluster-analysis/Caregiver Outputs";
const A = {
  logoWhite: `${SK}/logos/logo-horizontal-cool-white.png`,
  logoBlue: `${SK}/logos/logo-horizontal-discovery-blue.png`,
  uofsc: `${SK}/logos/uofsc-horizontal-garnet.png`,
  band: `${SK}/patterns/pattern-icon-band-white.png`,
  sunburst: `${SK}/icons/sunburst-cool-white.png`,
};
const fig = (n) => `${OUT}/${n}`;

let no = 0;

function head(s, title) {
  no += 1;
  s.background = { color: C.white };
  s.addImage({ path: A.logoBlue, x: 0.5, y: 0.32, h: 0.29, w: 1.29 });
  s.addImage({ path: A.uofsc, x: 1.95, y: 0.32, h: 0.29, w: 1.08 });
  s.addText(title, {
    x: 0.5, y: 0.86, w: 12.33, h: 0.56, margin: 0,
    fontFace: FH, fontSize: 29, bold: true, color: C.jet, charSpacing: -0.3,
  });
  s.addText(String(no), {
    x: 12.45, y: 6.95, w: 0.4, h: 0.24, margin: 0,
    fontFace: FB, fontSize: 10, color: C.science, align: "right",
  });
}

function foot(s, text) {
  s.addText(text, {
    x: 0.5, y: 6.93, w: 11.7, h: 0.28, margin: 0,
    fontFace: FB, fontSize: 10, color: C.grey,
  });
}

function cap(s, x, y, w, text) {
  s.addText(text, { x, y, w, h: 0.26, margin: 0, fontFace: FB, fontSize: 9, color: C.greyLight });
}

function rule(s, x, y, w) {
  s.addShape(pres.ShapeType.rect, { x, y, w, h: 0.011, fill: { color: C.coolBlue }, line: { color: C.coolBlue } });
}

function stat(s, x, y, w, big, label, sub, color, size) {
  s.addText(big, {
    x, y, w, h: 0.58, margin: 0,
    fontFace: FH, fontSize: size || 38, bold: true, color: color || C.discovery, charSpacing: -0.5,
  });
  s.addText(label, { x, y: y + 0.6, w, h: 0.26, margin: 0, fontFace: FH, fontSize: 12, bold: true, color: C.jet });
  if (sub) s.addText(sub, { x, y: y + 0.85, w, h: 0.3, margin: 0, fontFace: FB, fontSize: 10.5, color: C.grey });
}

// =========================================================
// 1 - Title
// =========================================================
{
  const s = pres.addSlide();
  no += 1;
  s.background = { color: C.discovery };
  s.addImage({ path: A.band, x: 0, y: 0, w: 13.333, h: 0.58, transparency: 62 });
  s.addImage({ path: A.band, x: 0, y: 6.92, w: 13.333, h: 0.58, transparency: 62 });
  s.addImage({ path: A.logoWhite, x: 0.7, y: 1.35, h: 0.4, w: 1.77 });

  s.addText("Caregiver Acceptability and\n4-Month Autism Screening", {
    x: 0.7, y: 2.2, w: 11.6, h: 1.5, margin: 0,
    fontFace: FH, fontSize: 42, bold: true, color: C.coolWhite, charSpacing: -0.4, lineSpacing: 46,
  });
  s.addText("Grouping caregivers by how acceptable they find screening, and what that predicts", {
    x: 0.7, y: 3.78, w: 11.6, h: 0.36, margin: 0,
    fontFace: FB, fontSize: 17, color: C.science,
  });

  s.addShape(pres.ShapeType.rect, { x: 0.7, y: 4.5, w: 1.5, h: 0.03, fill: { color: C.science }, line: { color: C.science } });

  s.addText("131 caregivers split into two groups. 70.0% of one group would definitely screen at 4 months, against 27.7% of the other.", {
    x: 0.7, y: 4.76, w: 11.4, h: 0.36, margin: 0, fontFace: FH, fontSize: 15.5, bold: true, color: C.coolWhite,
  });
  s.addText("A 42.3 point difference that held up under every check we ran.", {
    x: 0.7, y: 5.16, w: 11.4, h: 0.32, margin: 0, fontFace: FB, fontSize: 14, color: C.coolBlue,
  });

  s.addText("Namit   ·   Early Social Development Lab   ·   2026-08-09", {
    x: 0.7, y: 6.2, w: 11.6, h: 0.28, margin: 0, fontFace: FB, fontSize: 11, color: C.science,
  });
  s.addNotes(
`Question: can we group caregivers by how acceptable they find infant screening, and does that grouping tell us anything.
Answer: yes on both, with one caveat about how many groups there really are.
Ten slides, about ten minutes. Data, method, results, then what I still do not know.`
  );
}

// =========================================================
// 2 - How the analysis works
// =========================================================
{
  const s = pres.addSlide();
  head(s, "How the analysis works");

  const steps = [
    ["1", "Score 10 acceptability areas", "Each survey item is rescaled to 0 to 100. Negative items are flipped so higher always means more accepting."],
    ["2", "Keep caregivers with enough data", "At least 8 of the 10 areas answered.  131 of 135 qualify, 97.0%."],
    ["3", "Split into groups", "Sort caregivers by how alike their ten scores are. Two groups: 84 and 47."],
    ["4", "Compare on things kept out", "Screening intention, autism knowledge, parental values, demographics. None of these help form the groups."],
  ];
  let y = 1.9;
  steps.forEach((st) => {
    s.addShape(pres.ShapeType.ellipse, { x: 0.5, y: y + 0.02, w: 0.44, h: 0.44, fill: { color: C.coolBlue }, line: { color: C.coolBlue } });
    s.addText(st[0], { x: 0.5, y: y + 0.08, w: 0.44, h: 0.32, margin: 0, fontFace: FH, fontSize: 16, bold: true, color: C.discovery, align: "center" });
    s.addText(st[1], { x: 1.15, y: y + 0.02, w: 4.0, h: 0.32, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.jet });
    s.addText(st[2], { x: 5.3, y: y + 0.02, w: 7.5, h: 0.5, margin: 0, fontFace: FB, fontSize: 12.5, color: C.grey });
    y += 0.68;
    rule(s, 0.5, y, 12.33);
    y += 0.28;
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 5.75, w: 12.33, h: 0.95, rectRadius: 0.12,
    fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("Why step 4 matters: if screening intention helped build the groups, any difference we found afterwards would be something we put there ourselves.", {
    x: 0.8, y: 5.98, w: 11.7, h: 0.5, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: C.discovery,
  });
  s.addNotes(
`Four steps, no more than that.
The ten areas come from theory, not from searching the data, so they stay interpretable.
The 8-of-10 rule was set before we looked at any result. Only four caregivers fail it.
Two groups was the choice, and slide 6 shows how well it fits.
Step four is the discipline that makes the result mean anything. Screening stays out of group formation entirely.`
  );
}

// =========================================================
// 3 - The data
// =========================================================
{
  const s = pres.addSlide();
  head(s, "The data we have");

  s.addText("TWO REDCAP PROJECTS", { x: 0.5, y: 1.85, w: 6.3, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });

  const projRows = [
    ["Project 4797", "177 records", "324 fields", "Current, used for the analysis", C.discovery],
    ["Project 4581", "1,779 records", "315 fields", "Older, kept for comparison only", C.orange],
  ];
  let y = 2.16;
  projRows.forEach((r) => {
    s.addText(r[0], { x: 0.5, y, w: 1.7, h: 0.28, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: r[4] });
    s.addText(r[1], { x: 2.3, y, w: 1.35, h: 0.28, margin: 0, fontFace: FB, fontSize: 13, color: C.jet });
    s.addText(r[2], { x: 3.7, y, w: 1.2, h: 0.28, margin: 0, fontFace: FB, fontSize: 13, color: C.jet });
    s.addText(r[3], { x: 5.0, y, w: 1.85, h: 0.4, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
    y += 0.42;
    rule(s, 0.5, y, 6.35);
    y += 0.24;
  });

  s.addText("311 fields are shared. 13 exist only in 4797, 4 only in 4581.", {
    x: 0.5, y: 3.6, w: 6.35, h: 0.3, margin: 0, fontFace: FB, fontSize: 12.5, color: C.grey,
  });

  s.addText("THREE CHECKS 4797 HAS AND 4581 DOES NOT", { x: 0.5, y: 4.15, w: 6.3, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  const checks = [
    ["Picture question", "Asks what a parent would do in a photo. Cannot be answered from text alone."],
    ["Age cross-check", "Compares the age given at sign-up with the child's date of birth later on."],
    ["Email field", "Lets us spot repeat and throwaway addresses."],
  ];
  y = 4.46;
  checks.forEach((c) => {
    s.addText(c[0], { x: 0.5, y, w: 2.0, h: 0.28, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.jet });
    s.addText(c[1], { x: 2.6, y, w: 4.25, h: 0.5, margin: 0, fontFace: FB, fontSize: 11.5, color: C.grey });
    y += 0.62;
  });

  s.addImage({ path: fig("figure_7_timing_ecdf.png"), x: 7.35, y: 1.9, w: 5.48, h: 3.49 });
  cap(s, 7.35, 5.43, 5.48, "How long people took to finish. Shaded area is faster than any verified caregiver.");

  s.addShape(pres.ShapeType.roundRect, {
    x: 7.35, y: 5.78, w: 5.48, h: 0.88, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("The fastest of 131 real caregivers took 11.57 minutes. In 4581, 12.5% finished faster than that.", {
    x: 7.55, y: 5.9, w: 5.1, h: 0.65, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.discovery,
  });

  foot(s, "Both projects record how long each of the four survey sections took, so speed checks work in either one.");
  s.addNotes(
`Two projects. 4797 is the one we analyse. 4581 is older and larger and we keep it only for comparison.
The three checks on the left are the reason 4797 is trustworthy and 4581 is not. They were added to the newer version.
The chart is the single clearest picture of the problem. Not one of 131 real caregivers finished in under 11.57 minutes. In the old project, one in eight did.
That gives us a threshold we did not have to invent.`
  );
}

// =========================================================
// 4 - Screening out bad responses
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Screening out bad responses");

  s.addText("NINE RULES, EACH WITH A FIXED THRESHOLD", { x: 0.5, y: 1.85, w: 7.5, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });

  const rules = [
    ["Finished the whole survey faster than 11.57 min", "0.0%"],
    ["Finished the main section faster than 7.85 min", "0.0%"],
    ["Any section faster than the slowest 1% of real caregivers", "4.6%"],
    ["Gave nearly the same answer to everything", "3.8%"],
    ["Answer pattern identical to another person", "0.0%"],
    ["Three or more submissions within 60 minutes", "2.3%"],
    ["Written answers nearly identical to someone else's", "0.0%"],
    ["Family answers contradict each other", "0.0%"],
    ["Demographics that cannot be true", "1.5%"],
  ];
  s.addText("RULE", { x: 0.5, y: 2.18, w: 6.0, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.greyLight, charSpacing: 0.5 });
  s.addText("WRONGLY FLAGS", { x: 6.4, y: 2.18, w: 1.6, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.greyLight, charSpacing: 0.5, align: "right" });
  let y = 2.46;
  rules.forEach((r) => {
    s.addText(r[0], { x: 0.5, y, w: 5.8, h: 0.28, margin: 0, fontFace: FB, fontSize: 12, color: C.jet });
    s.addText(r[1], { x: 6.4, y, w: 1.6, h: 0.28, margin: 0, fontFace: FH, fontSize: 12, bold: true, color: r[1] === "0.0%" ? C.discovery : C.orange, align: "right" });
    y += 0.32;
    rule(s, 0.5, y, 7.5);
    y += 0.14;
  });

  s.addText("Rates measured against the 131 caregivers we know are real.", {
    x: 0.5, y: 6.62, w: 7.5, h: 0.28, margin: 0, fontFace: FB, fontSize: 10.5, color: C.grey,
  });

  s.addText("WHAT THE RULES FOUND", { x: 8.4, y: 1.85, w: 4.4, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  stat(s, 8.4, 2.2, 4.4, "5 of 177", "Flagged in project 4797", "2.8% of the current project", C.discovery, 34);
  stat(s, 8.4, 3.55, 4.4, "1,043 of 1,779", "Flagged in project 4581", "58.6% of the old project", C.orange, 34);

  s.addShape(pres.ShapeType.roundRect, {
    x: 8.4, y: 4.95, w: 4.4, h: 1.3, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("Only 3 flagged records fall inside the 135 caregivers we analyse. Two for impossible demographics, one for speed and repetition together.", {
    x: 8.62, y: 5.1, w: 3.96, h: 1.0, margin: 0, fontFace: FB, fontSize: 12, color: C.jet,
  });
  s.addNotes(
`Nine rules, each a fixed threshold written down before we used it.
The right-hand column is what makes them defensible. Each rule was run against caregivers we know are real, and the number is how often it wrongly flagged them. Five rules never do.
The two projects come out completely differently. Under three percent flagged in the current project, nearly sixty percent in the old one.
The number that matters most for today: only three flagged records sit inside the 135 we analyse. The cohort was already clean.`
  );
}

// =========================================================
// 5 - Who is in the analysis
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Who is in the analysis");

  const steps = [
    ["177", "In project 4797", "Everyone who started"],
    ["135", "Selected", "Matched the study's cohort file"],
    ["131", "Grouped", "Answered 8 of 10 areas  ·  97.0%"],
    ["127", "Answered the screening question", "Missing stays missing"],
  ];
  const w = 2.82, gap = 0.35;
  steps.forEach((st, i) => {
    const x = 0.5 + i * (w + gap);
    const last = i === 3;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 2.05, w, h: 1.95, rectRadius: 0.14,
      fill: { color: last ? C.peach : C.coolBlue }, line: { color: last ? C.peach : C.coolBlue },
    });
    s.addText(st[0], { x: x + 0.22, y: 2.22, w: w - 0.44, h: 0.8, margin: 0, fontFace: FH, fontSize: 52, bold: true, color: last ? C.orange : C.discovery, charSpacing: -1 });
    s.addText(st[1], { x: x + 0.22, y: 3.04, w: w - 0.44, h: 0.48, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: C.jet });
    s.addText(st[2], { x: x + 0.22, y: 3.5, w: w - 0.44, h: 0.4, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
    if (!last) {
      s.addShape(pres.ShapeType.rightArrow, {
        x: x + w + 0.04, y: 2.92, w: 0.27, h: 0.24,
        fill: { color: C.science }, line: { color: C.science },
      });
    }
  });

  const drops = [
    ["42 dropped", "not in the study's cohort file"],
    ["4 dropped", "answered fewer than 8 of the 10 areas"],
    ["4 dropped", "gave no screening answer"],
  ];
  let y = 4.5;
  drops.forEach((d) => {
    s.addText([
      { text: d[0], options: { bold: true, color: C.jet } },
      { text: "   " + d[1], options: { color: C.grey } },
    ], { x: 0.5, y, w: 6.2, h: 0.3, margin: 0, fontFace: FB, fontSize: 13.5 });
    y += 0.42;
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 7.1, y: 4.42, w: 5.73, h: 1.5, rectRadius: 0.12, fill: { color: C.peach }, line: { color: C.peach },
  });
  s.addText("Worth saying plainly", { x: 7.35, y: 4.56, w: 5.2, h: 0.28, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.orange });
  s.addText("None of those last 4 said Definitely yes. Dropping them is not neutral, so we tested every way of assigning them. The difference stays between 39.0 and 44.5 points.", {
    x: 7.35, y: 4.9, w: 5.2, h: 0.9, margin: 0, fontFace: FB, fontSize: 12, color: C.jet,
  });

  foot(s, "Every count here comes from a saved, checksummed copy of the data, so the same numbers come back on a rerun.");
  s.addNotes(
`Four numbers, each drop with a stated reason.
The 42 are people outside the study's own cohort file. That selection rule predates this analysis and is one thing I would still like documented.
Only four people fail the 8-of-10 rule, so the data is in good shape.
The last four matter more than they look. None said Definitely yes, so removing them helps our result. We tested all four ways of putting them back and the difference never drops below 39 points.`
  );
}

// =========================================================
// 6 - How well the two groups fit
// =========================================================
{
  const s = pres.addSlide();
  head(s, "How well the two groups fit");

  s.addImage({ path: fig("figure_16_case_silhouette.png"), x: 7.35, y: 1.85, w: 5.48, h: 4.15 });
  cap(s, 7.35, 6.04, 5.48, "Fit for each of the 131 caregivers. Bars below zero sit closer to the other group.");

  stat(s, 0.5, 1.9, 3.2, "84 / 47", "Group sizes", "64.1% and 35.9% of 131", C.discovery, 34);
  stat(s, 4.0, 1.9, 3.0, "0.180", "Separation score", "0 means no gap, 1 is perfect", C.orange, 34);
  stat(s, 0.5, 3.3, 3.2, "0.704", "Repeatability", "Same split found again in 100 reruns", C.discovery, 34);
  stat(s, 4.0, 3.3, 3.0, "97.0%", "Had enough data", "131 of 135", C.discovery, 34);

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 4.7, w: 6.5, h: 1.95, rectRadius: 0.12, fill: { color: C.peach }, line: { color: C.peach },
  });
  s.addText("How to read these two together", { x: 0.75, y: 4.86, w: 6.0, h: 0.28, margin: 0, fontFace: FH, fontSize: 13.5, bold: true, color: C.orange });
  s.addText("Repeatability of 0.704 means we keep finding the same split. Separation of 0.180 means the two groups sit close together.\n\nBoth are true. These are useful working groups along a scale, not two different kinds of people.", {
    x: 0.75, y: 5.2, w: 6.0, h: 1.3, margin: 0, fontFace: FB, fontSize: 12.5, color: C.jet,
  });

  foot(s, "Two other fit measures point to three groups rather than two. Slide 10 covers what that means.");
  s.addNotes(
`Four numbers describe the fit.
Sizes are usable. Neither group is so small that comparisons fall apart.
Read repeatability and separation together. We find the same split every time we resample, and that split is not a wide gap.
Both can hold at once. It means we are describing a scale rather than two categories, and I keep the language matched to that all the way through.
The chart shows it honestly. A handful of people sit on the wrong side of the line and I have left them visible.`
  );
}

// =========================================================
// 7 - What separates the groups
// =========================================================
{
  const s = pres.addSlide();
  head(s, "What separates the groups");

  const rows = [
    ["Positive feelings and trust in the clinician", "73.6", "47.7", "25.9"],
    ["Willing to do a blood draw", "70.7", "44.9", "25.7"],
    ["Feels right to offer it to everyone", "87.7", "62.3", "25.3"],
    ["Low worry or fear", "82.9", "60.1", "22.8"],
    ["Thinks screening is useful", "83.7", "64.5", "19.2"],
    ["Accepts a less-than-perfect test", "44.6", "26.6", "18.0"],
    ["Willing to do a scan", "47.4", "29.9", "17.5"],
    ["Finds the visits manageable", "73.1", "55.8", "17.3"],
    ["Open to simple options like video or saliva", "91.1", "74.5", "16.6"],
    ["Low disgust or anger", "97.6", "93.4", "4.2"],
  ];

  s.addText("AREA", { x: 0.5, y: 1.83, w: 4.4, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.greyLight, charSpacing: 0.5 });
  s.addText("HIGHER", { x: 5.6, y: 1.83, w: 1.2, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.discovery, charSpacing: 0.5, align: "right" });
  s.addText("CONDITIONAL", { x: 6.95, y: 1.83, w: 1.5, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.orange, charSpacing: 0.5, align: "right" });
  s.addText("GAP", { x: 8.6, y: 1.83, w: 0.9, h: 0.22, margin: 0, fontFace: FH, fontSize: 9.5, bold: true, color: C.greyLight, charSpacing: 0.5, align: "right" });

  let y = 2.14;
  rows.forEach((r, i) => {
    const big = i < 3;
    s.addText(r[0], { x: 0.5, y, w: 5.0, h: 0.3, margin: 0, fontFace: big ? FH : FB, fontSize: 13, bold: big, color: C.jet });
    s.addText(r[1], { x: 5.6, y, w: 1.2, h: 0.3, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, align: "right" });
    s.addText(r[2], { x: 6.95, y, w: 1.5, h: 0.3, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, align: "right" });
    s.addText(r[3], { x: 8.6, y, w: 0.9, h: 0.3, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: big ? C.discovery : C.grey, align: "right" });
    y += 0.335;
    rule(s, 0.5, y, 9.0);
    y += 0.135;
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 9.8, y: 2.14, w: 3.03, h: 2.05, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("The three biggest gaps are all about 25 points", { x: 10.02, y: 2.3, w: 2.6, h: 0.6, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.discovery });
  s.addText("Trust in the clinician, willingness to do a blood draw, and whether offering it to everyone feels right.", {
    x: 10.02, y: 2.94, w: 2.6, h: 1.1, margin: 0, fontFace: FB, fontSize: 11.5, color: C.jet,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 9.8, y: 4.42, w: 3.03, h: 2.05, rectRadius: 0.12, fill: { color: C.peach }, line: { color: C.peach },
  });
  s.addText("Neither group is refusing", { x: 10.02, y: 4.58, w: 2.6, h: 0.3, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.orange });
  s.addText("Both score above 74 on the simplest options, and both are near the top on disgust and anger. The split is about effort and trust, not about being against it.", {
    x: 10.02, y: 4.92, w: 2.6, h: 1.4, margin: 0, fontFace: FB, fontSize: 11.5, color: C.jet,
  });

  foot(s, "All scores run 0 to 100, where higher always means more accepting. Averages across 84 and 47 caregivers.");
  s.addNotes(
`This is the table I would want if I were in the audience. Every area, both averages, and the gap.
The three biggest gaps are all around twenty-five points: trust in the clinician, willingness to do a blood draw, and whether offering it to everyone feels right.
The smallest gap is disgust and anger, four points, and both groups are near the top of that scale. Nobody finds the idea offensive.
Note the bottom row of the ranking. Both groups sit above 74 on simple options like video and saliva. The Conditional group is not saying no, it is saying not like that.
Practical read: reduce the burden and build trust, rather than trying to educate.`
  );
}

// =========================================================
// 8 - The screening question
// =========================================================
{
  const s = pres.addSlide();
  head(s, "The screening question");

  s.addText("“Would you have your baby screened for autism at their 4-month check-up?”", {
    x: 0.5, y: 1.75, w: 12.33, h: 0.32, margin: 0, fontFace: FB, fontSize: 14, italic: true, color: C.grey,
  });

  stat(s, 0.5, 2.25, 3.9, "56 / 80", "Higher acceptability", "70.0% said Definitely yes   ·   95% range 59.2 to 78.9", C.discovery);
  stat(s, 4.7, 2.25, 3.9, "13 / 47", "Conditional acceptability", "27.7% said Definitely yes   ·   95% range 16.9 to 41.8", C.orange);
  stat(s, 8.9, 2.25, 3.9, "42.3 pts", "Difference", "95% range 17.5 to 62.0   ·   odds ratio 8.09", C.discovery);

  s.addImage({ path: fig("figure_3_screening_outcome.png"), x: 1.35, y: 3.85, w: 10.63, h: 2.37 });
  cap(s, 1.35, 6.26, 10.63, "Definitely-yes rate by group with 95% ranges, and the full answer mix, n = 127.");

  foot(s, "This is what people said they would do, not what they did. Screening was never used to form the groups.");
  s.addNotes(
`This is the finding, and it is large by any standard.
Seventy percent against twenty-eight, a forty-two point difference, with ranges that do not come close to touching.
The counts differ, 80 and 47, because four people in the Higher group gave no answer. That is why I show counts rather than percentages alone.
Look at the right-hand chart too. It is not just Definitely yes. Forty percent of the Conditional group land in no, probably not, or unsure, against ten percent in the Higher group.
Say the caveat once: this is stated intention, not behaviour.`
  );
}

// =========================================================
// 9 - Everything else we checked
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Everything else we checked");

  s.addText("DOES THE RESULT SURVIVE DIFFERENT INCLUSION RULES?", { x: 0.5, y: 1.83, w: 6.2, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  stat(s, 0.5, 2.14, 3.0, "42.3 – 48.5", "Difference in points", "Three ways of choosing who is in", C.discovery, 30);
  stat(s, 3.7, 2.14, 3.0, "7.4 – 11.1", "Odds ratio", "Every range stays above 1", C.discovery, 30);

  rule(s, 0.5, 3.5, 6.2);

  s.addText("OTHER MEASURES", { x: 0.5, y: 3.72, w: 6.2, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  const rows = [
    ["Autism knowledge", "3.90 vs 3.41 out of 7", "p = 0.099", "No difference", C.greyLight],
    ["Parental values", "Biggest gap is self-direction", "adjusted p = 0.129", "No difference", C.greyLight],
    ["Premature birth", "25/84 (30%) vs 22/47 (47%)", "p = 0.051, adjusted p = 0.562", "Suggestive only", C.orange],
    ["Autistic child at home", "4.3 times the odds of screening", "95% range 1.78 to 10.50, p = 0.0012", "Real, and separate", C.discovery],
  ];
  let y = 3.98;
  rows.forEach((r) => {
    s.addText(r[0], { x: 0.5, y, w: 2.2, h: 0.28, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.jet });
    s.addText(r[1], { x: 2.8, y, w: 2.55, h: 0.28, margin: 0, fontFace: FB, fontSize: 11.5, color: C.jet });
    s.addText(r[3], { x: 0.5, y: y + 0.28, w: 2.2, h: 0.25, margin: 0, fontFace: FB, fontSize: 10.5, bold: true, color: r[4] });
    s.addText(r[2], { x: 2.8, y: y + 0.28, w: 3.9, h: 0.25, margin: 0, fontFace: FB, fontSize: 10.5, color: C.grey });
    y += 0.6;
    rule(s, 0.5, y, 6.2);
    y += 0.12;
  });

  s.addImage({ path: fig("figure_20_tier_sensitivity.png"), x: 7.1, y: 1.95, w: 5.73, h: 3.29 });
  cap(s, 7.1, 5.28, 5.73, "The difference and its 95% range under five ways of choosing who to include.");

  s.addShape(pres.ShapeType.roundRect, {
    x: 7.1, y: 5.66, w: 5.73, h: 1.0, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("What predicts screening is how caregivers feel about it, plus whether they already have an autistic child. Not what they know, and not who they are.", {
    x: 7.32, y: 5.78, w: 5.3, h: 0.78, margin: 0, fontFace: FB, fontSize: 12, color: C.jet,
  });

  foot(s, "With 47 people in the smaller group, a difference under about 26 points would be too small for us to detect, so a null here means too little data rather than no difference.");
  s.addNotes(
`Top left answers the obvious challenge. Change who counts as included and the difference moves between forty-two and forty-nine points, the odds ratio between seven and eleven. Stable.
Below it, the honest summary of everything else. Knowledge does not separate the groups. Values do not survive testing ten of them at once.
Premature birth is the one to be careful with. Thirty against forty-seven percent looks real but it is borderline before correction and nowhere near after. It is a hypothesis for the next round.
The one solid extra factor is having an autistic child at home, about four times the odds, and it works alongside group rather than through it.
The footer matters. With forty-seven people, no difference found is not the same as no difference.`
  );
}

// =========================================================
// 10 - What holds, what does not
// =========================================================
{
  const s = pres.addSlide();
  head(s, "What holds, what does not");

  s.addText("HOLDS", { x: 0.5, y: 1.85, w: 6.0, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  s.addText([
    { text: "The 42.3 point screening difference, under every inclusion rule we tried.", options: { bullet: true, breakLine: true } },
    { text: "The current cohort is clean: only 3 flagged records among 135.", options: { bullet: true, breakLine: true } },
    { text: "The analysis reruns and reproduces all 17 earlier results exactly.", options: { bullet: true } },
  ], { x: 0.5, y: 2.16, w: 6.0, h: 1.5, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, paraSpaceAfter: 8 });

  s.addText("DOES NOT", { x: 6.83, y: 1.85, w: 6.0, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.orange, charSpacing: 0.6 });
  s.addText([
    { text: "Two groups. One fit measure prefers three, and a different method splits 131 into 110 and 21 rather than 84 and 47.", options: { bullet: true, breakLine: true } },
    { text: "The 4581 comparison. It differs in questionnaire and timing, so we cannot separate bad responses from an ordinary cohort difference.", options: { bullet: true } },
  ], { x: 6.83, y: 2.16, w: 6.0, h: 1.5, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, paraSpaceAfter: 8 });

  rule(s, 0.5, 3.92, 12.33);

  s.addText("NEXT", { x: 0.5, y: 4.14, w: 6.0, h: 0.24, margin: 0, fontFace: FH, fontSize: 10.5, bold: true, color: C.discovery, charSpacing: 0.6 });
  const acts = [
    ["1", "Collect more clean records", "Keep all three checks switched on"],
    ["2", "Document the cohort file and answer key", "Two open items"],
    ["3", "Rerun the checks as numbers grow", "Same five inclusion rules"],
    ["4", "Say groups, not types", "In the write-up"],
  ];
  const aw = 3.0, ag = 0.11;
  acts.forEach((a, i) => {
    const x = 0.5 + i * (aw + ag);
    s.addText(a[0], { x, y: 4.5, w: 0.9, h: 0.32, margin: 0, fontFace: FH, fontSize: 20, bold: true, color: C.science });
    s.addText(a[1], { x, y: 4.88, w: aw - 0.15, h: 0.56, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.jet });
    s.addText(a[2], { x, y: 5.46, w: aw - 0.15, h: 0.44, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 6.05, w: 12.33, h: 0.78, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("For the clinic: keep the option low-effort, and lead with trust and consent rather than with facts about autism.", {
    x: 0.8, y: 6.16, w: 11.7, h: 0.58, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: C.discovery,
  });
  s.addNotes(
`Left column is what I would defend today. Right column is what I would not.
The screening difference is large and it moved very little under every inclusion rule we tried.
What I will not claim is that there are exactly two kinds of caregiver. One fit measure prefers three groups, and a different method splits the same 131 people 110 to 21. There is structure, but its shape is unsettled.
Item two on the next list is what I need from this room: the cohort selection rule and one knowledge answer key are still undocumented.
The bottom line is the practical one. Lower the effort, lead with trust and consent. Teaching people more about autism is not what moves this.`
  );
}

const outPath = path.join("/sessions/happy-eloquent-pascal/mnt/outputs", "ESD_Caregiver_Cluster_Analysis_10.pptx");
pres.writeFile({ fileName: outPath }).then(() => console.log("WROTE " + outPath));

const pptxgen = require("pptxgenjs");
const path = require("path");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.author = "Early Social Development Lab";
pres.company = "University of South Carolina";
pres.title = "Caregiver Acceptability Profiles for 4-Month Autism Screening";

// ---------- ESD canon tokens ----------
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

let slideNo = 0;

function head(s, title) {
  slideNo += 1;
  s.background = { color: C.white };
  s.addImage({ path: A.logoBlue, x: 0.5, y: 0.34, h: 0.3, w: 1.34 });
  s.addImage({ path: A.uofsc, x: 2.0, y: 0.34, h: 0.3, w: 1.12 });
  s.addText(title, {
    x: 0.5, y: 0.92, w: 12.33, h: 0.6, margin: 0,
    fontFace: FH, fontSize: 30, bold: true, color: C.jet, charSpacing: -0.3,
  });
  s.addText(String(slideNo), {
    x: 12.5, y: 6.92, w: 0.4, h: 0.24, margin: 0,
    fontFace: FB, fontSize: 10, color: C.science, align: "right",
  });
}

// small unlabelled footer line, plain language, no callout box
function foot(s, text) {
  s.addText(text, {
    x: 0.5, y: 6.9, w: 11.6, h: 0.28, margin: 0,
    fontFace: FB, fontSize: 10, color: C.grey,
  });
}

function caption(s, x, y, w, text) {
  s.addText(text, { x, y, w, h: 0.26, margin: 0, fontFace: FB, fontSize: 9, color: C.greyLight });
}

// big number block, no card chrome
function stat(s, x, y, w, big, label, sub, color) {
  s.addText(big, {
    x, y, w, h: 0.62, margin: 0,
    fontFace: FH, fontSize: 40, bold: true, color: color || C.discovery, charSpacing: -0.5,
  });
  s.addText(label, {
    x, y: y + 0.64, w, h: 0.26, margin: 0,
    fontFace: FH, fontSize: 12.5, bold: true, color: C.jet,
  });
  if (sub) {
    s.addText(sub, {
      x, y: y + 0.9, w, h: 0.28, margin: 0,
      fontFace: FB, fontSize: 10.5, color: C.grey,
    });
  }
}

function rule(s, x, y, w) {
  s.addShape(pres.ShapeType.rect, { x, y, w, h: 0.012, fill: { color: C.coolBlue }, line: { color: C.coolBlue } });
}

// =========================================================
// 1 - Title
// =========================================================
{
  const s = pres.addSlide();
  slideNo += 1;
  s.background = { color: C.discovery };
  s.addImage({ path: A.band, x: 0, y: 0, w: 13.333, h: 0.6, transparency: 62 });
  s.addImage({ path: A.band, x: 0, y: 6.9, w: 13.333, h: 0.6, transparency: 62 });
  s.addImage({ path: A.logoWhite, x: 0.7, y: 1.3, h: 0.42, w: 1.86 });

  s.addText("Caregiver Acceptability Profiles for\n4-Month Autism Screening", {
    x: 0.7, y: 2.15, w: 11.6, h: 1.55, margin: 0,
    fontFace: FH, fontSize: 42, bold: true, color: C.coolWhite, charSpacing: -0.4, lineSpacing: 46,
  });
  s.addText("Reproducible Upgrade and Robustness Results", {
    x: 0.7, y: 3.76, w: 11.6, h: 0.4, margin: 0,
    fontFace: FB, fontSize: 19, color: C.science,
  });

  s.addShape(pres.ShapeType.rect, { x: 0.7, y: 4.46, w: 1.6, h: 0.03, fill: { color: C.science }, line: { color: C.science } });

  s.addText("Project 4797 (n=177) is the primary cohort. Project 4581 (n=1,779) is replication only.", {
    x: 0.7, y: 4.72, w: 11.4, h: 0.32, margin: 0, fontFace: FB, fontSize: 14, color: C.coolBlue,
  });
  s.addText("The screening difference held up under every check. The number of profiles did not settle.", {
    x: 0.7, y: 5.12, w: 11.4, h: 0.32, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.coolWhite,
  });

  s.addText("Namit   ·   2026-07-30", {
    x: 0.7, y: 6.15, w: 11.6, h: 0.28, margin: 0, fontFace: FB, fontSize: 11, color: C.science,
  });
  s.addNotes(
`Methods and robustness update, not a new finding.
One line to hold onto: the screening difference survived every check we ran, the profile count did not settle.
4797 is the clean project. 4581 is older, has no eligibility checks, and stays as replication only.
Roughly a minute a slide, twelve minutes, then questions.`
  );
}

// =========================================================
// 2 - Questions
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Two questions");

  s.addText("01", { x: 0.5, y: 2.05, w: 0.9, h: 0.5, margin: 0, fontFace: FH, fontSize: 30, bold: true, color: C.science });
  s.addText("Can caregivers be grouped by their acceptability patterns?", {
    x: 1.55, y: 2.05, w: 10.9, h: 0.5, margin: 0, fontFace: FH, fontSize: 22, bold: true, color: C.jet,
  });
  s.addText("Ten acceptability domains, scored 0 to 100, are the only inputs.", {
    x: 1.55, y: 2.58, w: 10.9, h: 0.3, margin: 0, fontFace: FB, fontSize: 13, color: C.grey,
  });

  rule(s, 0.5, 3.32, 12.33);

  s.addText("02", { x: 0.5, y: 3.62, w: 0.9, h: 0.5, margin: 0, fontFace: FH, fontSize: 30, bold: true, color: C.science });
  s.addText("Do the groups differ on things we never used to build them?", {
    x: 1.55, y: 3.62, w: 10.9, h: 0.5, margin: 0, fontFace: FH, fontSize: 22, bold: true, color: C.jet,
  });
  s.addText("Screening intention, autism knowledge, parental values, demographics and family context.", {
    x: 1.55, y: 4.15, w: 10.9, h: 0.3, margin: 0, fontFace: FB, fontSize: 13, color: C.grey,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 5.0, w: 12.33, h: 1.0, rectRadius: 0.12,
    fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("Those four outcomes stay out of the grouping entirely. They are looked at only after the groups are fixed.", {
    x: 0.8, y: 5.24, w: 11.7, h: 0.55, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.discovery,
  });
  s.addNotes(
`Two questions, nothing else.
The grouping runs on acceptability alone.
Screening, knowledge, values, and demographics are set aside until the groups are frozen. Otherwise any difference we find is just something we built in.
Cluster naming also excludes accuracy tolerance so the labels cannot be circular.`
  );
}

// =========================================================
// 3 - Cohort flow
// =========================================================
{
  const s = pres.addSlide();
  head(s, "From 177 records to 127");

  const steps = [
    ["177", "Pulled from REDCap", "Project 4797"],
    ["135", "Selected for analysis", "Documented selection file"],
    ["131", "Grouped", "8 of 10 domains answered"],
    ["127", "Screening answer given", "Missing left as missing"],
  ];
  const w = 2.82, gap = 0.35;
  steps.forEach((st, i) => {
    const x = 0.5 + i * (w + gap);
    const last = i === 3;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 2.15, w, h: 1.85, rectRadius: 0.14,
      fill: { color: last ? C.peach : C.coolBlue }, line: { color: last ? C.peach : C.coolBlue },
    });
    s.addText(st[0], { x: x + 0.22, y: 2.32, w: w - 0.44, h: 0.8, margin: 0, fontFace: FH, fontSize: 52, bold: true, color: last ? C.orange : C.discovery, charSpacing: -1 });
    s.addText(st[1], { x: x + 0.22, y: 3.14, w: w - 0.44, h: 0.3, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: C.jet });
    s.addText(st[2], { x: x + 0.22, y: 3.46, w: w - 0.44, h: 0.4, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
    if (!last) {
      s.addShape(pres.ShapeType.rightArrow, {
        x: x + w + 0.04, y: 2.97, w: 0.27, h: 0.24,
        fill: { color: C.science }, line: { color: C.science },
      });
    }
  });

  s.addText([
    { text: "42 dropped", options: { bold: true, color: C.jet } },
    { text: " outside the selection file", options: { color: C.grey, breakLine: true } },
    { text: "4 dropped", options: { bold: true, color: C.jet } },
    { text: " below the 8-of-10 domain rule", options: { color: C.grey, breakLine: true } },
    { text: "4 dropped", options: { bold: true, color: C.jet } },
    { text: " with no screening answer", options: { color: C.grey } },
  ], { x: 0.5, y: 4.55, w: 6.0, h: 1.2, margin: 0, fontFace: FB, fontSize: 13.5, paraSpaceAfter: 8 });

  s.addText("None of the last 4 answered Definitely yes, so dropping them is not neutral. Slide 8 shows the full range.", {
    x: 7.0, y: 4.55, w: 5.83, h: 0.9, margin: 0, fontFace: FB, fontSize: 12.5, color: C.jet,
  });

  foot(s, "Pulls are cached and hashed, so every count here traces back to a specific row.");
  s.addNotes(
`Four numbers, each drop with a stated reason.
177 is what the API returns for 4797. The code asserts that and stops if it changes.
The 8-of-10 rule was set before we looked at results.
The last four matter: none of them answered Definitely yes, so removing them is not neutral. Slide 8 quantifies it.`
  );
}

// =========================================================
// 4 - What changed
// =========================================================
{
  const s = pres.addSlide();
  head(s, "What changed");

  const rows = [
    ["Data source", "Local CSV, no checks", "Hashed API pulls, row counts asserted"],
    ["Bad responses", "No screen", "10 rules, 4 trust levels, all 1,956 records"],
    ["Rule accuracy", "Not measured", "Every rule checked against 131 real caregivers"],
    ["Exports", "Record-level fields included", "Aggregate only"],
    ["Old results", "Checked by hand", "17 automated checks, all pass"],
  ];
  const x0 = 0.5, cA = 2.5, cB = 4.2, cC = 5.63;
  s.addText("", { x: 0, y: 0, w: 0.1, h: 0.1 });
  s.addText("BEFORE", { x: x0 + cA, y: 1.92, w: cB, h: 0.22, margin: 0, fontFace: FH, fontSize: 10, bold: true, color: C.greyLight, charSpacing: 0.6 });
  s.addText("AFTER", { x: x0 + cA + cB, y: 1.92, w: cC, h: 0.22, margin: 0, fontFace: FH, fontSize: 10, bold: true, color: C.orange, charSpacing: 0.6 });

  let y = 2.2;
  rows.forEach((r) => {
    s.addText(r[0], { x: x0, y: y + 0.06, w: cA - 0.2, h: 0.4, margin: 0, fontFace: FH, fontSize: 13.5, bold: true, color: C.jet, valign: "middle" });
    s.addText(r[1], { x: x0 + cA, y: y + 0.06, w: cB - 0.3, h: 0.4, margin: 0, fontFace: FB, fontSize: 12.5, color: C.greyLight, valign: "middle" });
    s.addText(r[2], { x: x0 + cA + cB, y: y + 0.06, w: cC - 0.2, h: 0.4, margin: 0, fontFace: FB, fontSize: 12.5, color: C.jet, valign: "middle" });
    y += 0.56;
    rule(s, x0, y, 12.33);
    y += 0.26;
  });

  s.addText([
    { text: "1,956 / 1,956", options: { bold: true, color: C.discovery } },
    { text: " records given a trust level        ", options: { color: C.grey } },
    { text: "17 / 17", options: { bold: true, color: C.discovery } },
    { text: " old results reproduced", options: { color: C.grey } },
  ], { x: 0.5, y: 6.55, w: 12.33, h: 0.32, margin: 0, fontFace: FB, fontSize: 13.5 });
  s.addNotes(
`What we actually did since last time.
The pipeline now pulls from the API, hashes what it caches, and refuses to run if the counts drift.
The response screen is a set of fixed rules, written down before use, so they can be argued with.
Each rule's error rate was measured against caregivers we know are real. That is what makes the thresholds defensible.
All seventeen old numbers reproduce exactly, so nothing in this deck moved by accident.`
  );
}

// =========================================================
// 5 - Grouping
// =========================================================
{
  const s = pres.addSlide();
  head(s, "How the groups were found");

  s.addImage({ path: fig("figure_1_cluster_count_diagnostics.png"), x: 4.9, y: 1.8, w: 7.93, h: 4.15 });
  caption(s, 4.9, 5.98, 7.93, "Figure 1. Fit measures for 2 to 6 groups, n = 131.");

  s.addText("Ten acceptability domains, scored 0 to 100 and standardised, then K-means.", {
    x: 0.5, y: 1.85, w: 4.1, h: 0.68, margin: 0, fontFace: FB, fontSize: 12.5, color: C.grey,
  });

  stat(s, 0.5, 2.68, 4.1, "2 groups", "Chosen solution", null, C.discovery);
  stat(s, 0.5, 4.02, 4.1, "0.180", "Silhouette", "Weak separation", C.orange);
  stat(s, 0.5, 5.24, 4.1, "0.704", "Stability (ARI)", "100 resamples of 80% of the data", C.discovery);

  foot(s, "Two other fit measures, BIC and the gap statistic, both point to more than two groups. Slide 11 covers this.");
  s.addNotes(
`Ten domains built from theory, not from a data search, so they stay interpretable.
Read the two numbers together. Stability 0.704 means we recover the same split when we resample. Silhouette 0.180 means the two groups sit close together.
Both can be true. We can find the same split repeatedly and still be describing a gradient rather than a gap.
BIC and the gap statistic disagree with two groups. I come back to that on slide eleven.`
  );
}

// =========================================================
// 6 - The two groups
// =========================================================
{
  const s = pres.addSlide();
  head(s, "The two groups");

  s.addImage({ path: fig("figure_2_primary_cluster_profiles.png"), x: 4.4, y: 1.78, w: 8.43, h: 4.38 });
  caption(s, 4.4, 6.2, 8.43, "Figure 2. Average domain scores by group, n = 131. Dashed ticks are the overall average.");

  s.addShape(pres.ShapeType.roundRect, { x: 0.5, y: 1.85, w: 3.6, h: 0.95, rectRadius: 0.12, fill: { color: C.discovery }, line: { color: C.discovery } });
  s.addText("84", { x: 0.72, y: 1.94, w: 1.3, h: 0.5, margin: 0, fontFace: FH, fontSize: 30, bold: true, color: C.coolWhite });
  s.addText("of 131  ·  64.1%", { x: 1.85, y: 2.08, w: 2.0, h: 0.28, margin: 0, fontFace: FB, fontSize: 12, color: C.science });
  s.addText("Higher acceptability", { x: 0.72, y: 2.44, w: 3.2, h: 0.28, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.coolWhite });

  s.addShape(pres.ShapeType.roundRect, { x: 0.5, y: 2.92, w: 3.6, h: 0.95, rectRadius: 0.12, fill: { color: C.orange }, line: { color: C.orange } });
  s.addText("47", { x: 0.72, y: 3.01, w: 1.3, h: 0.5, margin: 0, fontFace: FH, fontSize: 30, bold: true, color: C.coolWhite });
  s.addText("of 131  ·  35.9%", { x: 1.85, y: 3.15, w: 2.0, h: 0.28, margin: 0, fontFace: FB, fontSize: 12, color: C.coolWhite });
  s.addText("Conditional acceptability", { x: 0.72, y: 3.51, w: 3.2, h: 0.28, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.coolWhite });

  s.addText("What separates them", { x: 0.5, y: 4.14, w: 3.6, h: 0.26, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.discovery });
  s.addText([
    { text: "Trust and comfort with the clinician", options: { bullet: true, breakLine: true } },
    { text: "Willingness to do a blood draw", options: { bullet: true, breakLine: true } },
    { text: "Whether offering it to everyone feels right", options: { bullet: true } },
  ], { x: 0.5, y: 4.44, w: 3.6, h: 1.2, margin: 0, fontFace: FB, fontSize: 12, color: C.jet, paraSpaceAfter: 6 });

  foot(s, "Both groups stay open to low-burden options. The split is about burden and trust, not blanket refusal.");
  s.addNotes(
`Sizes first: 84 and 47 of the 131 grouped records.
Labels come from average acceptability across the aligned inputs, with accuracy tolerance left out so the naming cannot be circular.
What separates them is feelings and logistics, not information. Trust, comfort with specific procedures, and whether offering it to everyone feels right.
I say group and gradient, not type. That follows from the silhouette on the last slide.`
  );
}

// =========================================================
// 7 - Screening intention
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Would you screen at 4 months?");

  stat(s, 0.5, 1.9, 3.9, "56 / 80", "Higher acceptability", "70.0% said Definitely yes   ·   95% CI 59.2 to 78.9", C.discovery);
  stat(s, 4.7, 1.9, 3.9, "13 / 47", "Conditional acceptability", "27.7% said Definitely yes   ·   95% CI 16.9 to 41.8", C.orange);
  stat(s, 8.9, 1.9, 3.9, "42.3 pp", "Difference", "Odds ratio 8.09   ·   95% CI 3.47 to 20.80", C.discovery);

  s.addImage({ path: fig("figure_3_screening_outcome.png"), x: 1.3, y: 3.5, w: 10.73, h: 2.39 });
  caption(s, 1.3, 5.94, 10.73, "Figure 3. Definitely-yes rate by group with 95% intervals, n = 127 who answered.");

  foot(s, "This is a stated intention, not screening behaviour. Screening was never used to build the groups.");
  s.addNotes(
`This is the finding the lab cares about, and it is large.
Seventy percent against twenty-eight, a forty-two point difference, with intervals nowhere near overlapping.
The endpoint follows the written analysis plan: Definitely yes against every other valid answer, missing left missing.
The two bottom numbers differ, 80 and 47, because four Higher records had no answer. That is why I show counts.
Say the caveat out loud: stated intention is not behaviour.`
  );
}

// =========================================================
// 8 - Robustness
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Does it survive the screening rules?");

  s.addImage({ path: fig("figure_20_tier_sensitivity.png"), x: 5.15, y: 1.85, w: 7.68, h: 4.41 });
  caption(s, 5.15, 6.3, 7.68, "Figure 20. Difference with 95% intervals across all five ways of choosing who to include.");

  stat(s, 0.5, 1.9, 4.4, "42.3 – 48.5 pp", "Difference stays in this range", "Across the three clean-project definitions", C.discovery);
  stat(s, 0.5, 3.28, 4.4, "7.39 – 11.09", "Odds ratio stays in this range", "Every interval sits above 1", C.discovery);
  stat(s, 0.5, 4.66, 4.4, "39.0 – 44.5 pp", "Worst case for the 4 dropped records", "Even the least favourable split holds", C.orange);

  s.addText("The result holds.", {
    x: 0.5, y: 5.92, w: 4.4, h: 0.34, margin: 0, fontFace: FH, fontSize: 18, bold: true, color: C.discovery,
  });

  foot(s, "Rules and thresholds were fixed before we looked at what they did to the result. Definitions 4 and 5 mix or replace cohorts and are shown for comparison only.");
  s.addNotes(
`This answers the obvious challenge: did the response screening change the answer?
Across the three clean-project definitions the difference moves between forty-two and forty-nine points, odds ratio between seven and eleven. Stable.
Every interval excludes one.
The third number handles the four dropped records. Even if we assign all four the way that hurts most, the difference is still thirty-nine points.
Order matters: thresholds set first, applied second. We did not tune them while watching the result.`
  );
}

// =========================================================
// 9 - Other measures
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Everything else we looked at");

  const rows = [
    ["Autism knowledge", "3.90 (n=77) vs 3.41 (n=46) out of 7", "p = 0.099", "No difference", C.greyLight],
    ["Parental values", "Largest gap is self-direction, d = 0.48", "adjusted p = 0.129", "No difference", C.greyLight],
    ["Premature birth", "25/84 (30%) vs 22/47 (47%)", "p = 0.051, adjusted p = 0.562", "Suggestive only", C.orange],
    ["Autistic child at home", "Odds ratio 4.32, 95% CI 1.78 to 10.50", "p = 0.0012", "Real, and separate from group", C.discovery],
  ];
  let y = 1.95;
  rows.forEach((r) => {
    s.addText(r[0], { x: 0.5, y: y + 0.02, w: 2.5, h: 0.3, margin: 0, fontFace: FH, fontSize: 13.5, bold: true, color: C.jet });
    s.addText(r[1], { x: 3.05, y: y + 0.02, w: 3.5, h: 0.3, margin: 0, fontFace: FB, fontSize: 12, color: C.jet });
    s.addText(r[2], { x: 3.05, y: y + 0.34, w: 3.5, h: 0.28, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
    s.addText(r[3], { x: 0.5, y: y + 0.34, w: 2.5, h: 0.28, margin: 0, fontFace: FB, fontSize: 11, bold: true, color: r[4] });
    y += 0.72;
    rule(s, 0.5, y, 6.05);
    y += 0.28;
  });

  s.addImage({ path: fig("figure_18_logistic_forest.png"), x: 7.1, y: 1.9, w: 5.73, h: 3.3 });
  caption(s, 7.1, 5.24, 5.73, "Figure 18. Odds ratios for screening intention, n = 127.");

  s.addText("Having an autistic child at home raises the odds of screening about fourfold, whichever group a caregiver is in.", {
    x: 7.1, y: 5.7, w: 5.73, h: 0.6, margin: 0, fontFace: FB, fontSize: 12.5, color: C.jet,
  });

  foot(s, "With 47 people in the smaller group we could only detect a difference of about 26 points, so a null here means not enough data rather than no difference.");
  s.addNotes(
`The honest headline: the signal is about attitude and logistics, not knowledge or demographics.
Knowledge does not separate the groups on the seven-item score. The eight-item versions stay as backup because one answer key is still unresolved.
Self-direction looks interesting before correcting for ten comparisons and not after. I am not presenting it as a finding.
Premature birth is the one to caveat hardest. Thirty against forty-seven percent looks real but is borderline before correction and nowhere near after. The medical-fatigue story is a hypothesis.
The one solid covariate is an autistic child at home, about fourfold, and it works alongside group rather than through it.
Last point: with forty-seven in the smaller group, no difference found is not the same as no difference.`
  );
}

// =========================================================
// 10 - Project 4581
// =========================================================
{
  const s = pres.addSlide();
  head(s, "The old project, 4581");

  s.addImage({ path: fig("figure_7_timing_ecdf.png"), x: 7.55, y: 1.88, w: 5.28, h: 3.36 });
  caption(s, 7.55, 5.28, 5.28, "Figure 7. How long people took. The verified-human floor is 11.57 minutes.");

  stat(s, 0.5, 1.9, 3.4, "71 / 1,779", "Pass the strict rules", "4.0% of the old project", C.orange);
  stat(s, 4.1, 1.9, 3.1, "49", "Enough data to group", null, C.orange);
  stat(s, 0.5, 3.32, 6.7, "9.74", "Odds ratio in the 71", "95% CI 1.96 to 96.77   ·   same direction, far less precise", C.discovery);

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 4.62, w: 6.7, h: 1.55, rectRadius: 0.12, fill: { color: C.peach }, line: { color: C.peach },
  });
  s.addText("94.4% is not a bot rate", { x: 0.75, y: 4.76, w: 6.2, h: 0.3, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: C.orange });
  s.addText("A model separates the two projects at 94.4%. But they were run at different times with a different questionnaire, so that number mixes fraud with ordinary differences between cohorts. It cannot be read as a fraud rate.", {
    x: 0.75, y: 5.1, w: 6.2, h: 0.95, margin: 0, fontFace: FB, fontSize: 12, color: C.jet,
  });

  foot(s, "4581 has no picture check, no age cross-check, and no email field. That is why it is dirty, and why it stays a comparison rather than part of the main result.");
  s.addNotes(
`This is where the fraud problem lives. Seventy-one of one thousand seven hundred seventy-nine clear the strict rules.
Those seventy-one point the same way, odds ratio around ten, but look at the interval. Two to ninety-seven. Reassuring in direction, almost useless in size.
Be careful quoting the ninety-four percent. It is separation between two projects that differ in questionnaire and recruitment window, not just in authenticity.
A control check helps here: split 4797 in half at random and the same model gets 0.525, basically chance. So the separation is not something our pipeline invented.
The reason 4797 is clean is the picture check, the age cross-check, and the email field. Keep all three on every future collection.`
  );
}

// =========================================================
// 11 - Model checks
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Why we say the group count is unsettled");

  s.addImage({ path: fig("figure_16_case_silhouette.png"), x: 7.6, y: 1.88, w: 5.23, h: 3.96 });
  caption(s, 7.6, 5.88, 5.23, "Figure 16. Fit for each person. Bars below zero sit closer to the other group.");

  const items = [
    ["BIC prefers three groups", "1,697.9 for three against 2,329.7 for two.", C.orange],
    ["A different method splits it differently", "A mixture model gives 110 and 21, not 84 and 47.", C.orange],
    ["Separation is weak", "Silhouette 0.180, and some people sit on the wrong side.", C.orange],
    ["Structure does exist", "A one-group model is rejected, p = 0.0099. The shape is what is unclear.", C.discovery],
  ];
  let y = 1.95;
  items.forEach((it) => {
    s.addText(it[0], { x: 0.5, y, w: 6.7, h: 0.28, margin: 0, fontFace: FH, fontSize: 14, bold: true, color: it[2] });
    s.addText(it[1], { x: 0.5, y: y + 0.3, w: 6.7, h: 0.3, margin: 0, fontFace: FB, fontSize: 12.5, color: C.jet });
    y += 0.7;
    rule(s, 0.5, y, 6.7);
    y += 0.3;
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.5, y: 5.95, w: 6.7, h: 0.78, rectRadius: 0.12, fill: { color: C.coolBlue }, line: { color: C.coolBlue },
  });
  s.addText("Keep two groups because they are useful. Drop any wording that treats them as real kinds of people.", {
    x: 0.75, y: 6.06, w: 6.2, h: 0.58, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.discovery,
  });
  s.addNotes(
`I want to argue against our own structure before a reviewer does.
Three things point away from a clean two-group story: BIC prefers three, a mixture model splits 110 to 21 instead of 84 to 47, and separation stays weak with people visibly on the wrong side.
What does hold is that a single group is rejected outright. There is structure. We just cannot claim we found its true form.
So keep two groups because they are interpretable and useful, and drop the wording that treats them as real kinds of people.
For the manuscript: group, gradient, associated with. Not type, class, or leads to.`
  );
}

// =========================================================
// 12 - Where this leaves us
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Where this leaves us");

  s.addText("Solid", { x: 0.5, y: 1.95, w: 6.0, h: 0.3, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.discovery });
  s.addText([
    { text: "The screening difference is large and holds up: 42.3 to 48.5 points.", options: { bullet: true, breakLine: true } },
    { text: "The analysis reruns cleanly, with 17 of 17 old results reproduced.", options: { bullet: true } },
  ], { x: 0.5, y: 2.3, w: 6.0, h: 1.1, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, paraSpaceAfter: 8 });

  s.addText("Open", { x: 6.83, y: 1.95, w: 6.0, h: 0.3, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.orange });
  s.addText([
    { text: "How many groups there really are.", options: { bullet: true, breakLine: true } },
    { text: "How much of the 4581 separation is fraud rather than a different cohort.", options: { bullet: true } },
  ], { x: 6.83, y: 2.3, w: 6.0, h: 1.1, margin: 0, fontFace: FB, fontSize: 13, color: C.jet, paraSpaceAfter: 8 });

  rule(s, 0.5, 3.72, 12.33);

  s.addText("Next", { x: 0.5, y: 3.98, w: 6.0, h: 0.3, margin: 0, fontFace: FH, fontSize: 15, bold: true, color: C.discovery });
  const acts = [
    ["01", "Collect and check more clean records", "Keep the eligibility checks on"],
    ["02", "Settle the knowledge key and selection file", "Two open documentation items"],
    ["03", "Rerun the checks as the sample grows", "All five inclusion definitions"],
    ["04", "Reword the manuscript", "Group and gradient, not type"],
  ];
  const aw = 3.0, ag = 0.11;
  acts.forEach((a, i) => {
    const x = 0.5 + i * (aw + ag);
    s.addText(a[0], { x, y: 4.38, w: 1.0, h: 0.32, margin: 0, fontFace: FH, fontSize: 18, bold: true, color: C.science });
    s.addText(a[1], { x, y: 4.74, w: aw - 0.15, h: 0.56, margin: 0, fontFace: FH, fontSize: 12.5, bold: true, color: C.jet });
    s.addText(a[2], { x, y: 5.32, w: aw - 0.15, h: 0.44, margin: 0, fontFace: FB, fontSize: 11, color: C.grey });
  });

  foot(s, "Everything here is an association within an exploratory grouping. Nothing supports targeting caregivers by demographics.");
  s.addNotes(
`Two columns because the split between solid and open is the message.
Solid: the difference itself, and the fact that the analysis now reruns cleanly.
Open: how many groups there are, and how to read the 4581 separation.
Item two is what I need from this room. The knowledge answer key and the selection rule are documentation gaps.
Item four is a writing task, cheap now and expensive in review.
Practical read: prioritise a low-burden pathway, lead with trust and ethics.`
  );
}

// =========================================================
// 13 - Backup
// =========================================================
{
  const s = pres.addSlide();
  head(s, "Backup slides");

  const app = [
    ["A1", "The 10 screening rules and thresholds"],
    ["A2", "Rule error rates against 131 real caregivers"],
    ["A3", "Agreement between the different detectors"],
    ["A4", "Full table of all five inclusion definitions"],
    ["A5", "Complete model output"],
    ["A6", "Group-count diagnostics in full"],
    ["A7", "Field differences between the two projects"],
    ["A8", "The 17 reproduced results"],
    ["A9", "How small a difference we could detect"],
  ];
  let y = 2.0;
  let col = 0;
  let rowInCol = 0;
  app.forEach((a, i) => {
    if (i === 5) { y = 2.0; col = 1; rowInCol = 0; }
    const x = 0.5 + col * 6.33;
    s.addText(a[0], { x, y, w: 0.6, h: 0.3, margin: 0, fontFace: FH, fontSize: 13, bold: true, color: C.science });
    s.addText(a[1], { x: x + 0.7, y, w: 5.2, h: 0.3, margin: 0, fontFace: FB, fontSize: 13, color: C.jet });
    y += 0.42;
    rule(s, x, y, 6.0);
    y += 0.3;
    rowInCol += 1;
  });

  foot(s, "Each one is already an exported table or figure, so any of them can be opened during questions.");
  s.addNotes(
`Do not walk this slide. It exists so questions have somewhere to land.
Everything listed is already exported, so I can open any of it live.
Most likely to come up: A2 on rule error rates, A3 on detector agreement, A6 on group count.`
  );
}

// =========================================================
// 14 - Closing line
// =========================================================
{
  const s = pres.addSlide();
  slideNo += 1;
  s.background = { color: C.discovery };
  s.addImage({ path: A.band, x: 0, y: 0, w: 13.333, h: 0.6, transparency: 62 });
  s.addImage({ path: A.band, x: 0, y: 6.9, w: 13.333, h: 0.6, transparency: 62 });
  s.addImage({ path: A.sunburst, x: 11.2, y: 1.55, w: 1.4, h: 1.4, transparency: 60 });

  s.addText("The screening difference is solid.\nThe number of groups is not.", {
    x: 0.9, y: 2.7, w: 11.3, h: 1.8, margin: 0,
    fontFace: FH, fontSize: 38, bold: true, color: C.coolWhite, charSpacing: -0.3, lineSpacing: 48,
  });
  s.addText("42.3 to 48.5 point difference   ·   odds ratio 7.39 to 11.09   ·   17 of 17 old results reproduced", {
    x: 0.9, y: 4.72, w: 11.3, h: 0.32, margin: 0, fontFace: FB, fontSize: 13.5, color: C.coolBlue,
  });
  s.addImage({ path: A.logoWhite, x: 0.9, y: 5.5, h: 0.4, w: 1.77 });
  s.addText("Early Social Development Lab  ·  Institute for Mind and Brain  ·  University of South Carolina", {
    x: 0.9, y: 6.02, w: 11.3, h: 0.28, margin: 0, fontFace: FB, fontSize: 10.5, color: C.science,
  });
  s.addNotes(
`One sentence to leave the room with, two clauses.
Solid: the screening difference survived every check.
Not settled: how many groups there are. Better we say that now than a reviewer later.
Recommendation is unchanged: low-burden pathway, lead with trust and ethics.
Open for questions, backup slides ready.`
  );
}

const outPath = path.join("/sessions/happy-eloquent-pascal/mnt/outputs", "ESD_Caregiver_Robustness_Update.pptx");
pres.writeFile({ fileName: outPath }).then(() => console.log("WROTE " + outPath));

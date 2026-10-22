# Parent Screening Attitudes: Notebook Refinements

**Purpose:** Show which patterns of parent attitudes appear in the survey, how dependable those patterns are, and how they can inform more acceptable infant screening research.

**Document status:** Notebook improvement plan—not a newly executed analysis. Numerical starting points below are saved outputs from the attached `caregiver_cluster_simple_metrics.ipynb`, not results regenerated for this document.

**Interpretation rule:** Clusters describe patterns in reported answers. They do not reveal parents’ “true feelings,” establish causes, or guarantee clearly separated groups.

## Start With Research Questions

The proposal examines screening acceptability in Aim 1 and prediagnostic intervention acceptability in Aim 2, including differences by family experience and sociocultural context. Source: `Aims_ResearchStrategy_BradshawR21.pdf`, Specific Aims and Specific Aim Overview and Outcomes.

| Research question | Notebook output | What it could reveal |
|---|---|---|
| Do parents express different combinations of support and concern? | Attitude-profile table | Supportive parents may still have emotional or practical reservations |
| How do income, education, and race relate to the profiles? | Adjusted comparisons with uncertainty ranges | Whether differences remain after considering other measured characteristics |
| How do values and family experience relate to attitudes? | Comparison of profiles using these separately measured characteristics | Which priorities and experiences are associated with each profile |
| Which screening options are acceptable? | Method-by-profile preference chart | Whether concerns differ across observation, saliva testing, blood testing, or imaging |
| Does support for screening extend to intervention? | Screening–intervention comparison, if measured | Whether identification and intervention require different conversations |
| Does recruitment affect the findings? | Results separated by recruitment source | Whether research-connected and community-recruited parents express different perspectives |

**Keep explanations separate from evidence:** A pattern suggesting practical barriers should lead to follow-up questions—not an automatic conclusion that cost, mistrust, or intervention fatigue caused hesitation.

## Reorganize the Notebook

Use the same format in every section: **question → simple explanation → table or chart → interpretation → limitation**.

| Notebook section | Main improvement | Stakeholder-facing output |
|---|---|---|
| Who participated? | Freeze the verified dataset and document exclusions | Participant-count flowchart |
| What was measured? | Show question wording, scoring direction, and missing answers | Question-to-score table |
| What patterns appear? | Compare alternative grouping approaches | Profile comparison chart |
| Can the groups overlap? | Show assignment probabilities | Confidence distribution |
| Are the patterns dependable? | Repeat the analysis under different reasonable choices | Reliability scorecard |
| Who reports each pattern? | Compare demographics without using them to create the primary profiles | Adjusted difference chart |
| What should change? | Connect observed concerns to possible study-design changes | Evidence-to-action table |

## Protect the Analysis

### Keep Three Variable Roles

| Role | Examples | Rule |
|---|---|---|
| Create attitude profiles | Feelings, perceived usefulness, practical burden, ethical concerns | Use a documented set of attitude questions |
| Explain profile differences | Income, race/ethnicity, education, values, family experience, recruitment source | Keep outside the primary grouping step |
| Check practical relevance | Screening intention or intervention willingness | Do not use the same answer to create and validate the profiles |

**Important:** If screening-method preferences help create the groups, differences in those preferences describe the groups; they are not independent confirmation. A stronger check would rebuild profiles without those questions and then compare method preferences.

### Handle Missing Answers Carefully

- Separate unanswered questions from questions that were intentionally skipped.
- Estimate plausible missing answers several times rather than filling every blank with one average.
- Repeat grouping across those completed datasets and report whether conclusions change.
- Retain all verified respondents in the participation summary, but identify respondents with too little attitude information for dependable classification.
- Present unweighted results first. Add population-adjusted results only when a clearly defined target population and suitable external benchmarks exist.
- Treat equal recruitment-source weighting as a sensitivity check—not proof of community representativeness.

## Make the Logic Visible

### Analysis Architecture

```text
Verified responses
        |
        v
Check coding, missing answers, and question availability
        |
        v
Convert selected attitudes to comparable scores
        |
        v
Estimate missing answers in several reasonable ways
        |
        v
Compare possible attitude profiles
        |
        v
Measure overlap and repeatability
        |
        v
Compare separately measured demographics and intentions
        |
        v
Translate supported patterns into proposals for parent review
```

### Explain the Calculations

| Calculation | Mathematical rule | Plain-language meaning |
|---|---|---|
| Put an answer on a common scale | \(s=100(x-L)/(U-L)\) | Convert the lowest possible answer to 0 and the highest to 100 |
| Reverse a negatively worded answer | \(s_{\mathrm{reversed}}=100-s\) | Make the direction consistent where appropriate |
| Combine related answers | \(D=\sum s_j/m\) | Average the available, relevant answers |
| Describe assignment confidence | \(c_i=\max_g p_{ig}\) | Show how strongly a parent’s answers fit the best-fitting profile |
| Estimate a probability-weighted profile share | \(\pi_g=\sum_i w_i p_{ig}/\sum_i w_i\) | Count uncertain assignments proportionally rather than treating them as certain |

Here, \(L\) and \(U\) are the question’s allowed limits; \(m\) is the number of included answers; \(p_{ig}\) is the estimated probability of profile \(g\); and \(w_i=1\) for an unweighted analysis.

**Scoring caution:** A 0–100 attitude score is not a percentage of parents. Requiring high screening accuracy should not automatically be scored as lower acceptance.

### Choose Groups Without Forcing Them

- Keep **k-means**, which assigns each parent to one group, as the existing comparison.
- Add a **Gaussian mixture model**, which provides probabilities for alternative profiles.
- For individual ordered survey answers, consider a model designed for answer categories rather than assuming the answers behave like continuous measurements.
- Include a one-profile comparison: the data may support a broad continuum rather than distinct groups.
- Do not select the number of groups from one score alone.

**Terminology correction:** Gaussian mixture probabilities represent uncertainty about group assignment; they do not establish that a parent genuinely belongs to several underlying groups. Topic-model LDA should not be the default for structured attitude ratings.

## Show Readable Metric Tables

### Verified Starting Point

These are saved results from the attached notebook, not results regenerated for this document. Source: `caregiver_cluster_simple_metrics.ipynb`, saved outputs in sections 3.1–3.3.

| Measure | Saved result | Meaning |
|---|---:|---|
| Verified respondents | 664 | Starting participant pool |
| Included in existing clustering | 642 | Met the existing attitude-score completeness rule |
| Left out of existing clustering | 22 | Had fewer than eight of ten scores |
| Existing two-group separation score | 0.185 | Report overlap explicitly; do not describe the groups as sharply distinct |

### Reliability Scorecard Preview

The completed notebook should populate this table automatically. This is an output specification, not a table of newly calculated metrics.

| Display label | What to calculate | How to explain it |
|---|---|---|
| Group separation | Silhouette score, from −1 to 1 | Higher means clearer separation; this is not an accuracy percentage |
| Repeatability | Agreement after repeated resampling | Whether similar profiles return when the sample changes |
| Assignment confidence | Largest profile probability per parent | Whether individual assignments are clear or uncertain |
| Missing-answer sensitivity | Changes across completed datasets | Whether estimated missing answers change the conclusions |
| Recruitment sensitivity | Changes across sources and source-weighting choices | Whether one recruitment channel dominates the pattern |
| Group size | Respondent counts and estimated shares | Whether a profile is large enough to interpret responsibly |

Repeat the preprocessing and grouping steps during resampling. Match corresponding profiles before comparing them, because software group numbers can switch.

## Preview the Visuals

**These are layout previews—not additional findings.** Use calculated results only when producing the final charts.

### Participant Flow

```text
664 verified respondents
             |
      Existing completeness rule
             |
       +-----+-----+
       |           |
 642 included   22 excluded
```

These counts reflect the existing notebook analysis. Source: `caregiver_cluster_simple_metrics.ipynb`, section 3.2 saved output.

### Profile Comparison

**Recommended visual:** A horizontal dot chart.

```text
Attitude                         Profile comparison
Feels screening is useful        Group means + uncertainty ranges
Extra visits feel manageable     Group means + uncertainty ranges
Feels comfortable with testing   Group means + uncertainty ranges
Concern about receiving results  Group means + uncertainty ranges
```

- Use one shared 0–100 scale.
- Write “more concern” or “more agreement” explicitly for each score.
- Show uncertainty ranges and the number answering.
- Use neutral labels until the patterns justify descriptive names.

### Screening Preferences

**Recommended visual:** A table with gentle shading.

| Screening option, if measured | Profile-level result | Required context |
|---|---|---|
| Video observation | Percentage reporting acceptance | Number answering and uncertainty range |
| Saliva sampling | Percentage reporting acceptance | Number answering and uncertainty range |
| Blood testing | Percentage reporting acceptance | Number answering and uncertainty range |
| EEG or MRI | Percentage reporting acceptance | Display separately if asked separately |

Use the survey’s wording. Do not assume a method is acceptable, invasive, or burdensome on behalf of parents.

### Demographic Differences

**Recommended visual:** Horizontal dots with uncertainty ranges around adjusted differences.

- Display percentage-point differences where possible.
- State which characteristics were considered together.
- Show subgroup counts.
- Mark estimates with insufficient information as inconclusive.
- Do not treat equal profile proportions as the definition of fairness.

## Strengthen Interpretation

- **Avoid circular findings:** Differences in questions used to create profiles are descriptive—not independent discoveries.
- **Carry assignment uncertainty forward:** Do not treat uncertain group labels as perfectly observed facts when comparing demographics or intentions.
- **Check several comparisons responsibly:** Report difference sizes, uncertainty ranges, and corrections when testing many relationships.
- **Separate trust from general positivity:** Only claim healthcare trust was measured if suitable questions directly assess it.
- **Audit fairness:** Compare missingness, assignment confidence, and repeatability across SES and racial/ethnic groups; a nonsignificant difference does not prove fairness.
- **Avoid unsupported explanations:** Demographic associations do not establish financial hardship, mistrust, or cultural values as causes.
- **Validate future responses carefully:** Apply frozen scoring and grouping rules before considering model updates. New monthly respondents are not evidence that the same parents changed over time.
- **Reconcile earlier summaries:** Regenerate profile percentages from one documented dataset and model before reusing them.

## End With Supported Actions

| If the analysis supports… | A reasonable next proposal | What still needs testing |
|---|---|---|
| Support alongside emotional concern | Clearer result explanations and optional support | Whether parents find the approach helpful |
| Acceptance alongside visit burden | Flexible scheduling or fewer separate visits | Whether participation becomes more feasible |
| Different preferences across testing methods | Offer acceptable choices where scientifically feasible | Whether choices improve informed participation |
| Different views across recruitment sources | Expand recruitment and seek additional interviews | Whether previously missed perspectives emerge |
| Different screening and intervention preferences | Discuss these decisions separately | Whether separate conversations support understanding |

The research aims emphasize participant-informed, ethical research design—not validation of a particular biomarker or treatment. Proposed changes should therefore be reviewed with parents and autistic stakeholders before being presented as guidelines. Source: `Aims_ResearchStrategy_BradshawR21.pdf`, Significance and Expected Outcomes.

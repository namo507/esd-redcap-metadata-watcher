# Mullen Paper-Layout Replication and Automated Scoring Plan

## Confirmed source structure

The current electronic setup is split into two REDCap-style instruments:

1. **Mullen Items** — participant/evaluation metadata followed by five scored scales: Gross Motor, Visual Reception, Fine Motor, Receptive Language, and Expressive Language.
2. **Mullen Scores** — unadjusted and prematurity-adjusted outputs for each scale: Raw Score, T Score, Percentile Rank, Descriptive Category, and Age Equivalent.

The paper score sheet in `02 Scoring/Mullen Score Sheet Preterm.docx` uses one matrix with these columns:

| Scale | Raw Score | T Score | Percentile | Descriptive Category | Age Equivalent |
|---|---:|---:|---:|---|---|
| Gross Motor | | | | | |
| Visual Reception | | | | | |
| Fine Motor | | | | | |
| Receptive Language | | | | | |
| Expressive Language | | | | | |

It also contains: Participant ID, Visit Month, DOB, Date of Evaluation, Chronological Age, Gestational Age at Birth, Expected DOB, Adjusted Age, and Norms Used to Score.

## Target design

Build one paper-faithful assessment workflow with four visible sections:

1. **Paper header** — Participant ID, Visit Month, DOB, Evaluation Date, Examiner, Data Entry RA, and Date Entered.
2. **Item record form** — five scale sections in the same order as the paper protocol, showing item number, item wording, valid score choices, and optional administration note.
3. **Scoring worksheet** — the paper matrix reproduced exactly, with calculated fields visually distinguished from manually entered fields.
4. **Prematurity panel** — Gestational Age at Birth, Expected DOB, Chronological Age, Adjusted Age, Norms Used, and both chronological-age and adjusted-age score results.

Keep the authoritative score in one normalized item variable. The paper layout and any REDCap/mobile layout should be presentation layers over the same stored fields, not duplicate scoring fields.

## Data model

### Assessment metadata

- `participant_id`
- `visit_month`
- `date_of_birth`
- `evaluation_date`
- `examiner`
- `data_entry_ra`
- `date_entered`
- `gestational_age_weeks`
- `gestational_age_days`
- `expected_dob`
- `chronological_age_months`
- `adjusted_age_months`
- `norm_age_used_months`
- `prematurity_adjustment_applied`
- `norms_version`

### Item response table

- `scale_code`: GM, VR, FM, RL, EL
- `item_number`
- `item_label`
- `score_min`
- `score_max`
- `item_score`
- `administered_flag`
- `source_or_override`
- `override_reason`

### Norm lookup table

Do not hard-code proprietary norms into branching logic. Store authorized norms in a versioned table:

```text
norms_version
scale_code
norm_age_months
raw_score_min
raw_score_max
t_score
percentile_rank
age_equivalent_months
descriptive_category
source_page
verified_by
verified_date
```

Use one row for every valid age-band × scale × raw-score mapping. Add a uniqueness constraint on `(norms_version, scale_code, norm_age_months, raw_score_min, raw_score_max)` and reject overlapping raw-score ranges.

## Calculation rules

### Age calculation

1. Calculate chronological age from DOB to evaluation date using complete calendar months, retaining days for audit/display.
2. Compute weeks premature as `max(0, 40 weeks - gestational age at birth)`.
3. Compute expected DOB as DOB plus weeks/days premature.
4. Calculate adjusted age from expected DOB to evaluation date.
5. Apply prematurity adjustment only under the study's approved protocol. Store both chronological and adjusted values even when only one is used for norm lookup.
6. Record the exact norm age and norms version used for every conversion.

### Raw score

For each scale:

```text
raw_score = sum(valid item scores included by the approved basal/ceiling/discontinuation rules)
```

The engine must not infer basal, ceiling, reversal, or unadministered-item credit until those rules are transcribed and validated from the authorized manual. Before that validation, calculate only the transparent sum of entered item scores and label it `provisional_raw_score`.

### Raw-to-standard conversion

```text
norm_row = norms_table where
  norms_version = selected version
  and scale_code = current scale
  and norm_age_months = selected norm age
  and raw_score between raw_score_min and raw_score_max

T score              = norm_row.t_score
Percentile rank      = norm_row.percentile_rank
Age equivalent       = norm_row.age_equivalent_months
Descriptive category = norm_row.descriptive_category
```

Do not calculate T scores or percentiles from generic normal-distribution formulas. These outputs must come from the authorized Mullen lookup tables.

### Composite score

If the required overall composite is added later, calculate it only from the scales and conversion table explicitly authorized by the manual. Gross Motor should not be silently included in a composite unless the source rule requires it.

## Current-to-paper field map

| Current electronic field group | Paper destination | Action |
|---|---|---|
| Participant ID, Date of Evaluation, Examiner, RA, Date Entered | Paper header | Retain and reorder to paper layout |
| Individual GM/VR/FM/RL/EL item fields | Five paper item sections | Preserve score ranges exactly; add item numbers and compact paper rows |
| Scale raw score | Scoring matrix: Raw Score | Auto-calculate from item responses after scoring-rule validation |
| T Score | Scoring matrix: T Score | Lookup from age-specific norm table |
| Percentile Rank | Scoring matrix: Percentile | Lookup; read-only |
| Descriptive Category | Scoring matrix: category | Derive from authorized norm output/rule; read-only |
| Age Equivalent | Scoring matrix: Age Equivalent | Lookup; read-only |
| Adjusted-for-prematurity block | Prematurity panel and second score state | Remove duplicate manual entry; calculate from the same item raw scores using adjusted norm age |
| Chronological/gestational/expected/adjusted age | Paper footer/prematurity panel | Add auto-calculation and audit fields |
| Norms Used to Score | Paper footer | Auto-populate norms version plus chronological/adjusted status |

## Step-by-step checklist

### Phase 1 — Source control and verification

- [ ] Preserve read-only copies of both current exported PDFs and all paper-layout source files.
- [ ] Assign a controlled version identifier to the authorized scoring manual/norm tables.
- [ ] Confirm the exact item numbering, item wording, score range, basal rule, ceiling rule, reversal rule, discontinue rule, and missing/not-administered handling for every scale.
- [ ] Confirm the study rule for prematurity adjustment, including the maximum child age at which adjustment is permitted.
- [ ] Have a qualified Mullen assessor verify all transcribed rules and tables.

### Phase 2 — Paper-faithful UI

- [ ] Recreate the paper header in the same order, labels, grouping, and page width.
- [ ] Recreate every scale in paper order with fixed item numbers and only valid score options.
- [ ] Use print CSS with Letter-size pages, stable margins, repeated participant ID/page footer, controlled page breaks, and no clipped rows.
- [ ] Recreate the six-column score matrix exactly.
- [ ] Add the paper age/prematurity fields below the matrix.
- [ ] Distinguish calculated fields with read-only styling without changing paper labels.
- [ ] Add print preview and a print-to-PDF acceptance check.

### Phase 3 — Calculation engine

- [ ] Implement validated date parsing and prohibit evaluation dates before DOB.
- [ ] Calculate chronological age, expected DOB, adjusted age, and norm age.
- [ ] Implement item-range validation so impossible scores cannot be saved.
- [ ] Implement scale raw-score calculation from one authoritative item dataset.
- [ ] Add explicit basal/ceiling/reversal logic only after manual verification.
- [ ] Load authorized norm rows into the versioned lookup table.
- [ ] Convert raw score to T score, percentile, category, and age equivalent by exact lookup.
- [ ] Generate chronological-age and adjusted-age results from the same raw score rather than duplicate entry.
- [ ] Auto-fill `Norms Used to Score` with version, norm age, and adjustment status.
- [ ] Require a reason and preserve original values for any manual override.

### Phase 4 — Validation

- [ ] Unit-test every age boundary and month transition.
- [ ] Unit-test minimum, maximum, and every score-range boundary for each item.
- [ ] Test every norm age band at minimum and maximum raw scores.
- [ ] Test lookup gaps and overlaps; both must block final scoring.
- [ ] Compare at least two independently hand-scored cases per age band/scale with the automated result.
- [ ] Include preterm cases exactly on and around the adjustment cutoff.
- [ ] Perform independent double scoring and reconcile all discrepancies.
- [ ] Save expected results as regression fixtures.

### Phase 5 — Deployment

- [ ] Separate draft/provisional scores from verified/final scores.
- [ ] Restrict norms-table editing to authorized administrators.
- [ ] Record item edits, calculation version, norms version, overrides, scorer, and timestamp in an audit trail.
- [ ] Prevent edits after verification unless the record is explicitly unlocked.
- [ ] Pilot with research staff before production use.
- [ ] Obtain instrument/licensing and study-governance approval before distributing an electronic paper replica or embedded norms.

## Acceptance criteria

- Printed output matches the controlled paper score sheet in label order, table structure, page size, and grouping.
- A response is stored once and appears consistently in screen, print, export, and score calculation.
- Recalculation is deterministic and records the scoring-engine and norms versions.
- Every automated output can be traced to item responses, norm age, norm row, and adjustment decision.
- Automated results match independently verified hand scoring for the complete validation set.
- Missing rules or norm rows fail closed with a visible “cannot score” status; the system never guesses.

## Blocking inputs before final scoring implementation

1. An authorized, readable source for all Mullen norm conversion tables.
2. The official basal/ceiling/reversal/discontinuation rules for all five scales.
3. The exact project rule for prematurity adjustment and cutoff age.
4. Confirmation of whether the target implementation is REDCap, a standalone web application, or both.
5. Licensing/permission confirmation for reproducing item wording and norm tables electronically.

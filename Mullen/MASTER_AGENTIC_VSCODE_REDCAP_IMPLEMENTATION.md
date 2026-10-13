# Master Agentic VS Code + Playwright Runbook
## Mullen Paper Layout, Verified Norm Mapping, and REDCap Auto-Scoring

> **Execution target:** The existing REDCap project visible in the authenticated browser.
>
> **Agent mode:** VS Code coding agent with Playwright/browser tools and local workspace access.
>
> **Workspace root:** `/Users/namomac/Downloads/Mullen`
>
> **Primary rule:** Do not guess, interpolate, approximate, reverse-engineer, or statistically estimate any Mullen norm, basal rule, ceiling rule, age-equivalent value, T score, percentile, or composite score. Use only authorized source tables and rules that have been transcribed, independently verified, versioned, and approved.

---

## 1. Mission

Implement a production-ready REDCap version of the Mullen assessment that:

1. Reproduces the controlled paper layout and field order as closely as REDCap permits.
2. Stores every item response once and uses it for screen display, print output, raw scoring, norm conversion, export, and audit.
3. Calculates chronological age and approved prematurity-adjusted age deterministically.
4. Applies verified basal, reversal, ceiling, discontinuation, and unadministered-item rules.
5. Maps raw scores to T scores, percentiles, descriptive categories, age equivalents, and any approved composite through exact, versioned table lookups.
6. Fails closed when data, rules, or norm rows are missing or ambiguous.
7. Uses Playwright to inspect the current REDCap webpage dynamically, adapt to the actual interface, and verify every browser mutation.
8. Leaves an auditable implementation package containing source mappings, generated REDCap dictionary, tests, screenshots, traces, validation results, and rollback files.

The agent is not merely editing a form. It is implementing a controlled scoring system. Correctness, provenance, reversibility, and test evidence take priority over speed.

---

## 2. Authoritative Inputs

Use sources in this priority order:

1. `02 Scoring/Mullen Scoring.pdf` — authorized norm and reference-score source. It is scanned; OCR output is provisional until visually verified against every source cell.
2. `Mullen.pdf` and `Mullen Fine Motor Printout.pdf` — paper administration and layout sources.
3. `02 Scoring/Mullen Score Sheet Preterm.docx` — score-sheet layout, age, and prematurity fields.
4. Current REDCap project export and live browser state — authoritative for existing variable names, events, instruments, records, permissions, and production status.
5. `MullenItems_IPSALabAssessments.pdf` and `MullenScores_IPSALabAssessment.pdf` — current electronic behavior.
6. `Mullen_Paper_Layout_Data_Dictionary.csv` — implementation draft, not scoring authority.
7. `mullen_assessment_system.html` — visual prototype and item inventory, not scoring authority.
8. `implementation-plan.md` — approved architectural guardrails.
9. `MULLEN_NEW_DESIGN_IMPLEMENTATION.md` — design proposal only; any scoring formula in it must be treated as unverified until matched cell-for-cell to the authorized manual.
10. `01 Training/Mullen_03222022.docx` — local administration notes; never let training notes override the authorized scoring manual.

When sources conflict, stop the affected scoring path, record the conflict, and request a qualified assessor’s decision. Never silently choose one source.

---

## 3. Critical Findings to Correct

The present draft must not be deployed as authoritative scoring logic.

### 3.1 Standalone HTML risks

The current HTML:

- Estimates expected raw scores from linear age trajectories.
- Converts z scores to T scores and percentiles with a generic distribution formula.
- Estimates age equivalents with inverse linear equations.
- Estimates the Early Learning Composite with a linear formula.
- Falls back to 12 months when age is missing or invalid.
- Generates adjusted scores even when the approved prematurity policy has not been established.
- Uses a fixed “Credit Items 1–10” action that assigns each item’s maximum value without first proving the official basal rule.
- Produces scores from partially entered or empty forms instead of failing closed.

**Required action:** quarantine these functions as prototype-only. Replace `computeNorms`, generic percentile conversion, formula-based age equivalents, formula-based composite conversion, the 12-month fallback, and fixed basal buttons with verified table/rule-driven logic. Do not transfer these approximations into REDCap.

### 3.2 Current data dictionary gaps

The draft dictionary has 237 fields and correctly inventories 159 item fields across five scales, with maximum item sums of GM 36, VR 50, FM 49, RL 48, and EL 50. However:

- T scores, percentiles, age equivalents, descriptive categories, and composite outputs remain manual fields.
- Prematurity-adjusted standard outputs remain manual fields.
- Raw scores are simple arithmetic sums and do not yet prove official basal, reversal, ceiling, discontinuation, missingness, or not-administered handling.
- `chronological_age_d` is total elapsed days, not residual days in a years/months/days age display.
- `weeks_preterm` ignores gestational days.
- `expected_dob` currently ignores gestational days.
- `is_preterm` is manually selected although it should be derived from validated gestational age under the approved protocol.
- Only a few fields are required, so an incomplete record can appear scorable.

**Required action:** generate a corrected dictionary from verified machine-readable rules and norms. Do not hand-edit hundreds of formulas independently.

---

## 4. Non-Negotiable Safety Rules

1. **Development first.** Never upload a dictionary, modify fields, run bulk recalculation, or alter records in a production project without explicit human approval.
2. **Backup first.** Before any mutation, export and timestamp the current data dictionary, project XML if permitted, event/instrument mapping, reports relevant to this instrument, and a de-identified test-record export.
3. **No credential automation.** The human completes login, password manager, SSO, Duo/MFA, CAPTCHA, and reauthentication. Never store credentials in files, code, traces, screenshots, or logs.
4. **No PHI in artifacts.** Use synthetic record IDs and dates for screenshots/tests. Mask any visible real identifiers in Playwright screenshots and traces.
5. **No guessed norms.** Missing or unreadable norm cells produce `SCORING_BLOCKED`, not an estimate.
6. **No silent overwrite.** Preserve current variables or provide an explicit migration map and human-approved rename plan.
7. **No manual norm transcription without verification.** OCR is only an extraction aid. Every final norm row requires visual source confirmation and independent second-person verification.
8. **No force-click by default.** Playwright `force` is prohibited unless the agent documents why normal actionability checks fail and receives approval.
9. **No brittle selectors.** Prefer accessible role, label, text, and test-id locators. Avoid dynamic REDCap CSS classes and absolute XPath.
10. **Fail closed.** If project state, permissions, source table, formula length, field mapping, or post-import verification is uncertain, stop and report the exact blocker.

---

## 5. Human Approval Gates

The agent must pause and ask for approval at each gate.

| Gate | Required evidence | Action after approval |
|---|---|---|
| A — Project identity | Project title/PID, development/production status, URL, current instruments, timestamped screenshot | Continue discovery |
| B — Backup complete | Export filenames, hashes, download timestamps | Create implementation files |
| C — Norm verification | Norm-table validation report with zero unresolved cells; verifier name/date | Generate scoring logic |
| D — Dictionary diff | Row-level before/after diff, fields added/changed/deleted, formula validation | Upload to development |
| E — Development import | Import preview with zero unintended deletions/type changes | Commit import |
| F — Regression pass | All unit, integration, browser, and hand-scored fixtures pass | Prepare release |
| G — Production release | Change-control approval and rollback package | Apply approved production change |

A generic “continue” is insufficient for a production mutation. The approval must name the project and intended action.

---

## 6. Agent Operating Loop

For every browser step, use this loop:

1. **Observe** — inspect URL, title, visible headings, alerts, project banner, accessible tree/ARIA snapshot, and a screenshot.
2. **Orient** — identify current REDCap page, project PID, environment, instrument, modal/dialog, unsaved state, and available actions.
3. **Plan** — choose the smallest reversible action and state the expected UI/database result in the run log.
4. **Act** — use one semantic Playwright action.
5. **Verify** — assert URL or heading, success/error message, changed field count/value, and absence of unexpected warnings.
6. **Record** — save a timestamped screenshot, structured log entry, and trace reference.
7. **Decide** — continue, retry with a new observation, roll back, or stop for human input.

Never chain multiple destructive actions without an intermediate assertion.

---

## 7. Dynamic Playwright Protocol

### 7.1 Browser attachment

- Use the existing authenticated browser context when available.
- If authentication is required, navigate to the login page and pause for the human.
- Start tracing before project inspection with screenshots, DOM snapshots, and network metadata.
- Mask participant IDs, names, DOBs, emails, medical record numbers, and free-text notes in screenshots.
- Store traces under `artifacts/playwright/YYYYMMDD-HHMMSS/` and never commit traces containing PHI.

### 7.2 Locator priority

Use locators in this order:

1. `getByRole(role, { name })`
2. `getByLabel(label)`
3. `getByText(text, { exact: true })`
4. `getByPlaceholder(placeholder)`
5. `getByTestId(testId)` if the instance provides stable IDs
6. A scoped CSS locator only after inspecting the current DOM

After navigation or modal changes, reacquire locators. Do not cache element handles across page transitions.

### 7.3 State detection

At minimum, detect and log:

- REDCap version if displayed.
- Project title and PID.
- Development, analysis/cleanup, or production status.
- Longitudinal/classic project type.
- Arms/events and designated record ID.
- Existing instruments and instrument-event mappings.
- Whether Data Dictionary upload/download is available.
- Whether Online Designer is available.
- Whether External Modules are enabled and whether the user has module-management permission.
- Whether API access is enabled; never expose an API token.
- Current records or real participant data visible on screen.
- Existing calculations, action tags, piping, branching logic, matrices, repeating instruments, and record-locking behavior relevant to Mullen.

### 7.4 Mutation behavior

- Take a full-page screenshot before and after each mutation batch.
- Download the current dictionary immediately before upload.
- On import-preview pages, parse all added, changed, deleted, renamed, and warning rows.
- If any existing variable would be deleted, renamed, or have its type changed unexpectedly, cancel the import.
- Do not click “Commit changes” until Gate E approval.
- After commit, redownload the dictionary and compare it with the intended generated file.

### 7.5 Unexpected UI handling

If the UI differs from expected:

1. Do not guess a selector.
2. Capture screenshot and ARIA snapshot.
3. Re-identify the page by headings and landmarks.
4. Search for the action by accessible role/name.
5. If still ambiguous, stop and show the human the screenshot and candidate actions.

---

## 8. Required Repository Structure

Create this structure without deleting original source files:

```text
Mullen/
├── MASTER_AGENTIC_VSCODE_REDCAP_IMPLEMENTATION.md
├── source/                         # hashes/manifests; source files may remain in original paths
├── norms/
│   ├── norm_source_manifest.csv
│   ├── norm_ocr_provisional.csv
│   ├── norm_verification_queue.csv
│   ├── mullen_scale_norms_verified.csv
│   ├── mullen_age_equivalents_verified.csv
│   ├── mullen_composite_norms_verified.csv
│   └── norms_validation_report.md
├── rules/
│   ├── scoring_rules.schema.json
│   ├── scoring_rules.verified.json
│   └── prematurity_policy.verified.json
├── redcap/
│   ├── baseline_data_dictionary.csv
│   ├── generated_data_dictionary.csv
│   ├── data_dictionary_diff.md
│   ├── field_mapping.csv
│   └── migration_plan.md
├── scripts/
│   ├── extract_norms.py
│   ├── validate_norms.py
│   ├── generate_redcap_dictionary.py
│   └── validate_redcap_dictionary.py
├── tests/
│   ├── fixtures/
│   ├── test_norm_lookup.py
│   ├── test_age_calculation.py
│   ├── test_raw_scoring.py
│   ├── test_dictionary.py
│   └── playwright/
├── artifacts/
│   ├── playwright/
│   ├── screenshots/
│   └── validation/
└── CHANGELOG.md
```

Norm files are controlled/licensed material. Keep them private, exclude them from public repositories, and follow institutional data governance.

---

## 9. Norm Extraction and Verification

`02 Scoring/Mullen Scoring.pdf` has no extractable text. Use a page-rendering plus OCR workflow, but treat every OCR value as unverified.

### 9.1 Source manifest

For every source document record:

- Filename and relative path
- SHA-256 hash
- Page count
- Document/version label shown on source
- Date obtained
- Owner/license status
- Extractor tool/version
- Reviewer and approval status

If the source version cannot be identified, scoring remains blocked.

### 9.2 Verified scale norm schema

Use a long-format table such as:

```text
norms_version
source_document_hash
source_table_id
source_page
source_row_label
source_column_label
scale_code
age_min_days
age_max_days
age_label
raw_score_min
raw_score_max
t_score
percentile_rank
age_equivalent_months
descriptive_category
ocr_value
verified_value
verification_status
verified_by
verified_at
second_verified_by
second_verified_at
notes
```

Do not combine distinct source tables unless the manual explicitly defines the relationship. If percentile or category is derived from another verified score table rather than printed in the raw-to-T table, record that dependency and source separately.

### 9.3 Composite schema

If the authorized manual uses a composite conversion table, create:

```text
norms_version
cognitive_t_sum_min
cognitive_t_sum_max
composite_standard_score
percentile_rank
descriptive_category
source_page
source_row_label
verified_by
verified_at
```

Do not use a linear composite equation unless the authorized manual explicitly defines that exact equation for operational scoring.

### 9.4 Verification workflow

1. Render every source page at high resolution.
2. OCR into provisional rows with page and cell coordinates.
3. Run structural checks for numeric domains and duplicated cells.
4. Present each provisional row beside the source crop.
5. First reviewer confirms or corrects every cell.
6. Second reviewer independently verifies every final cell.
7. Lock the verified CSV and record its SHA-256 hash.
8. Generate a validation report.
9. Do not build scoring formulas until unresolved-cell count equals zero.

### 9.5 Automated norm checks

The validator must fail on:

- Duplicate key ranges.
- Overlapping raw-score ranges within scale and age band.
- Gaps where the source table indicates continuous coverage.
- Reversed minimum/maximum values.
- Out-of-domain scale codes or scores.
- Missing source page/cell provenance.
- Missing verification identities/dates.
- Non-monotonic relationships where the source is expected to be monotonic; report exceptions for manual confirmation rather than auto-correcting.
- Age-band gaps or overlaps.
- Composite sums without exactly one matching verified row.

Never “repair” a questionable value algorithmically.

---

## 10. Scoring Rules Model

Create `rules/scoring_rules.verified.json` only from the authorized manual and qualified-assessor review.

For each scale include:

```json
{
  "scale_code": "GM",
  "items": [{"field":"gm_01","item_number":1,"min":0,"max":1}],
  "start_points": [],
  "basal_rule": null,
  "reversal_rule": null,
  "ceiling_rule": null,
  "discontinuation_rule": null,
  "unadministered_handling": null,
  "not_applicable_handling": null,
  "raw_score_min": 0,
  "raw_score_max": 36,
  "source_page": null,
  "verified_by": null,
  "verified_at": null
}
```

`null` means blocked, not “use a reasonable default.” Remove every fixed “credit first 10 items” behavior until the verified rule explicitly supports it.

### 10.1 Effective item status

Do not overload blank values. Distinguish:

- Administered and scored.
- Credited by verified basal rule.
- Not administered because ceiling/discontinuation was met.
- Missing unexpectedly.
- Unable to administer.
- Invalid/needs review.

Store sufficient fields to reconstruct how the raw score was produced. If REDCap field count makes item-level derived fields impractical, retain scale-level provenance fields containing basal item, ceiling item, count of observed items, credited points, missing-item count, and scoring status.

### 10.2 Raw score

The final raw score is not simply a sum until the verified rules prove that approach. Compute:

```text
raw_score = observed_points
          + verified_basal_credit
          + other explicitly authorized credit
```

Block final raw scoring when:

- Required administered items are blank.
- Basal is not established under the verified rule.
- Ceiling/discontinuation status is unresolved.
- An item contains a value outside its permitted choices.
- A manual override lacks reason, user, and timestamp.

---

## 11. Age and Prematurity Logic

Implement one shared, tested definition in the generator and mirror it in REDCap.

### 11.1 Chronological age

- Require valid DOB and evaluation date.
- Reject evaluation date earlier than DOB.
- Calculate complete calendar years, months, and residual days for display.
- Store a single approved norm-age key matching the manual’s age-band convention.
- Do not use decimal months unless the manual explicitly specifies that convention.
- Do not default missing age to 12 months or any other value.

### 11.2 Gestational age

- Collect gestational weeks and days with validated ranges.
- Derive preterm status; do not ask the user to assert a status that conflicts with gestational age.
- Calculate days early from both weeks and days.
- Calculate expected DOB with a verified formula.
- Confirm how term/post-term values are handled.

### 11.3 Adjustment policy

The prematurity-adjustment cutoff and norm-age convention must come from an approved project policy and authorized source. Store:

- `prematurity_policy_version`
- `adjustment_eligible`
- `adjustment_applied`
- `adjustment_reason`
- `chronological_norm_age_key`
- `adjusted_norm_age_key`
- `selected_norm_age_key`

If eligibility is unknown, show chronological outputs only if otherwise valid and mark adjusted scoring `SCORING_BLOCKED_POLICY_MISSING`.

---

## 12. Exact Norm Lookup

For every scale, lookup uses:

```text
verified norms version
+ scale code
+ selected age band/key
+ final raw score
= exactly one verified output row
```

Return:

- T score
- Percentile rank if authorized/available
- Descriptive category based on verified source mapping
- Age equivalent from the verified age-equivalent table
- Source table/page reference
- Norm row identifier/hash

Zero matches or multiple matches must block scoring and produce an actionable error. Never interpolate between rows, extrapolate beyond table limits, or substitute the nearest age band unless the manual explicitly directs it.

For the approved Early Learning Composite, use only the authorized component scales and exact composite conversion table. Do not include Gross Motor unless the source explicitly requires it.

---

## 13. REDCap Implementation Strategy

### 13.1 Choose the engine after discovery

Use this decision order:

1. **Native generated REDCap logic** when verified tables can be represented safely within calculation/action-tag limits and remain maintainable.
2. **Approved External Module or server-side hook** when native formulas are too long, cannot perform reliable lookup, or cannot preserve provenance. This requires administrator and governance approval.
3. **Approved external scoring service via API** only if institutional security and IRB/data-governance requirements permit it. Never place API tokens in browser code or the repository.
4. **Manual verified scoring fallback** when no approved automated mechanism can represent the rules. Do not deploy approximate automation merely to avoid manual work.

### 13.2 Native REDCap pattern

- Numeric outputs use calculated fields.
- Text outputs use text fields with `@CALCTEXT` if supported by the installed REDCap version.
- Date outputs use validated date fields with `@CALCDATE` if supported.
- Generated outputs are read-only using instance-supported action tags.
- Use explicit score-status fields so outputs remain blank when prerequisites fail.
- Use generated nested lookup expressions only from verified CSVs.
- Split large lookups into intermediate age-band and raw-score mapping fields when needed.
- Keep formulas deterministic and versioned.
- Add `norms_version`, `scoring_engine_version`, and `norm_row_id` fields.
- Test actual instance behavior for blanks; never assume `+`, `sum()`, `datedif()`, or action-tag behavior without a development record test.

REDCap calculations commonly execute when the relevant form is opened or saved. Include a post-deployment recalculation plan, such as the instance’s Data Quality recalculation rule, for pre-existing records.

### 13.3 Dictionary generation

Generate `redcap/generated_data_dictionary.csv`; do not maintain 237+ fields manually.

The generator must:

- Start from the exported live baseline dictionary.
- Preserve existing variable names unless an approved migration requires change.
- Preserve forms/events needed by existing data.
- Add or modify only fields listed in `field_mapping.csv`.
- Generate raw formulas from verified scoring rules.
- Generate norm formulas from verified norm tables.
- Add explicit prerequisites and scoring-status logic.
- Add branching logic for adjusted outputs only when policy permits.
- Escape CSV, quotes, line breaks, and action-tag expressions correctly.
- Enforce REDCap variable naming and length limits.
- Produce a row-level diff and formula-length report.

### 13.4 Data preservation

Before import, classify each current field:

- Preserve unchanged.
- Preserve and relabel/reorder.
- Convert from manual to calculated without losing historic values.
- Deprecate but retain for audit.
- Migrate to a new field with a documented transformation.

Never change a populated field from manual to calculated without first exporting values and defining how historical/manual scores will be retained. Prefer new `_auto` fields during validation, compare them with legacy fields, then retire legacy entry only after approval.

---

## 14. Field Mapping Requirements

Maintain `redcap/field_mapping.csv` with:

```text
existing_variable
target_variable
form
section
paper_label
field_type
choices_or_calculation
source_file
source_page
scoring_role
migration_action
compatibility_status
notes
```

At minimum, cover:

- Participant and evaluation metadata.
- All 159 item fields.
- Five final raw scores.
- Five chronological T scores, percentiles, categories, and age equivalents.
- Five adjusted outputs if policy permits.
- Composite sum and verified composite outputs if approved.
- Age and prematurity fields.
- Norms and engine version fields.
- Scoring status/error fields.
- Basal/ceiling/provenance fields.
- Verification, override, lock, and audit fields.

---

## 15. Validation Suite

### 15.1 Static dictionary tests

Fail on:

- Duplicate or invalid variable names.
- Unknown field types.
- Invalid choice syntax.
- References to nonexistent variables.
- Circular calculations.
- Invalid branching logic.
- Formula length beyond the instance limit.
- Missing validation on dates and numeric fields.
- Calculated outputs that remain user-editable.
- Any norm output lacking version/provenance linkage.

### 15.2 Unit tests

Test:

- Calendar age around month ends, leap years, and birthdays.
- Evaluation date before DOB.
- Gestational weeks/days boundaries.
- Adjustment-policy cutoff boundaries.
- Every item’s minimum, maximum, and invalid values.
- Basal established/not established/reversal scenarios.
- Ceiling/discontinuation boundaries.
- Missing and unable-to-administer states.
- Minimum and maximum raw score for each scale.
- Every norm age-band boundary.
- Every raw-score range boundary.
- Zero-match and duplicate-match norm failures.
- Composite minimum/maximum and category boundaries.

### 15.3 Golden fixtures

A qualified assessor must provide de-identified hand-scored fixtures. Each fixture includes:

- DOB, evaluation date, gestational age.
- Item-level responses and administration status.
- Basal/ceiling decisions.
- Expected raw scores.
- Chronological and adjusted norm ages.
- Expected T scores, percentiles, categories, age equivalents, and composite.
- Source pages used.
- Assessor/verifier signatures and dates.

No production release without 100% agreement on all approved fixtures.

### 15.4 Playwright tests

Automate in the development project:

1. Open a synthetic record.
2. Enter metadata.
3. Verify age and prematurity outputs.
4. Enter item scores with semantic locators.
5. Verify raw scores update only when prerequisites are met.
6. Verify exact standard outputs against fixture values.
7. Reload the form and verify persistence.
8. Test blanking a prerequisite and confirm outputs clear/block.
9. Test invalid and boundary values.
10. Verify print layout against the paper structure.
11. Verify exports contain the intended variables and values.
12. Capture traces and masked screenshots.

Use visual snapshots only for layout. Numeric scoring assertions must compare exact values, not screenshots.

---

## 16. Browser Execution Plan

### Phase 0 — Read-only discovery

- Inspect local files and compute hashes.
- Attach to browser and identify project/environment.
- Export baseline dictionary and available metadata.
- Inventory Mullen instruments, fields, events, records, and dependencies.
- Capture screenshots and trace.
- Produce `discovery_report.md`.
- Stop at Gate A/B.

### Phase 1 — Source normalization

- Render and OCR scoring pages.
- Build provisional norm tables with provenance.
- Verify every value twice.
- Encode official scoring and prematurity rules.
- Run validators.
- Stop at Gate C.

### Phase 2 — Generate implementation

- Build field mapping.
- Generate corrected dictionary and optional approved module code.
- Generate tests and golden-fixture templates.
- Produce before/after diff and migration plan.
- Stop at Gate D.

### Phase 3 — Development import

- Confirm development project.
- Upload generated dictionary.
- Parse import preview.
- Cancel on unexpected deletions/type changes.
- Stop at Gate E before commit.
- Commit only after approval.
- Redownload and compare.

### Phase 4 — Development validation

- Execute fixture tests.
- Execute Playwright end-to-end tests.
- Run REDCap data-quality/recalculation checks as appropriate.
- Compare auto fields with legacy/manual fields.
- Resolve every discrepancy; never waive unexplained scoring differences.
- Stop at Gate F.

### Phase 5 — Release

- Freeze verified files and hashes.
- Build rollback package.
- Obtain governance/change-control approval.
- Apply only the approved change set.
- Re-run smoke and fixture tests.
- Monitor logs and discrepancies.
- Stop/rollback on any unexplained divergence.

---

## 17. Run Log Format

Append every operation to `artifacts/agent-run-log.jsonl`:

```json
{
  "timestamp":"ISO-8601",
  "phase":"discovery|norms|generation|import|validation|release",
  "project_pid":"masked-or-approved",
  "environment":"development|production|unknown",
  "page_url":"redacted",
  "observation":"what the agent saw",
  "planned_action":"single intended action",
  "approval_gate":"A-G or none",
  "action":"what was executed",
  "expected_result":"expected state",
  "actual_result":"observed state",
  "verification":"assertions performed",
  "artifact":"screenshot/trace/diff path",
  "status":"pass|blocked|rolled_back|failed",
  "notes":"no credentials or PHI"
}
```

---

## 18. Rollback Plan

Before release, prepare:

- Baseline dictionary and hash.
- Pre-change project XML when permitted.
- Data export for affected fields/records.
- Instrument-event mapping snapshot.
- List of added fields and formulas.
- Reverse migration steps.
- Person authorized to approve rollback.

Rollback triggers include:

- Import produced unintended deletion/type changes.
- Any golden fixture differs.
- Norm lookup returns zero/multiple rows.
- Calculated fields fail to refresh as expected.
- Real records show unexpected blanks or changed scores.
- Browser/UI behavior diverges from validated development behavior.

---

## 19. Definition of Done

The implementation is complete only when:

- The paper layout is reproduced and print-validated.
- All item fields and permitted choices match controlled sources.
- Official scoring rules are verified and versioned.
- Every norm and reference-score cell has source provenance and two-person verification.
- Approximate HTML scoring functions and fixed basal shortcuts are removed or disabled.
- Missing prerequisites produce a blocked status, not a score.
- Chronological and adjusted ages match approved hand calculations.
- Raw and standard scores match every golden fixture exactly.
- Composite results use the authorized conversion method.
- The REDCap dictionary diff contains no unintended destructive change.
- Development browser tests pass with traces and masked screenshots.
- Recalculation/backfill behavior is validated.
- Legacy/manual values are preserved or migrated under an approved plan.
- Norms, scoring engine, and policy versions are stored with every scored record.
- Rollback package and release approval exist.
- No credential, PHI, or licensed norm table has been exposed in public artifacts.

---

## 20. Agent Start Command

When this file is given to the VS Code browser agent, begin with:

> Read this runbook and all listed workspace sources. Perform **Phase 0 read-only discovery only**. Attach to the currently open authenticated REDCap browser with Playwright, inspect the live page dynamically using semantic locators and screenshots, identify the project/environment, export the baseline dictionary if the UI permits a non-mutating download, and create the discovery report and backups manifest. Do not upload, edit, save, import, recalculate, or modify records. Stop at Approval Gates A and B with exact evidence and blockers.

After Gates A and B are approved, continue one phase at a time. Never skip Gate C norm verification, and never deploy the current approximate HTML formulas.

---

## 21. Implementation References

- Playwright locators: `https://playwright.dev/docs/locators`
- Playwright screenshots: `https://playwright.dev/docs/screenshots`
- Playwright tracing: `https://playwright.dev/docs/api/class-tracing`
- REDCap calculation overview: `https://research.bidmc.org/redcap-core/documentation/calculations`
- REDCap calculated fields versus `@CALCTEXT` and `@CALCDATE`: `https://research.bidmc.org/redcap-core/faq/calculated-fields-calctext-calcdate`

Use the installed REDCap instance’s own help/documentation as the final authority for supported functions and action tags because available behavior can vary by version and institutional configuration.

# NANO/NICO recruitment ground-truth contract

This document records only what is present in the REDCap projects, the
user-supplied reference table, and this repository as of 2026-07-24. It does
not infer a protocol rule from similarly named fields.

## Verified read access

Both supplied API tokens resolved to the expected projects and successfully
returned every endpoint used by the recruitment pipeline:

| Project | REDCap project | Project structure observed | Required reads that succeeded |
|---|---|---|---|
| NANO | PID 4218, `NANO Study Surveys` | Longitudinal; 12 events; 58 instruments | project info, metadata, instruments, events, instrument-event mappings, and minimal recruitment records |
| NICO | PID 3836, `NICO Study` | Longitudinal; 8 events; 48 instruments; repeating definitions present | project info, metadata, instruments, events, instrument-event mappings, repeating definitions, and minimal recruitment records |

The record exports returned the configured record identifier, race, ethnicity,
status/review, dual-enrollment (NICO), and diagnostic date fields. Metadata and
instrument access also exposed the family-information and demographic
instruments needed to evaluate field meaning and coverage.

REDCap project-info and record-export responses do not identify the username
that owns a token. Effective rights are therefore established by successful
read calls and returned fields, not by a username or role label. NANO data
access group assignment was not returned by the optional user/DAG endpoint;
that endpoint is not used by the counting pipeline.

## Source field mapping

| Role | NANO | NICO |
|---|---|---|
| Participant ID | `demo_id` — `demographics_complete_this_first` | `id` — `demographics_complete_this_first` |
| Child race | `fif_childrace` checkbox — `family_information_form` | `race` checkbox — `infant_demographics` |
| Primary child ethnicity | `fif_childethnicity` radio — `family_information_form` | `fif_childethnicity` radio — `family_information_and_demographics` |
| Secondary ethnicity | None | `ethnicity` radio — `prapare` |
| Ineligible flag | `demo_ineligible` | `demo_ineligible` |
| Unenrolled flag | `demo_unenrolled` | `demo_unenrolled` |
| Review flag | `demo_exclude` (`Consider excluding participant?`) | `demo_exclude` (`Consider excluding participant?`) |
| Cross-project marker | None | `dual_enrolled` (`0` NICO only; `1` also NANO) |
| Confirmed enrollment status | Not present in repository evidence | Not present in repository evidence |
| Confirmed enrollment/consent date | Not present in repository evidence | Not present in repository evidence |

The following accessible dates are diagnostic only and are never substituted
for enrollment:

- NANO: `visit_date`, `bsrc_doe` (data-sharing consent),
  `papf_parent_date` (optional media/advertising form), and `fif_doe`
  (family-information evaluation).
- NICO: `visit_date` and `dob`.

## Coded demographic rules

NANO race codes:

- Racial-minority-specific codes: `1` American Indian/Alaska Native, `2`
  Asian, `3` Native Hawaiian/Other Pacific Islander, `4` Black/African
  American.
- `5` White.
- `6` Unknown/Other.

NICO race codes:

- Racial-minority-specific codes: `1` African American/Black, `2` American
  Indian/Alaska Native, `3` Asian, `5` Native Hawaiian/Other Pacific Islander.
- `6` White.
- `7` Other.
- `4` Hispanic/Latino is a Hispanic source in the existing project mapping but
  is not counted as a racial-minority-specific code.

For either study, any selected specific non-White race produces
`racial_minority_flag = True`, including a participant who also selects White.
Multiple selections still count once and are listed in `Data_Quality_Issues`.
Unknown/Other alone is `False`; no race response is missing, not `False`.

Ethnicity rules:

- `fif_childethnicity`: `1` Hispanic/Latino, `2` Not Hispanic/Latino, `3`
  Unknown/Other.
- NICO `ethnicity`: `0` Yes, `1` No, `2` choose not to answer.
- A positive value from any configured source produces
  `hispanic_ethnicity_flag = True`. A negative value with no positive source
  produces `False`. Missing, unknown, or declined-only responses remain
  missing. Positive and negative sources on the same participant are retained
  as a conflict issue.

## Inclusion, exclusion, and milestone logic

Observed `demo_ineligible = 1`, `demo_unenrolled = 1`, or a configured hard
test/duplicate flag produces `included_in_recruitment_count = False`. No hard
test/duplicate field is currently configured because no such field is present
in the reviewed mapping. `demo_exclude = 1` is retained as a review issue
because the source label says “Consider excluding participant?” rather than
recording a final exclusion.

For every other participant,
`included_in_recruitment_count` remains missing because neither project has a
protocol-confirmed affirmative enrollment-status rule in the repository
evidence. The pipeline does not treat record existence as confirmed enrollment.

`milestone_period` remains missing without a confirmed enrollment date. When a
confirmed mapping is configured in the future, the implementation assigns the
first milestone on or after the enrollment date and counts cumulatively through
each milestone. An enrollment occurring exactly on a milestone is included in
that milestone.

NANO milestone targets and published cumulative actuals are transcribed
unchanged from the user-supplied reference table and existing repository
configuration. They are not overwritten with the current record inventory.
NICO milestone targets and historical actuals are not present, so those cells
remain `N/A`. A combined value is not inferred when a constituent project value
is unavailable. NICO `dual_enrolled` is reported but does not silently
deduplicate the separate project tables.

## Restricted output contract

A complete two-token run writes exactly two restricted Excel workbooks:

- `nano_recruitment_ground_truth_<date>.xlsx`
- `nico_recruitment_ground_truth_<date>.xlsx`

The first sheet in each workbook is the image-matched milestone table. The
workbooks collectively contain the required logical sheets:

- `NANO_Participant_Audit`
- `NICO_Participant_Audit`
- `NANO_Milestone_Summary`
- `NICO_Milestone_Summary`
- `Combined_Milestone_Summary`
- `Data_Quality_Issues`

The same six logical outputs are written as formula-safe CSV files under
`recruitment_audit_secure/csv_package_<date>/`.

Participant audit sheets contain raw coded sources alongside:

- `included_in_recruitment_count`
- `racial_minority_flag`
- `hispanic_ethnicity_flag`
- `exclusion_reason`
- `milestone_period`

`Data_Quality_Issues` contains participant-specific missing dates, status
conflicts, missing/unknown demographics, multiple race selections, exclusions,
and unassignable milestone periods. Logs contain aggregate row and issue counts
only.

## Secure run

Tokens are read only from process environment variables:

```bash
export NANO_API_TOKEN="..."
export NICO_API_TOKEN="..."
python recruitment_reports.py \
  --output-dir recruitment_outputs \
  --secure-output-dir recruitment_audit_secure
```

`recruitment_audit_secure/` and `outputs/` are ignored by Git. The scheduled
GitHub workflow passes `--no-secure-audit`, publishes only aggregate outputs,
runs the test suite, and rejects token-shaped literals before the API refresh.

Unresolved protocol/PI confirmations are limited to:

1. The affirmative enrollment-status rule for NANO.
2. The affirmative enrollment-status rule for NICO.
3. The primary enrollment/consent date for NANO.
4. The primary enrollment/consent date for NICO.
5. External verification of the NANO target plan and any NICO targets/history.


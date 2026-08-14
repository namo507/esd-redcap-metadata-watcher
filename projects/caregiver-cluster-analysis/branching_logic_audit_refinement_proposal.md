# Branching-Logic Audit Refinement Proposal

## Scope and evidence

This proposal is a research deliverable for the caregiver cluster analysis project. It does not implement code. It is grounded in the current repository state as verified on 2026-08-14 from these sources:

- `caregiver_analysis_pipeline.py`
- `config.yaml`
- `README.md`
- `build_notebook.py`
- `Caregiver Outputs/table_14_fraud_rule_definitions.csv`
- `Caregiver Outputs/table_15b_rule_counts_by_project.csv`
- `Caregiver Outputs/record_flags.parquet`
- `data_cache/4581_metadata_2026-07-30.parquet`
- `data_cache/4581_record_2026-07-30.parquet`
- `InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv`
- `caregiver_cluster_analysis_improvement_prompt.md`

Two constraints changed the analysis plan and should stay explicit:

- The audit PDF titled `Potential Bot Responses` is not present in the workspace, so this proposal cannot quote or reconcile PDF-only wording beyond what the prompt already enumerates.
- The named CSV `InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv` is not the 1,779-row dirty 4581 export described in the prompt. In the current repo it has 177 rows, matching the clean 4797 fallback behavior in `config.yaml`. Of the prompt's cited audit IDs, only `31` and `118` appear in that CSV. The full 4581 data needed for validation is present in `data_cache/4581_record_2026-07-30.parquet` and should be treated as the operative verification source for this task.

## 1. Verified branching-logic gaps

The cached 4581 metadata confirms that REDCap exposes machine-readable branching logic for the relevant parent fields. The record export materializes checkbox parents as `field___n` child columns, so any generalized audit must bridge metadata parent names to exported child columns.

### 1.1 Prenatal question block

| Family | Verified REDCap logic from metadata | Violation pattern seen in export | Verified 4581 record IDs |
| --- | --- | --- | --- |
| Mutually exclusive prenatal follow-up radios both populated | `fif_prenatal_no_preg` is shown when `([fif_pregnant] <> "" and [fif_pregnant]=0)` and `fif_prenatal_preg` is shown when `([fif_pregnant] <> "" and [fif_pregnant]=1)` | Both conditional sibling fields contain saved values at the same time | `31`, `118`, `273`, `571`, `768`, `1076`, `1554`, `1699` |
| Tested reason answered when no prenatal-test yes gate is active | `fif_tested_reason` is shown when `([fif_prenatal_no_preg] <> "" and [fif_prenatal_no_preg]=1) or ([fif_prenatal_preg] <> "" and [fif_prenatal_preg]=1)` | One or more `fif_tested_reason___*` columns are checked while neither prenatal field equals `1` | `966`, `967`, `968`, `969`, `970`, `971`, `972`, `973`, `974`, `975`, `976`, `977`, `978`, `979`, `981`, `1591` |
| Not-tested reason answered when no prenatal-test no gate is active | `fif_not_tested_reason` is shown when `([fif_prenatal_no_preg] <> "" and [fif_prenatal_no_preg]=0) or ([fif_prenatal_preg] <> "" and [fif_prenatal_preg]=0)` | One or more `fif_not_tested_reason___*` columns are checked while neither prenatal field equals `0` | `364`, `416`, `987`, `1026`, `1707`, `1741` |

Verified prenatal subpatterns from the cached 4581 export:

- `31`, `118`, `273`, `571`, and `1076` save `fif_prenatal_preg=1` and `fif_prenatal_no_preg=1`.
- `1554` and `1699` save `fif_prenatal_preg=0` and `fif_prenatal_no_preg=0`.
- `768` saves `fif_prenatal_preg=1` and `fif_prenatal_no_preg=0`, which is still impossible because both mutually exclusive conditional siblings are populated.
- The prompt's cited tested-reason family is fully reproducible from the cached 4581 export, but the export contains one additional matching record, `978`, that was not listed in the prompt.

### 1.2 Earlier-diagnosis question block

| Family | Verified REDCap logic from metadata | Violation pattern seen in export | Verified 4581 record IDs |
| --- | --- | --- | --- |
| Earlier-diagnosis yes reasons answered under a non-yes gate | `fif_diag_early_yes` is shown when `([fif_diag_earlier] <> "" and [fif_diag_earlier]=1)` | One or more `fif_diag_early_yes___*` columns are checked while `fif_diag_earlier` is not `1` | `205`, `1025` |
| Earlier-diagnosis no reasons answered under a non-no gate | `fif_diag_early_no` is shown when `([fif_diag_earlier] <> "" and [fif_diag_earlier]=0)` | One or more `fif_diag_early_no___*` columns are checked while `fif_diag_earlier` is not `0` | `1137`, `1642`, `1678` |

### 1.3 Metadata details that matter to the implementation

- `fif_pregnant` is a radio field with choices `1, Yes | 0, No | 2, Other`.
- `fif_tested_reason` and `fif_not_tested_reason` are metadata parent field names, but the record export stores their answers across checkbox children such as `fif_tested_reason___1`.
- The metadata shows `fif_not_tested_reason` has 10 checkbox choices, not 9.
- The metadata shows `fif_diag_early_yes` has 4 checkbox choices, not 3.
- The metadata shows `fif_diag_early_no` has 4 checkbox choices. The prompt's shortened suffix examples are therefore incomplete as an implementation guide.

## 2. Current pipeline coverage and exact gap

`engineer_behavioral_features_and_rules` currently concatenates clean and dirty metadata into `combined_meta` and then builds `rule_R8` by scanning metadata rows whose `branching_logic` string contains the literal substring `[fif_num_autistic]`. In practice, current `R8` does only two things:

- it flags follow-up fields tied to `fif_num_autistic` when those follow-up fields are non-empty for rows with `fif_num_autistic=0`
- it flags impossible counts where selected child-age checkbox bands exceed `fif_num_children`

That logic does not generalize to the prenatal block or the earlier-diagnosis follow-ups. No other rule covers those chains either:

- `R1` to `R7` are timing, answer-pattern, submission-burst, or open-text similarity rules
- `R9` is restricted to caregiver age, ZIP format, and age-at-first-birth plausibility
- `R10` is restricted to the 4797 knee check, age mismatch, and optional email heuristics

The current outputs confirm the gap:

- `Caregiver Outputs/table_15b_rule_counts_by_project.csv` reports `R8=9` for dirty 4581 and `R8=0` for clean 4797.
- The current dirty-4581 `R8` records are `52`, `120`, `167`, `182`, `220`, `676`, `769`, `1069`, and `1774`.
- None of the prompt-cited branching-audit IDs currently trigger `R8` in `record_flags.parquet`.

This is therefore a real detection hole, not a reporting-only issue.

## 3. Recommended rule design

### 3.1 Prefer a generalized `R8`, not a new `R11`

The minimal, lowest-churn design is to keep the existing rule number and broaden its implementation. Reasons:

- `R8` is already defined in outputs and docs as a logical family-information inconsistency rule.
- Tier 1 is already documented as `R1/R2/R5/R8/R9`, so preserving `R8` avoids needless downstream rule renumbering and keeps the tier formula stable.
- These new findings are hard logical impossibilities or forbidden follow-up states, not soft suspicion signals, so they fit the existing Tier 1 role better than a new non-tier-defining appendix rule.

### 3.2 Conceptual design

Generalize `R8` into a metadata-driven branching audit with two subtypes.

Subtype A: mutually exclusive conditional sibling fields saved simultaneously.

- Use explicit config mappings for sibling pairs that are logically exclusive but represented as separate REDCap fields.
- For this task the required pair is `fif_prenatal_preg` versus `fif_prenatal_no_preg`.
- A row should fail this subtype whenever both sibling fields are populated, regardless of whether the saved values are `1/1`, `0/0`, or mixed values such as `1/0`.

Subtype B: orphaned follow-up answers.

- Use metadata branching logic plus explicit config mappings from gating fields to dependent parent fields.
- Expand checkbox parent names such as `fif_tested_reason` to all matching exported child columns such as `fif_tested_reason___*` when checking whether a follow-up was answered.
- Treat any checked child option as a populated follow-up.
- Evaluate the gate against the exported gating fields and flag the row whenever follow-up data exists while the gate is false.

### 3.3 Required config shape

`config.yaml` already has a natural home under `fraud_rules`, but it currently contains only scalar thresholds and disposable-email domains. Add a nested, auditable section for branching audits rather than hardcoding field names in Python. At minimum it needs:

- an explicit list of mutually exclusive sibling pairs
- an explicit list of dependent follow-up parents and the gating fields they rely on
- room for future exceptions or per-family labels so the output table can say which family fired

The config should stay declarative. The Python should interpret the config and the REDCap metadata; it should not embed these specific field names as new literals.

### 3.4 Tiering and invariants

Keep the new generalized branching findings inside Tier 1 through `R8`.

- The violations are deterministic logical contradictions.
- That is closer to the existing confirmed-invalid philosophy than to the soft suspicion count used for Tiers 2 and 3.
- Preserving `R8` as Tier 1 keeps the documented tier formula intact.

Do not relax any current hard invariants:

- `clean_4797` expected records must remain `177`.
- `dirty_4581` expected records must remain `1779`.
- the completed-human timing reference must remain `131`.
- the documentation must continue to avoid claiming an identifiable bot prevalence.

One additional release gate should be added for the implementation change: inspect the false-positive output in clean 4797 before promoting the generalized `R8`. A nonzero clean hit rate is not automatically disqualifying, but it must be reviewed explicitly because these are intended as impossibility checks.

## 4. Files to change

### Source files that should change

1. `caregiver_analysis_pipeline.py`

This is the owning implementation surface. `R8` is hardcoded here to `[fif_num_autistic]` plus the age-band count check, so the branching-audit logic gap can only be fixed here. The conceptual change is to generalize `R8`, expand checkbox parents to exported child columns, attach per-row branching-audit detail for downstream reporting, and keep the existing 131-reference guard intact.

2. `config.yaml`

This file needs a new nested branching-audit definition under `fraud_rules`. The pipeline already reads rule settings from config, so this is the correct place to hold the sibling-pair and follow-up mapping definitions. That keeps the rule auditable and resilient to future REDCap instrument edits.

3. `README.md`

The docs currently describe outputs through `R1` to `R10` and state the tier philosophy and hard record-count invariants. They need to document that `R8` has been generalized to cover prenatal and earlier-diagnosis branching contradictions, note that the 4581 validation source is the cached export rather than the named fallback CSV, and record the audit-ID regression fixture list, including the extra cached-data match `978`.

4. `projects/caregiver-cluster-analysis/tests/` as a new test surface

There is currently no test directory or project-local automated coverage for this pipeline. A new test file should be added to lock the branching-audit behavior. The tests should cover sibling-pair collisions, orphaned follow-up detection, checkbox-parent expansion, and the clean-4797 no-regression expectation. This is the smallest reliable way to keep the rule from regressing.

### Downstream generated artifacts expected to change after rerun

- `Caregiver Outputs/table_14_fraud_rule_definitions.csv`
- `Caregiver Outputs/table_15_rule_false_positive_rates.csv`
- `Caregiver Outputs/table_15b_rule_counts_by_project.csv`
- `Caregiver Outputs/table_16_trust_tier_counts.csv`
- `Caregiver Outputs/figure_9_rule_cooccurrence.*`
- `Caregiver Outputs/record_flags.parquet`
- `Caregiver Outputs/output_manifest.csv`
- a new aggregate audit table, recommended as `Caregiver Outputs/table_34_branching_logic_audit.csv`

The new table should summarize record ID, source project, violation family, gating fields, dependent field family, and observed conflicting values. A new figure is optional. A table is sufficient for the first implementation because `figure_9` already covers rule-level co-occurrence.

### Files that should not change for this task

- `cleaned_autism_study_data.csv`
- `InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv`
- `data_cache/*`
- `Caregiver Outputs/record_flags.parquet` by manual editing
- `Analysis questions - Sheet3.csv`
- `build_notebook.py` in the minimal implementation path

`build_notebook.py` should stay out of scope here because it targets a missing `caregiver_cluster_analysis.ipynb` file, not the active `caregiver_cluster_simple_metrics.ipynb` notebook. If a notebook summary of the new audit is wanted later, update the active notebook directly in a follow-up change.

## 5. Upstream REDCap items to raise with the instrument owner

These violations indicate an upstream survey/data-dictionary problem, not just an analysis-layer issue.

- The prenatal sibling fields appear to retain values simultaneously even though their visibility gates are mutually exclusive.
- The tested-reason and not-tested-reason checkbox groups can persist answers after a respondent changes an earlier gate, or the instrument is otherwise allowing hidden-field residue to remain saved.
- The earlier-diagnosis reason checkboxes show the same hidden-answer persistence pattern.
- The live REDCap instrument should be reviewed for whether hidden fields are cleared when branching changes, whether these blocks should be collapsed into a single parent question, and whether a REDCap-side data quality rule should surface impossible sibling or orphaned follow-up states before export.
- The audit spec should be reconciled against the live data dictionary because the prompt understates the exported checkbox ranges for `fif_not_tested_reason` and `fif_diag_early_yes`.

## 6. Regression checklist for the implementation

Use this checklist when the code change is eventually implemented.

1. Confirm the repository still halts on `177` clean 4797 records, `1779` dirty 4581 records, and `131` completed clean reference records.
2. Confirm that the generalized `R8` newly flags the sibling-pair collision set exactly: `31`, `118`, `273`, `571`, `768`, `1076`, `1554`, `1699`.
3. Confirm that the generalized `R8` newly flags the tested-reason orphan set exactly: `966`, `967`, `968`, `969`, `970`, `971`, `972`, `973`, `974`, `975`, `976`, `977`, `978`, `979`, `981`, `1591`.
4. Confirm that the generalized `R8` newly flags the not-tested-reason orphan set exactly: `364`, `416`, `987`, `1026`, `1707`, `1741`.
5. Confirm that the generalized `R8` newly flags the earlier-diagnosis yes-orphan set exactly: `205`, `1025`.
6. Confirm that the generalized `R8` newly flags the earlier-diagnosis no-orphan set exactly: `1137`, `1642`, `1678`.
7. Confirm that none of those cited records currently overlap with the pre-change dirty-4581 `R8` set `52`, `120`, `167`, `182`, `220`, `676`, `769`, `1069`, `1774`, so the implementation change is demonstrably adding new coverage rather than merely re-labeling existing hits.
8. Confirm that the clean-4797 false-positive output remains acceptable and is explicitly reviewed before the rule is promoted as a release-ready Tier 1 check.
9. Confirm that `table_14`, `table_15`, `table_15b`, `table_16`, `figure_9`, and the new `table_34` agree with the new `record_flags.parquet` content.
10. If the missing audit PDF becomes available later, reconcile its original record list against the cached-data finding that `978` matches the same tested-reason orphan pattern and should either be added to the fixture list or explicitly excluded with justification.

## Bottom line

The repository has a real branching-audit blind spot. The cached REDCap metadata already provides enough structure to fix it cleanly, and the cached 4581 export reproduces every prompt-listed violation family plus one additional matching record, `978`. The smallest complete implementation is to generalize `R8`, drive the new field relationships from `config.yaml`, add project-local tests, regenerate the affected fraud outputs, and leave the stale notebook builder and raw source files untouched.
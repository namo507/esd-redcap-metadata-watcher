# Bot Detection Refinement Addendum (2026-08-25)

This addendum is designed for direct insertion into ESD_Bot_Detection_Review.pptx and ESD_Bot_Detection_Review.pdf.

## What changed in analysis logic

A new reproducible audit was added to the pipeline:

- table_35_demographic_signal_summary.csv
- table_35b_demographic_signal_enrichment.csv

Purpose:

- Treat the demo_maternalrace___1 cluster as an anomaly-concentration signal.
- Do not treat the race field itself as a bot label.
- Quantify uncertainty and co-enrichment against existing trust rules.

## Key numeric findings to report

### A. Project-level prevalence (descriptive)

- dirty_4581: 46/1779 = 2.59% (Wilson 95% CI: 1.94% to 3.43%)
- clean_4797: 2/177 = 1.13% (Wilson 95% CI: 0.31% to 4.03%)

### B. Clean-vs-dirty differential (inferential)

- Two-sided Fisher exact p = 0.312
- Odds ratio = 2.32
- Risk ratio = 2.29
- Risk difference = +1.46 percentage points

Interpretation:

- Directionally higher in dirty_4581, but not statistically decisive by prevalence alone.

### C. Enrichment against trust-risk markers within dirty_4581

Among the 46 marked records (demo_maternalrace___1 = 1):

- Tier 1 (confirmed invalid): 27/46 = 58.7%
- Tier <= 2 (confirmed invalid or high suspicion): 39/46 = 84.8%
- Tier <= 3: 46/46 = 100%

Compared to other dirty_4581 records:

- Tier 1 enrichment: OR = 3.91, p = 7.4e-06
- Tier <= 2 enrichment: OR = 3.99, p = 2.0e-04

Rule-level enrichment (FDR-adjusted across R1-R10):

- R1: q = 0.0010
- R2: q = 0.0011
- R3: q = 0.0028
- R7: q = 0.0213
- R8: q = 0.0499
- R4: nominal p < 0.05, but q = 0.061 (not below 0.05 FDR)

Interpretation:

- The 46-record set is strongly concentrated in independently suspicious patterns.
- This supports "suspected batch contamination" framing, not demographic causation framing.

## Slides to add

## Slide X: Demographic Spike Is a Concentration Signal, Not a Label

Title:

- Demographic spike: anomaly concentration check

Subtitle:

- The prevalence contrast alone is not decisive; concentration with independent risk signals is.

Body bullets:

- demo_maternalrace___1 appears in 46 of 1779 records (2.59%) in dirty_4581 vs 2 of 177 (1.13%) in clean_4797.
- Prevalence difference is directional but not decisive (Fisher p = 0.312).
- Therefore we do not label records from this field alone.
- Instead, we test whether this subset concentrates known suspicious patterns.

Footer/source:

- table_35_demographic_signal_summary.csv

Speaker note:

- Emphasize fairness and measurement validity: demographic fields are never used as stand-alone bot evidence.

## Slide Y: Co-Enrichment Confirms Suspicion Concentration

Title:

- Co-enrichment with trust-risk rules in dirty_4581

Subtitle:

- The 46-record subset is disproportionately represented in hard and soft risk flags.

Body bullets:

- Tier 1 (confirmed invalid): 58.7% in subset vs 26.7% in other dirty_4581 records (OR 3.91, p 7.4e-06).
- Tier <= 2: 84.8% in subset vs 58.3% in others (OR 3.99, p 2.0e-04).
- Significant rule enrichments after FDR: R1, R2, R3, R7, R8.
- R6 is not enriched in this subset (high overall base rate makes it less discriminating here).

Decision sentence:

- Operationally: classify this as a "suspected contamination cluster" requiring stricter inclusion tiering, not as deterministic identity-based exclusion.

Footer/source:

- table_35b_demographic_signal_enrichment.csv, table_16_trust_tier_counts.csv, table_15b_rule_counts_by_project.csv

## Exact record IDs (for appendix/traceability)

52, 53, 56, 57, 58, 118, 220, 224, 247, 251, 252, 353, 394, 425, 525, 574, 584, 589, 604, 606, 609, 614, 758, 786, 789, 802, 834, 854, 858, 898, 910, 1025, 1033, 1048, 1120, 1323, 1330, 1341, 1488, 1510, 1539, 1557, 1583, 1636, 1673, 1705

## Wording constraints for manuscript/deck consistency

Use:

- "suspected contamination cluster"
- "anomaly concentration signal"
- "co-enrichment with independent trust-risk rules"

Do not use:

- "American Indian records are bots"
- "demographic field proves automation"
- "bot prevalence equals demographic prevalence"

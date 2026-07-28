# IPSA CSBS output status

The current Jessi-facing deliverable is
`IPSA_CSBS_scoring_assignments_master.csv`. It was generated from the live IPSA
REDCap API after Full Data Set access became available and contains exactly four
columns: `ID`, `Visit Month`, `Examiner`, and `Assigned Scoring Clinician`.

The successful run contains 486 completed base-ID CSBS visits across 134 IDs:
113 at 9 months, 105 at 12 months, 81 at 15 months, 89 at 18 months, and 98 at
24 months. Its ID/event multiset exactly matches REDCap report 4692.

IDs ending in `--1` or `--2` are automatically generated double-entry
validation copies. They are ignored entirely for assignments and contact
history. The current live run excluded 688 completed target-month copies: 344
with each suffix. They remain visible only in the exclusion audit.

REDCap provided 302 examiner values in the base-ID population; the remaining
184 genuinely blank examiner cells used Jessi's workload-balanced, seed-42
fallback. Final assignments are Emma 162, Tessa 161, and Axie 163.

Examiner text is preserved as returned by REDCap. All current nonblank values
were recognized through an exact allowlist. For co-scored values, every named
Emma/Tessa/Axie clinician was excluded from that visit's candidate pool and
credited in contact history. Base-ID contact history uses the actual Date of
Evaluation and removes duplicate representations before applying the Never
Seen, Least Visits, and Furthest in Time rules. Double-entry IDs never affect
contact counts, date gaps, workload, or assignments.

All 44 notebook quality checks passed. Supporting assignment, candidate-trace,
exclusion, workload, field-mapping, and run-manifest files are retained for
audit. The older permission-filtered run remains isolated under
`invalid_permission_filtered_run_2026-07-20/` and must not be used.

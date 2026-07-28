# IPSA CSBS-BS Scoring Assignments

Assigns a scoring clinician to every completed IPSA CSBS-BS visit, pulled live from the
REDCap full-data export.

## Layout

```text
csbs_scoring_assignment.ipynb   The generator and its 44 quality checks
csbs_redcap_outputs/            Run outputs (see the README inside)
```

## Run

Open `csbs_scoring_assignment.ipynb` and run it from this folder — `OUTPUT_DIR` is the
relative path `csbs_redcap_outputs`, so the working directory must be this project folder.
The notebook needs an IPSA REDCap API token with full-data-set access; it writes the
Jessi-facing CSV only after the export contains the configured examiner field and every
quality check passes.

## Assignment rules

For a completed base-ID CSBS visit with a known examiner, the scorer is chosen by Never
Seen, Least Visits, Furthest in Time, lower workload, then a seeded random tie-break.
Every Emma, Tessa, or Axie name present in a single- or co-scored examiner value is
excluded from that visit's scorer candidates. Legitimate examiners outside this
three-person scoring pool are preserved.

IDs ending in `--1` or `--2` are automatically generated double-entry validation copies.
They are excluded from assignments and contact history entirely, and appear only in the
exclusion audit.

## Outputs

The primary deliverable is `csbs_redcap_outputs/IPSA_CSBS_scoring_assignments_master.csv`
with exactly four columns: `ID`, `Visit Month`, `Examiner`, and
`Assigned Scoring Clinician`. Supporting assignment, candidate-trace, exclusion, workload,
field-mapping, and run-manifest files sit alongside it.

Run status and validation detail for the current deliverable are in
[csbs_redcap_outputs/README.md](csbs_redcap_outputs/README.md).

## Data handling

The participant-level CSVs in `csbs_redcap_outputs/` are git-ignored; only the field
mapping audit, run manifest, and README are committed. A superseded run made before
full-data access was granted is retained under
[`archive/restricted/csbs-invalid-permission-run-2026-07-20/`](../../archive/restricted/)
and is also git-ignored.

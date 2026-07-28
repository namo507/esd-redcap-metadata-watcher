# NANO / NICO Recruitment Reporting

A standalone, read-only generator for the NANO and NICO recruitment milestone tables,
plus the exploratory notebooks behind the NIH reporting numbers.

| Key | REDCap project | PID | Notes |
| --- | --- | --- | --- |
| `NANO` | NANO Study Surveys | 4218 | De-identified export token (dates stripped, no logging) |
| `NICO` | NICO Study | 3836 | Token is labeled *"MICO"*; the project title is **NICO Study** |

The generator validates the token's project identity and the configured field forms,
types, and codes before requesting the minimum record fields. It writes image-matched
aggregate milestone tables to `recruitment_outputs/` and restricted participant-audit
workbooks/CSVs to the git-ignored `recruitment_audit_secure/`.

## Layout

```text
recruitment_reports.py         CLI entry point and HTML/table rendering
recruitment_config.py          Project registry, categories, month labels
recruitment_ground_truth.py    Inclusion logic, demographic coding, validation
recruitment_workbooks.py       Excel workbook and CSV package writers
docs/                          Verified field mapping and provenance
notebooks/                     Exploratory and NIH-reporting notebooks
scripts/                       Windows refresh wrapper
tests/                         Pipeline and report unit tests
recruitment_outputs/           Aggregate tables (committed)
recruitment_audit_secure/      Participant-level audit (git-ignored)
```

`recruitment_reports.py` and `recruitment_workbooks.py` prepend the repository-root
`shared/` directory to `sys.path` so they can import `redcap_client` and `exports`, which
are shared with the metadata-watcher dashboard.

The verified field mapping, coded demographic rules, inclusion logic, source provenance,
API-rights evidence, and unresolved protocol mappings are recorded in
[docs/recruitment-ground-truth.md](docs/recruitment-ground-truth.md).

## Refresh the tables

Set one or both environment variables first:

```bash
export NANO_API_TOKEN="..."
export NICO_API_TOKEN="..."
```

Run the generator from **this folder** (output paths are relative to the working
directory):

```bash
cd projects/nano-nico-recruitment
python recruitment_reports.py
```

Or use the Windows wrapper, which resolves its own paths and can be run from anywhere:

```powershell
.\scripts\update_recruitment_tables.ps1
```

Both entry points are read-only. They use REDCap `export_*` methods only and do not
create, edit, or delete REDCap records.

Optional flags:

- `--report-date YYYY-MM-DD` to rerun the tables for a specific cut-off date.
- `--output-dir recruitment_outputs` to change the destination folder.
- `--secure-output-dir recruitment_audit_secure` to change the ignored participant-audit
  destination.
- `--no-secure-audit` to write aggregate outputs only.
- `--no-excel` to skip the workbook export.

## Outputs

The refresh keeps only `latest` files in `recruitment_outputs/` and moves dated files
into `recruitment_outputs/archive/`:

```text
recruitment_outputs/nano_recruitment_milestones_latest.html
recruitment_outputs/nico_recruitment_milestones_latest.html
recruitment_outputs/recruitment_milestones_latest.html
recruitment_outputs/recruitment_milestones_latest.xlsx
recruitment_outputs/archive/<name>_<YYYY-MM-DD>.{html,xlsx}
```

With both project tokens present, the restricted directory also receives two project
workbooks and a six-file CSV package. The participant audit includes raw coded
status/date/race/ethnicity evidence and the derived inclusion, racial-minority,
Hispanic-ethnicity, exclusion-reason, and milestone fields. **These files contain
participant identifiers and must remain in the ignored `recruitment_audit_secure/`
directory.**

## Notebooks

| Notebook | Purpose |
| --- | --- |
| `notebooks/nano_recruitment_info.ipynb` | Rebuilds the NANO Recruitment Milestones table with dynamic field discovery instead of hard-coded field names |
| `notebooks/nano_nico_nih_report.ipynb` | Combined NANO + NICO milestone reporting for NIH submissions |

Both read tokens from `NANO_API_TOKEN` / `NICO_API_TOKEN` and never print token values.

## Automation

[`.github/workflows/refresh-recruitment-tables.yml`](../../.github/workflows/refresh-recruitment-tables.yml)
runs daily and on manual dispatch. It installs the pinned dependencies, runs the tests and
the token-shaped-literal scan, calls the generator with `--no-secure-audit`, uploads the
refreshed HTML/XLSX as an artifact, and commits changed public outputs.

Repository setup required:

- `NANO_API_TOKEN` repository secret
- `NICO_API_TOKEN` repository secret
- optionally a `REDCAP_API_URL` repository variable if the endpoint changes

The workflow is read-only against REDCap and never writes or uploads the restricted
participant-audit package.

## Tests

Run from the repository root:

```bash
python -m pytest
```

# ESD Lab REDCap Metadata Watcher

A read-only Streamlit dashboard for inventorying REDCap project metadata. The app builds its study views from the projects that connect successfully in the current session and reports only observed metadata, missing elements, and rule-based issue flags. It does not write to REDCap, retain credentials, or analyze participant outcomes.

## Requirements

- Python 3.12
- Network access to `https://redcap.research.sc.edu/api/`
- A read-only REDCap API token for each study being inspected

The repository records the validated local runtime, Python 3.12.13, in `.python-version`. Streamlit Community Cloud selects Python by major/minor version, so choose Python 3.12 in the deployment dialog.

## Run locally

From a local checkout:

```bash
cd esd-redcap-metadata-watcher
source .venv312/bin/activate
python -m streamlit run app.py
```

For a fresh checkout with Python 3.12 installed:

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Streamlit opens the application at `http://localhost:8501`. Enter one, two, or three project tokens on the gated landing screen, then select **Connect**.

Run the automated checks with:

```bash
source .venv312/bin/activate
python -m pytest
python -m compileall -q app.py redcap_client.py watcher_core.py charts.py exports.py
```

## Token and data handling

- Tokens are entered through password fields at runtime. No token is hardcoded or bundled with the app.
- Token values live only in the active Streamlit session state. They are not written to disk, added to URLs, included in downloads, or intentionally logged.
- **Clear tokens / reset** removes token widgets and all in-session snapshots before rerunning the app.
- API error messages pass through credential redaction before display.
- All REDCap operations are PyCap export methods. No import, delete, or other write method is used.
- Metadata snapshots and generated exports contain structural project metadata only. The optional row-count check is disabled by default and, when enabled, requests only the record identifier field.

For a public deployment, visitors must supply their own authorized token values. Do not prepopulate tokens, put them in `app.py`, commit them to Git, or add the study tokens to Streamlit Cloud secrets.

## REDCap request and rate-limit design

REDCap is contacted only when **Connect** or **Refresh from API** is selected.

- Projects are fetched sequentially.
- A process-wide pacing lock coordinates outbound request starts across active sessions.
- Request starts are separated by at least `REDCAP_MIN_REQUEST_INTERVAL_SECONDS` (1.25 seconds by default).
- Ordinary Streamlit reruns caused by tabs, filters, tables, or downloads reuse the in-session snapshots and make no API calls.
- A session cannot refresh again until `REFRESH_COOLDOWN_SECONDS` has elapsed (60 seconds by default).
- A detected rate-limit response is retried once after `RATE_LIMIT_RETRY_SECONDS` (15 seconds by default); it is not retried indefinitely.
- A failed token or optional permission failure is isolated to its project/call and does not restart successful requests.

These controls are defined together in the configuration block at the top of `app.py` and in `GlobalRequestPacer` in `redcap_client.py`.

## Refresh recruitment milestone tables

The repository includes a standalone generator for the NANO and NICO recruitment milestone tables shown in `recruitment_outputs/`. It pulls live counts from the REDCap API, preserves the NIH-style milestone layout, and writes both individual project pages and a combined two-table dashboard.

Set one or both environment variables before running the refresh:

```bash
export NANO_API_TOKEN="..."
export NICO_API_TOKEN="..."
```

Run the generator directly:

```bash
python recruitment_reports.py
```

Or use the Windows wrapper:

```powershell
.\scripts\update_recruitment_tables.ps1
```

Both entry points are read-only. They use REDCap `export_*` methods only and do not create, edit, or delete REDCap records.

Optional flags:

- `--report-date YYYY-MM-DD` to rerun the tables for a specific cut-off date.
- `--output-dir recruitment_outputs` to change the destination folder.
- `--no-excel` to skip the workbook export.

The refresh keeps only `latest` files in `recruitment_outputs/` and archives dated files under `recruitment_outputs/archive/`.

Latest files in the root output folder:

- `recruitment_outputs/nano_recruitment_milestones_latest.html`
- `recruitment_outputs/nico_recruitment_milestones_latest.html`
- `recruitment_outputs/recruitment_milestones_latest.html`
- `recruitment_outputs/recruitment_milestones_latest.xlsx`

Archived dated files:

- `recruitment_outputs/archive/nano_recruitment_milestones_<date>.html`
- `recruitment_outputs/archive/nico_recruitment_milestones_<date>.html`
- `recruitment_outputs/archive/recruitment_milestones_<date>.html`
- `recruitment_outputs/archive/recruitment_milestones_<date>.xlsx`

## Automate the refresh

The repository includes [refresh-recruitment-tables.yml](.github/workflows/refresh-recruitment-tables.yml), a GitHub Actions workflow that:

- runs on a daily schedule and on manual dispatch
- installs the pinned Python dependencies
- calls `python recruitment_reports.py --output-dir recruitment_outputs`
- uploads the refreshed HTML/XLSX files as a workflow artifact
- commits updated public outputs back to the repository when they change

Repository setup required:

- add `NANO_API_TOKEN` as a repository secret
- add `NICO_API_TOKEN` as a repository secret
- optionally add `REDCAP_API_URL` as a repository variable if the endpoint changes

The workflow is also read-only against REDCap. It only exports metadata and participant-level counts needed to render the milestone tables.

## Add another study

All study-specific configuration is at the top of `app.py`.

1. Add one entry to `PROJECT_REGISTRY`, preserving the desired display order:

   ```python
   "NEW_STUDY": {
       "pid": 0000,
       "label": "New Study",
       "reference": False,
   },
   ```

2. If it should be the default comparison reference, set `REFERENCE_PROJECT = "NEW_STUDY"` and set the registry `reference` flag to `True` only for that project.
3. Add or adjust `DOE_DOC_PATTERNS` only when the study uses an additional explicit date-of-evaluation/date-of-collection naming pattern.
4. Run the tests and start the app. The token field, connection status, study tab, filters, exports, and comparison membership are generated from the registry automatically.

The current implementation assumes every registry entry uses the single `REDCAP_API_URL` configured in `app.py`.

## Deploy on Streamlit Community Cloud

Use a dedicated repository whose root is this directory. Do not make the parent `ideas` directory the repository root because it contains unrelated material that must not be published.

1. Push this directory to a dedicated GitHub repository. A private source repository can still back a public Streamlit app.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app from that repository.
3. Choose `app.py` as the entrypoint and select Python 3.12 under **Advanced settings**.
4. Leave the deployment secrets field empty; study tokens are supplied at runtime.
5. Deploy, validate the token gate, and set the app's sharing setting to public when ready.

Community Cloud manages the container and start command, so this deployment does not need a Dockerfile. The `.streamlit/config.toml` file at the repository root supplies the dashboard theme.

## Secret-hygiene check before every push

The ignore rules exclude local environments, runtime output, `.env` variants, Streamlit secrets, key files, token files, and prompt documents. Before committing, review filenames and token-like content without printing credential values:

```bash
git status --short
git check-ignore -v .venv312 .env .streamlit/secrets.toml
git ls-files | rg '(^|/)(\.env($|\.)|secrets\.toml$)|\.(pem|key|p12|pfx|token|secret)$|_PROMPT\.md$'
rg -l --hidden -g '!.git/**' -g '!.venv*/**' -g '!assets/**' '[A-Fa-f0-9]{32,}' .
git diff --cached --check
```

The two search commands should return no tracked secret-bearing file and no unexpected file containing a token-shaped value. Review any filename they report before pushing; never paste the matching value into terminal output, an issue, or a deployment log.

## Project layout

```text
app.py                    Streamlit UI and configuration
redcap_client.py          Read-only, paced REDCap acquisition
watcher_core.py           Metadata normalization and comparisons
charts.py                 Plotly figures
exports.py                CSV, HTML, and ZIP exports
recruitment_reports.py    API-backed NANO/NICO recruitment table generator
scripts/                  Automation entry points for report refreshes
.streamlit/config.toml    Streamlit theme
assets/                   ESD Lab and USC visual assets
tests/                    Unit tests
requirements.txt          Pinned runtime dependencies
```

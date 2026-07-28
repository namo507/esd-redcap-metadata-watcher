# ESD Lab REDCap Workspace

A multi-project workspace for the ESD Lab's REDCap tooling and study analyses. Each
deliverable lives in its own folder under `projects/`; the modules that more than one
project imports live in `shared/`.

Every REDCap interaction in this repository is read-only. All code paths use PyCap
`export_*` methods; nothing here creates, edits, or deletes REDCap records.

## Projects

| Folder | What it is | Entry point |
| --- | --- | --- |
| [projects/redcap-metadata-watcher/](projects/redcap-metadata-watcher/) | Live read-only Streamlit dashboard over all configured studies: ground-truth counts, completion, instrument comparison, field search | `app.py` |
| [projects/nano-nico-recruitment/](projects/nano-nico-recruitment/) | API-backed recruitment milestone tables for NANO (pid 4218) and NICO (pid 3836), plus the NIH reporting notebooks | `recruitment_reports.py` |
| [projects/csbs-scoring-assignments/](projects/csbs-scoring-assignments/) | IPSA CSBS-BS scoring-clinician assignment generator | `csbs_scoring_assignment.ipynb` |
| [projects/caregiver-cluster-analysis/](projects/caregiver-cluster-analysis/) | Caregiver acceptability cluster analysis on the Infant Autism Screening survey | `caregiver_cluster_analysis.ipynb` |
| [projects/visit-volume-forecast/](projects/visit-volume-forecast/) | 36-Month visit-volume forecasting and model backtests | `visit_volume_forecast.ipynb` |
| [projects/redcap-logs-dashboard/](projects/redcap-logs-dashboard/) | REDCap logging dashboard (TypeScript/Vite; **nested git repository**) | `npm run dev` |

## Shared and root-level directories

| Path | Purpose |
| --- | --- |
| `shared/` | `redcap_client.py` (paced, read-only acquisition), `watcher_core.py` (metadata normalization), `exports.py` (CSV/HTML/ZIP builders) — imported by both Python projects |
| `shared/tests/` | Unit tests for the shared modules |
| `assets/` | ESD Lab and USC brand assets, embedded into HTML exports and the dashboard |
| `.streamlit/config.toml` | Dashboard theme; must stay at the repository root for Streamlit Cloud |
| `.github/workflows/` | Scheduled recruitment-table refresh and dashboard-site rebuild |
| `docs/` | Generated static dashboard published to GitHub Pages — rebuilt by workflow, not edited by hand |
| `archive/` | Superseded output kept for reference — see [archive/README.md](archive/README.md) |

`shared/` is not an installed package. `pyproject.toml` puts it on `sys.path` for pytest,
and the two Python entry points prepend it themselves so `streamlit run` and
`python recruitment_reports.py` both work without any environment setup.

## Environment

- Python 3.12 (the validated local runtime, 3.12.13, is recorded in `.python-version`)
- Network access to `https://redcap.research.sc.edu/api/`
- A read-only REDCap API token per study, supplied at runtime or through environment
  variables — never committed

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

`requirements.txt` holds the pinned runtime dependencies; `requirements-dev.txt` adds
pytest.

## Run the checks

From the repository root:

```bash
source .venv312/bin/activate
python -m pytest
python -m compileall -q shared projects/redcap-metadata-watcher projects/nano-nico-recruitment
```

`pyproject.toml` configures the test paths and import paths, so plain `python -m pytest`
picks up both the shared suite and the recruitment suite.

## Token and data handling

- Tokens live in the repository-root `.env` file, which is **git-ignored**. Copy
  `.env.example` and fill it in. A hosted deployment supplies the same variable names
  through its own secret store; already-set environment variables win over the file.
- Supported: `NANO_API_TOKEN`, `NICO_API_TOKEN`, `IPSA_API_TOKEN`, `ACTION_API_TOKEN`.
  None is hardcoded, and none may be committed.
- The dashboard loads only aggregate structure and form-completion counts. Participant
  rows are reduced to counts in the acquisition layer and discarded before rendering.
- API error messages pass through credential redaction before display.
- Participant-level output never leaves the ignored directories:
  `projects/nano-nico-recruitment/recruitment_audit_secure/`, the restricted CSVs under
  `projects/csbs-scoring-assignments/csbs_redcap_outputs/`, and `archive/restricted/`.
- The `.gitignore` patterns for those paths are depth-independent (`**/`) so they keep
  matching if a project folder is moved or renamed.

### Secret-hygiene check before every push

```bash
git status --short
git check-ignore -v .venv312 .env .streamlit/secrets.toml
git ls-files | rg '(^|/)(\.env($|\.)|secrets\.toml$)|\.(pem|key|p12|pfx|token|secret)$|_PROMPT\.md$'
rg -l --hidden -g '!.git/**' -g '!.venv*/**' -g '!assets/**' \
  -g '!projects/redcap-logs-dashboard/**' -g '!archive/**' '[A-Fa-f0-9]{32,}' .
git diff --cached --check
```

Both search commands should return no tracked secret-bearing file. Review any filename
they report before pushing; never paste a matching value into terminal output, an issue,
or a deployment log.

## Adding a new project

Create `projects/<kebab-case-name>/` with its own `README.md`, keep its inputs and
outputs inside that folder, and add a row to the table above. If it needs the shared
REDCap client or export helpers, add its folder to `pythonpath` in `pyproject.toml` and
prepend `shared/` in its entry point the way the existing projects do.

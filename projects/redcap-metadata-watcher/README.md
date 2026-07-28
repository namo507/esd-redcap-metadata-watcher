# REDCap Metadata Watcher

A read-only Streamlit dashboard for inventorying REDCap project metadata. The app builds
its study views from the projects that connect successfully in the current session and
reports only observed metadata, missing elements, and rule-based issue flags. It does not
write to REDCap, retain credentials, or analyze participant outcomes.

## Layout

```text
app.py        Streamlit UI and all study configuration
charts.py     Plotly figures
```

The acquisition, normalization, and export layers are shared with the recruitment
generator and live at the repository root:

```text
../../shared/redcap_client.py   Read-only, paced REDCap acquisition
../../shared/watcher_core.py    Metadata normalization and comparisons
../../shared/exports.py         CSV, HTML, and ZIP exports
../../assets/                   ESD Lab and USC visual assets
../../.streamlit/config.toml    Dashboard theme
```

`app.py` prepends `shared/` to `sys.path` before importing those modules, because
Streamlit only places the script's own directory on the path.

## Run locally

From the **repository root**:

```bash
source .venv312/bin/activate
python -m streamlit run projects/redcap-metadata-watcher/app.py
```

Streamlit opens the application at `http://localhost:8501`. Enter one, two, or three
project tokens on the gated landing screen, then select **Connect**.

Run from the repository root so Streamlit picks up `.streamlit/config.toml` (the theme)
and so the app resolves `../../assets/` as expected.

## Token and data handling

- Tokens are entered through password fields at runtime. No token is hardcoded or bundled
  with the app.
- Token values live only in the active Streamlit session state. They are not written to
  disk, added to URLs, included in downloads, or intentionally logged.
- **Clear tokens / reset** removes token widgets and all in-session snapshots before
  rerunning the app.
- API error messages pass through credential redaction before display.
- All REDCap operations are PyCap export methods. No import, delete, or other write
  method is used.
- Metadata snapshots and generated exports contain structural project metadata only. The
  optional row-count check is disabled by default and, when enabled, requests only the
  record identifier field.

For a public deployment, visitors must supply their own authorized token values. Do not
prepopulate tokens, put them in `app.py`, commit them to Git, or add study tokens to
Streamlit Cloud secrets.

## REDCap request and rate-limit design

REDCap is contacted only when **Connect** or **Refresh from API** is selected.

- Projects are fetched sequentially.
- A process-wide pacing lock coordinates outbound request starts across active sessions.
- Request starts are separated by at least `REDCAP_MIN_REQUEST_INTERVAL_SECONDS`
  (1.25 seconds by default).
- Ordinary Streamlit reruns caused by tabs, filters, tables, or downloads reuse the
  in-session snapshots and make no API calls.
- A session cannot refresh again until `REFRESH_COOLDOWN_SECONDS` has elapsed
  (60 seconds by default).
- A detected rate-limit response is retried once after `RATE_LIMIT_RETRY_SECONDS`
  (15 seconds by default); it is not retried indefinitely.
- A failed token or optional permission failure is isolated to its project/call and does
  not restart successful requests.

These controls are defined together in the configuration block at the top of `app.py` and
in `GlobalRequestPacer` in `../../shared/redcap_client.py`.

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

2. If it should be the default comparison reference, set `REFERENCE_PROJECT = "NEW_STUDY"`
   and set the registry `reference` flag to `True` only for that project.
3. Add or adjust `DOE_DOC_PATTERNS` only when the study uses an additional explicit
   date-of-evaluation/date-of-collection naming pattern.
4. Run the tests and start the app. The token field, connection status, study tab,
   filters, exports, and comparison membership are generated from the registry
   automatically.

The current implementation assumes every registry entry uses the single `REDCAP_API_URL`
configured in `app.py`.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub. A private source repository can still back a public
   Streamlit app.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app from that
   repository.
3. Set the entrypoint to **`projects/redcap-metadata-watcher/app.py`** and select
   Python 3.12 under **Advanced settings**.
4. Leave the deployment secrets field empty; study tokens are supplied at runtime.
5. Deploy, validate the token gate, and set the app's sharing setting to public when
   ready.

Community Cloud manages the container and start command, so this deployment does not need
a Dockerfile. The repository-root `.streamlit/config.toml` supplies the dashboard theme.

## Tests

The shared modules this app depends on are covered by `../../shared/tests/`. Run the full
suite from the repository root with `python -m pytest`.

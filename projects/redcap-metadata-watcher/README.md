# ESD Lab REDCap Studies Dashboard

A read-only, always-on Streamlit dashboard over every configured REDCap study. It
discovers each project's instruments, fields, and events from the API, reports
ground-truth counts and completion, and lets you compare instruments that more
than one study shares.

There is **no token entry in the UI**. Tokens come from the environment.

## Layout

```text
app.py            Streamlit UI — tabs, filters, KPI tiles
study_config.py   Study registry, .env loading, refresh/pacing settings
redcap_live.py    Read-only acquisition; reduces records to counts
metrics.py        Pure metric functions over snapshots
charts.py         Validated-palette Plotly figures
tests/            Read-only contract and metric definitions
```

Shared with the recruitment project at the repository root:
`../../shared/redcap_client.py` (request pacer, error redaction),
`../../assets/` (brand assets), `../../.streamlit/config.toml` (theme).

## Configure

Copy `.env.example` to `.env` at the repository root and fill in read-only
tokens:

```bash
NANO_API_TOKEN=…
NICO_API_TOKEN=…
IPSA_API_TOKEN=…
ACTION_API_TOKEN=…
```

`.env` is git-ignored. Any study whose token is absent is skipped and listed as
unconfigured in the sidebar — the dashboard runs fine with one, two, or all four.
Environment variables already set in the process win over the file, which is how
a hosted deployment injects its own secrets.

## Run

From the **repository root**:

```bash
source .venv312/bin/activate
python -m streamlit run projects/redcap-metadata-watcher/app.py
```

Opens on `http://localhost:8501`. Startup reads all configured projects once
(about 30 seconds for four studies at the default 1.25 s pacing) and caches the
result.

## What the tabs do

| Tab | Contents |
| --- | --- |
| **Portfolio** | Every study side by side: records, instruments, fields, events, completion rate; per-study bar charts; structural profile |
| **Study detail** | One study: KPI tiles, completion by instrument (stacked by REDCap status), completion rate, field-type mix, records per event, searchable instrument and event tables, structural signals |
| **Instrument comparison** | Which instruments are shared, a pairwise overlap heatmap, and a field-by-field harmonization diff for any shared instrument |
| **Field explorer** | Full-text search across every field in every selected study, with facets for study, instrument, field type, and the required / identifier / branching / unlabelled flags; CSV download of the filtered metadata |
| **Definitions** | The read-only guarantee, the no-participant-data guarantee, and every metric definition |

## Refresh behaviour

Snapshots are cached for `REDCAP_REFRESH_INTERVAL_SECONDS` (default 1800 = 30
min). Ordinary interaction — switching tabs, filtering, sorting, searching —
reuses the cached snapshot and makes **no** API call. A one-minute ticker checks
the snapshot's age and triggers a refetch once the cache expires. **Refresh from
REDCap now** in the sidebar forces one immediately. Outbound requests are
serialised behind the shared process-wide pacer, so concurrent viewers cannot
stampede the API.

## The two guarantees

**It cannot write.** `redcap_live.ReadOnlyClient.export` allowlists the REDCap
`content` values it will request and rejects the parameters REDCap uses to mutate
a project (`action`, `data`, `returnContent`, `overwriteBehavior`,
`forceAutoNumber`). A violation raises `ReadOnlyViolation` *before* any network
request. `tests/test_redcap_live.py` asserts both halves, including that a
blocked call never reaches the transport.

**It cannot leak participant data.** Completion figures come from one export of
the `<form>_complete` status fields. `_summarize_completion` reduces those rows
to per-instrument and per-event counts inside the acquisition layer and the raw
response is discarded before the snapshot is returned. No participant identifier
or response value exists anywhere the UI can reach it, and the CSV download
contains field *metadata* only.

Both guarantees are about this application's behaviour. The token itself is the
real security boundary — issue export-only tokens.

## Adding a study

1. Add a `StudyDefinition` to `STUDY_REGISTRY` in `study_config.py`.
2. Add its token to `.env` under the name you gave `token_env`.
3. Add a colour for its key to `charts.STUDY_COLORS`, re-validated against the
   dataviz palette checks (see the module docstring for the current values).

Everything else — tabs, filters, comparisons, KPI tiles — is generated from the
registry.

## Chart palette

The four study hues (`#3366FF #D74E2D #00A17A #8B5CF6`) and the completion-status
hues (`#1F8A5F #7C3AED #D74E2D #9CA3AF`) were validated for lightness band,
chroma floor, colour-vision-deficiency separation, normal-vision separation, and
contrast, in both light and dark mode. Two of the four study colours are the
existing ESD brand blue and red. Study colour is keyed to the study, so filtering
never repaints the remaining series. See the `charts.py` docstring.

## Tests

```bash
python -m pytest        # from the repository root
```

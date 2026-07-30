# Caregiver Acceptability Cluster Analysis

Decision-focused analysis of the Infant Autism Screening caregiver survey. Caregivers are
clustered on their Thoughts, Feelings, and Attitudes (TFA) responses; the four
characterization families — Demographics, Values, Autism Knowledge, and Screen vs Not —
are held out of clustering and examined only after cluster assignment.

Primary API/cache snapshot: **2026-07-30**. The **2026-01-20** CSV remains a
validated fallback and regression reference.

## Layout

```text
caregiver_cluster_analysis.ipynb                          Full analysis, writes every figure and table
caregiver_analysis_pipeline.py                            Reusable API, trust-screen, modeling, and figure pipeline
build_notebook.py                                         Idempotent notebook integration utility
config.yaml                                               Seeds, thresholds, tiers, and modeling configuration
requirements.txt                                          Pinned notebook dependencies
cleaned_autism_study_data.csv                             Analysis-ready dataset
InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv         Raw REDCap export (coded)
InfantAutismScreenin-FullDataset_DATA_LABELS_2026-01-20_0940.csv  Raw REDCap export (labeled)
Analysis questions - Sheet3.csv                           Requested analysis questions
Caregiver Outputs/                                        Generated figures (PNG + PDF) and tables (CSV)
data_cache/                                               Git-ignored Parquet API caches with SHA-256 sidecars
ESD_Caregiver_Cluster_Analysis.pptx / .key                Presentation decks
```

## Run

Install the pinned environment, then execute from this folder:

```bash
python3 -m pip install -r requirements.txt
python3 -m jupyter nbconvert --execute --to notebook --inplace \
  caregiver_cluster_analysis.ipynb
```

The repository-root `.env` must contain `REDCAP_API_URL`,
`TOKEN_CLEAN_4797`, and `TOKEN_DIRTY_4581`. Tokens are never hardcoded.
The first run pulls records, metadata, and instruments for both projects.
Later runs verify and reuse dated caches, so they do not need to re-hit the API.
The pipeline halts if the expected 177/1,779 record counts change.

## Outputs

`Caregiver Outputs/` contains the original six figures plus fraud-facing and
cluster-validation figures through `figure_20`, each as 300-DPI PNG and PDF.
Tables `table_1` through `table_33` cover the original analysis, API provenance,
field differences, R1–R10, false-positive rates, tier counts, detector
concordance, five inclusion definitions, Firth logistic models, Gaussian-mixture
diagnostics, bootstrap LRT, BCH correction, tipping points, power/precision,
data quality, and figure QA.

`record_flags.parquet` is the internal row-level audit artifact. It and
`data_cache/` are git-ignored; aggregate outputs contain no email, ZIP, date of
birth, occupation text, or API token.

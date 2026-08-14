# Caregiver Acceptability Cluster Analysis

Decision-focused analysis of the Infant Autism Screening caregiver survey. Caregivers are
clustered on their Thoughts, Feelings, and Attitudes (TFA) responses; the four
characterization families — Demographics, Values, Autism Knowledge, and Screen vs Not —
are held out of clustering and examined only after cluster assignment.

Primary API/cache snapshot: **2026-07-30**. The **2026-01-20** CSV remains a
validated clean fallback and regression reference; the full dirty 4581
branching-audit validation surface comes from the hashed API cache.

## Layout

```text
caregiver_cluster_simple_metrics.ipynb                    Reader-facing summary notebook for the current cached project state
caregiver_analysis_pipeline.py                            Reusable API, trust-screen, modeling, and figure pipeline
build_notebook.py                                         Idempotent notebook integration utility
config.yaml                                               Seeds, thresholds, tiers, and modeling configuration
requirements.txt                                          Pinned notebook dependencies
cleaned_autism_study_data.csv                             Analysis-ready dataset
InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv         Clean 4797 fallback CSV (coded)
InfantAutismScreenin-FullDataset_DATA_LABELS_2026-01-20_0940.csv  Clean 4797 fallback CSV (labeled)
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
  caregiver_cluster_simple_metrics.ipynb
```

The repository-root `.env` must contain `REDCAP_API_URL`,
`TOKEN_CLEAN_4797`, and `TOKEN_DIRTY_4581`. Tokens are never hardcoded.
The first run pulls records, metadata, and instruments for both projects.
Later runs verify and reuse dated caches, so they do not need to re-hit the API.
The pipeline halts if the expected 177/1,779 record counts change and if the
verified-human timing reference moves off 131 completed 4797 records.

## Outputs

`Caregiver Outputs/` contains the original six figures plus fraud-facing and
cluster-validation figures through `figure_20`, each as 300-DPI PNG and PDF.
Tables `table_1` through `table_34` cover the original analysis, API provenance,
field differences, R1–R10, false-positive rates, tier counts, detector
concordance, five inclusion definitions, Firth logistic models, Gaussian-mixture
diagnostics, bootstrap LRT, BCH correction, tipping points, power/precision,
data quality, figure QA, and the branching-logic audit table keyed to the
generalized R8 rule.

R8 now covers three logical inconsistency families: autistic-child follow-up
answers that persist when `fif_num_autistic=0`, manually specified branching
contradictions in the prenatal-testing and earlier-diagnosis blocks, and the
impossible child-age-band count check. Those contradictions stay Tier 1 because
they are hard logical impossibilities, not soft suspicion signals.

The current regression fixtures for the new branching audit include sibling-pair
collisions `31`, `118`, `273`, `571`, `768`, `1076`, `1554`, `1699`; tested
reason orphans `966`, `967`, `968`, `969`, `970`, `971`, `972`, `973`, `974`,
`975`, `976`, `977`, `978`, `979`, `981`, `1591`; not-tested reason orphans
`364`, `416`, `987`, `1026`, `1707`, `1741`; earlier-diagnosis yes-orphans
`205`, `1025`; and earlier-diagnosis no-orphans `1137`, `1642`, `1678`.

`record_flags.parquet` is the internal row-level audit artifact. It and
`data_cache/` are git-ignored; aggregate outputs contain no email, ZIP, date of
birth, occupation text, or API token.

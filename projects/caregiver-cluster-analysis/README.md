# Caregiver Acceptability Cluster Analysis

Decision-focused analysis of the Infant Autism Screening caregiver survey. Caregivers are
clustered on their Thoughts, Feelings, and Attitudes (TFA) responses; the four
characterization families — Demographics, Values, Autism Knowledge, and Screen vs Not —
are held out of clustering and examined only after cluster assignment.

Data snapshot: REDCap exports dated **2026-01-20**.

## Layout

```text
caregiver_cluster_analysis.ipynb                          Full analysis, writes every figure and table
cleaned_autism_study_data.csv                             Analysis-ready dataset
InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv         Raw REDCap export (coded)
InfantAutismScreenin-FullDataset_DATA_LABELS_2026-01-20_0940.csv  Raw REDCap export (labeled)
Analysis questions - Sheet3.csv                           Requested analysis questions
Caregiver Outputs/                                        Generated figures (PNG + PDF) and tables (CSV)
ESD_Caregiver_Cluster_Analysis.pptx / .key                Presentation decks
```

## Run

Open `caregiver_cluster_analysis.ipynb` and run it from this folder. `PROJECT_DIR` is
resolved relative to the working directory and `OUTPUT_DIR` is `Caregiver Outputs`, so the
notebook regenerates every numbered figure and table in place, along with
`Caregiver Outputs/output_manifest.csv`.

The notebook reads only the CSVs in this folder — it makes no REDCap API calls, so no
token is required.

## Outputs

`Caregiver Outputs/` holds six numbered figures (each as PNG and PDF) and the numbered
tables `table_1` through `table_11`, covering the file inventory, data-quality summary,
cluster diagnostics, cluster-defining profiles, cohort flow, screening outcome, knowledge,
values, demographics, decision summary, ASD family context, and sensitivity checks.

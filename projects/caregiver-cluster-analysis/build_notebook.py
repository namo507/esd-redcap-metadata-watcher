"""Idempotently integrate the REDCap trust-screen upgrade into the notebook."""

from pathlib import Path
import nbformat


PROJECT_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = PROJECT_DIR / "caregiver_cluster_analysis.ipynb"
UPGRADE_TAG = "caregiver-upgrade-v2"


def tagged_markdown(source: str):
    cell = nbformat.v4.new_markdown_cell(source)
    cell.metadata["tags"] = [UPGRADE_TAG]
    return cell


def tagged_code(source: str):
    cell = nbformat.v4.new_code_cell(source)
    cell.metadata["tags"] = [UPGRADE_TAG]
    return cell


if not NOTEBOOK_PATH.exists():
    print(f"Target notebook {NOTEBOOK_PATH.name} not found. Skipping integration.")
    raise SystemExit(0)

notebook = nbformat.read(NOTEBOOK_PATH, as_version=4)
notebook.cells = [
    cell
    for cell in notebook.cells
    if UPGRADE_TAG not in cell.get("metadata", {}).get("tags", [])
]

notebook.cells[0].source = """# Caregiver acceptability profiles, REDCap trust tiers, and held-out outcomes

**Early Social Development Lab · Infant Autism Screening caregiver survey**

This notebook is the reader-facing, reproducible analysis. It keeps the original
cluster-validation and held-out-outcome rigor, adds hashed REDCap API provenance,
evaluates all 1,956 records with auditable trust rules, and tests whether the
two-profile conclusion changes under five pre-specified inclusion definitions.

Direct identifiers never enter displayed or committed aggregate outputs. Raw API
caches and the record-level audit artifact remain git-ignored."""

notebook.cells[1].source = """## tl;dr

- The status-quo 4797 analysis reproduces every locked regression target: 135
  selected records, 131 clusterable, profile sizes 84/47, silhouette 0.180,
  mean 80% subsample ARI 0.704, and 56/80 (70.0%) versus 13/47 (27.7%)
  Definitely-yes responses.
- Within the verified-clean project, the Definitely-yes gap remains
  **42.3–48.5 percentage points** across trust definitions 1–3. The
  Firth-adjusted profile odds ratio remains **7.39–11.09**, with every
  profile-likelihood interval above 1. The main association is robust to the
  pre-specified trust screen.
- Project 4581 is not promoted into the primary analysis. Only 71/1,779 records
  pass every tier-defining rule and 49 are clusterable; its replication is
  directionally consistent but very imprecise (Firth OR 9.74, 95% CI 1.96–96.77).
- The PU/source classifier implies a 94.4% separation fraction in 4581, but this
  is **not an identifiable bot prevalence** because recruitment period and
  instrument differences violate the required exchangeability/SCAR assumption.
- Probabilistic modeling is divergent evidence: BIC prefers a three-component
  diagonal Gaussian mixture rather than k=2. The two k-means profiles remain a
  useful exploratory gradient, not a definitive taxonomy.

| Decision | Notebook default | Status |
|---|---|---|
| D1. Does 4581 enter the primary analysis? | No; replication only | Implemented |
| D2. Binary flag or graded tier? | Four graded tiers | Implemented |
| D3. Assumed contamination rate? | None | Not identifiable; model separation is caveated |
| D4. Primary knowledge score? | Binary verified correct-count | Pre-specified; graded subset is sensitivity |
| D5. Four cluster-excluded records? | Full tipping-point analysis | Implemented; gap remains 39.0–44.5 pp |"""

notebook.cells[2].source = """## Context & Methods

### Research question

Do caregiver acceptability responses support a reproducible two-profile summary,
and does the held-out Definitely-yes screening difference survive defensible
record-trust decisions?

### Key assumptions and pre-specifications

- Project 4797 is the verified-clean reference and primary analytic cohort.
  Project 4581 is an ungated, lower-quality replication/methods cohort.
- `uid = project_id + "_" + record_id` is the only join key; numeric REDCap IDs
  overlap across projects.
- Four decimal-minute `get_time_*` fields define timing. Ambiguous
  `survey_time_*` strings are never parsed.
- R1–R10 and the tier definitions are fixed in `config.yaml` before inspecting
  held-out outcomes. R10 is retained as validation-only because its observed
  false-positive rate exceeds the prompt's usability bar and the API does not
  expose email values under current permissions.
- Screening, autism knowledge, values, demographics, and family context never
  enter composites, trust rules, embeddings, profile fitting, or profile naming.
- The binary verified knowledge correct-count is primary. The locally documented
  graded mapping is incomplete and was noted after statistical significance, so
  it is sensitivity evidence only.
- Model separation from the clean human envelope is not a bot probability.
  Causal language is not supported by this observational design."""

setup_source = notebook.cells[4].source
setup_source = setup_source.replace(
    "import platform\n",
    "import platform\nimport importlib.metadata\n",
)
setup_source = setup_source.replace(
    "from IPython.display import Markdown, display\n",
    "from IPython.display import Image, Markdown, display\n",
)
setup_source = setup_source.replace(
    "from sklearn.preprocessing import StandardScaler\n",
    "from sklearn.preprocessing import StandardScaler\n\n"
    "from caregiver_analysis_pipeline import (\n"
    "    load_config as load_upgrade_config,\n"
    "    load_redcap_sources,\n"
    "    run_upgrade,\n"
    ")\n",
)
setup_source = setup_source.replace(
    '"figure.dpi": 140,\n    "savefig.dpi": 240,',
    '"figure.dpi": 140,\n    "savefig.dpi": 300,',
)
setup_source = setup_source.replace(
    '"component": ["Python", "pandas", "NumPy", "SciPy", "scikit-learn", "Matplotlib"],\n'
    '    "version": [\n'
    "        platform.python_version(),\n"
    "        pd.__version__,\n"
    "        np.__version__,\n"
    "        scipy.__version__,\n"
    "        sklearn.__version__,\n"
    "        mpl.__version__,\n"
    "    ],",
    '"component": [\n'
    '        "Python", "pandas", "NumPy", "SciPy", "scikit-learn", "Matplotlib",\n'
    '        "seaborn", "statsmodels", "requests", "PyYAML", "pyarrow",\n'
    '        "python-dotenv", "umap-learn", "shap",\n'
    "    ],\n"
    '    "version": [\n'
    "        platform.python_version(), pd.__version__, np.__version__, scipy.__version__,\n"
    "        sklearn.__version__, mpl.__version__,\n"
    '        *[importlib.metadata.version(name) for name in [\n'
    '            "seaborn", "statsmodels", "requests", "PyYAML", "pyarrow",\n'
    '            "python-dotenv", "umap-learn", "shap",\n'
    "        ]],\n"
    "    ],",
)
notebook.cells[4].source = setup_source

notebook.cells[5].source = """## Data

### 2. Load hashed REDCap API caches and validate the local fallback

The primary analysis frame now comes from `content='record'` API data with survey
fields, while metadata and instrument definitions are persisted alongside it.
Daily Parquet caches have SHA-256 sidecars, and the pipeline halts if project
counts differ from 177 and 1,779. The January CSV remains a lineage-checked
fallback/reference for the locked 4797 regression."""

load_source = notebook.cells[6].source
load_source = load_source.replace(
    "raw_header = pd.read_csv(FILES[\"raw\"], nrows=0)\n",
    "upgrade_config = load_upgrade_config(PROJECT_DIR)\n"
    "redcap_bundle = load_redcap_sources(PROJECT_DIR, upgrade_config)\n\n"
    "raw_header = pd.read_csv(FILES[\"raw\"], nrows=0)\n",
)
load_source = load_source.replace(
    'raw_df = pd.read_csv(FILES["raw"], usecols=required_raw_vars, low_memory=False)\n',
    'raw_df = redcap_bundle.records["clean_4797"][required_raw_vars].copy()\n'
    "raw_df = raw_df.apply(pd.to_numeric, errors=\"coerce\")\n",
)
notebook.cells[6].source = load_source

lineage_source = notebook.cells[8].source
lineage_source = lineage_source.replace(
    "assert raw_ids.is_unique and labeled_ids.is_unique and selected_ids.is_unique\n",
    "api_raw_ids = pd.to_numeric(\n"
    "    redcap_bundle.records[\"clean_4797\"][\"record_id\"], errors=\"raise\"\n"
    ").astype(int)\n"
    "assert set(api_raw_ids) == set(pd.to_numeric(raw_ids, errors=\"raise\").astype(int))\n"
    "assert raw_ids.is_unique and labeled_ids.is_unique and selected_ids.is_unique\n",
)
notebook.cells[8].source = lineage_source

caption_replacements = {
    14: (
        "Silhouette and separation remain modest; different criteria do not fully agree.",
        "n=131 clusterable caregivers from the 4797 status-quo cohort; different criteria "
        "do not fully agree. Screening, knowledge, values, and demographics were held out.",
    ),
    16: (
        "Displayed domain means use available raw composites (Table 4 gives domain-specific n); model fitting used median-imputed sparse missing composites. Held-out outcomes do not determine membership or labels.",
        "n=131 clusterable caregivers from the 4797 status-quo cohort. Displayed means use "
        "available composites; model fitting median-imputed sparse missing composites. "
        "All characterization outcomes were held out.",
    ),
    19: (
        "Primary endpoint: Definitely yes vs all other valid responses. Error bars are Wilson 95% intervals.",
        "n=127 cluster-assigned caregivers with valid screening responses. Primary endpoint: "
        "Definitely yes vs all other valid responses; error bars are Wilson 95% intervals. "
        "Screening was held out of clustering.",
    ),
    21: (
        "Seven verified keys are primary; error bars are bootstrap 95% intervals and item rates use item-specific available cases. Behavior-age scoring remains unresolved and appears only in table sensitivities.",
        "4797 status-quo cluster-assigned cohort with item-specific available n. Seven "
        "verified keys are primary; error bars are bootstrap 95% intervals. Knowledge was held out.",
    ),
    23: (
        "No comparison survives Benjamini–Hochberg FDR correction",
        "n=131 cluster-assigned caregivers with value-specific available cases. Values were "
        "held out of clustering. No comparison survives Benjamini–Hochberg FDR correction",
    ),
    25: (
        "For displayed binary demographic measures, event or complement counts below 5 are suppressed.",
        "n=131 cluster-assigned caregivers with measure-specific available cases. Demographics "
        "were held out of clustering; event or complement counts below 5 are suppressed.",
    ),
}
for cell_index, (old_text, new_text) in caption_replacements.items():
    notebook.cells[cell_index].source = notebook.cells[cell_index].source.replace(
        old_text, new_text
    )
notebook.cells[25].source = notebook.cells[25].source.replace(
    'demographics_summary_df.get("disclosure_safe", False).fillna(False)',
    'demographics_summary_df["disclosure_safe"].astype("boolean").fillna(False)',
)

notebook.cells[32].source = """## Upgrade results"""

upgrade_cells = [
    tagged_markdown("""### 15. Trust-screen implementation and API provenance

The following cell runs the full upgrade from the verified cache. It exports only
aggregate tables and figures; `record_flags.parquet` remains git-ignored. The
status-quo regression checks are hard failures, so downstream outputs cannot be
created if the original result changes unexpectedly."""),
    tagged_code("""upgrade = run_upgrade(PROJECT_DIR)
display(upgrade["bundle"].cache_inventory)
display(upgrade["data_quality"])
display(upgrade["regression_checks"])"""),
    tagged_markdown("""### 16. Deterministic rules before models

R1–R10 are named, thresholded, and independently evaluated in the 131 completed
4797 caregivers. R10 is diagnostic only; the explicit tier definition uses
R1/R2/R5/R8/R9 for confirmed-invalid and R3/R4/R6/R7 for graded suspicion."""),
    tagged_code("""display(upgrade["rule_definitions"])
display(upgrade["false_positive"])
display(upgrade["tier_counts"])
display(upgrade["rule_counts"])
display(Image(filename=OUTPUT_DIR / "figure_7_timing_ecdf.png", width=950))
display(Image(filename=OUTPUT_DIR / "figure_9_rule_cooccurrence.png", width=1100))"""),
    tagged_markdown("""### 17. Human-envelope distance and the identifiability limit

Isolation Forest, novelty-mode Local Outlier Factor, and robust Mahalanobis
distance are fit only on clean Tier-4 records. A gradient-boosted
positive-unlabeled/source classifier supplies a fourth, independent score.
Agreement—not nominal model accuracy—is the reportable validation quantity.

The Elkan–Noto-style fraction is retained only as a sensitivity estimate. The
clean and legacy projects differ in instrument and recruitment period, so the
data do not identify how much classifier separation is fraud versus legitimate
cohort drift."""),
    tagged_code("""display(upgrade["contamination_summary"])
display(upgrade["detector_agreement"])
display(upgrade["detector_importance"].head(15))
display(Image(filename=OUTPUT_DIR / "figure_11_detector_scores_and_agreement.png", width=1100))
display(Image(filename=OUTPUT_DIR / "figure_11b_pu_feature_importance.png", width=1100))"""),
    tagged_markdown("""### 18. The deliverable: conclusion sensitivity across trust definitions

Definitions 1–3 are the primary robustness test. Definition 4 is a pooled,
shared-item sensitivity only. Definition 5 is a lower-quality independent
replication attempt. The odds ratios use Firth penalization with
profile-likelihood confidence intervals."""),
    tagged_code("""display(upgrade["sensitivity"].round(3))
display(upgrade["characterization"].round(3))
display(upgrade["tipping"].round(2))
display(Image(filename=OUTPUT_DIR / "figure_20_tier_sensitivity.png", width=1050))"""),
    tagged_markdown("""### 19. Probabilistic profiles, consensus, and hardened inference

K-means stays primary for continuity. Gaussian mixtures, a parametric bootstrap
likelihood-ratio test, classification entropy, BCH-corrected distal outcomes,
and a 1,000-repeat consensus matrix are convergent or divergent evidence—not a
replacement label. BIC preferring k=3 is a substantive warning against treating
the two profiles as fixed natural types."""),
    tagged_code("""display(upgrade["gmm_grid"].head(12).round(3))
display(upgrade["blrt"].round(4))
display(upgrade["latent_quality"].round(3))
display(upgrade["bch_table"].round(2))
display(upgrade["logistic_table"].round(4))
display(upgrade["precision"].round(3))
display(Image(filename=OUTPUT_DIR / "figure_2_primary_cluster_profiles.png", width=1050))
display(Image(filename=OUTPUT_DIR / "figure_15_consensus_matrix.png", width=850))
display(Image(filename=OUTPUT_DIR / "figure_16_case_silhouette.png", width=900))
display(Image(filename=OUTPUT_DIR / "figure_18_logistic_forest.png", width=950))"""),
    tagged_markdown("""## Takeaways

The two-profile screening association is robust to the pre-specified trust
screen inside the verified-clean 4797 cohort. The strict Tier-4 estimate is
similar to the status quo and remains large and statistically compatible with
a meaningful association. The four cluster-excluded, screening-valid records
cannot erase the result under any allocation in the tipping-point analysis.

The profiles are best described as an exploratory acceptability gradient.
Silhouette remains modest, individual negative-silhouette cases are visible,
and the BIC-optimal Gaussian mixture has three components. These facts argue
against diagnostic or permanent “caregiver type” language.

Project 4581 should not be pooled into the primary manuscript estimate. Its tiny
Tier-4 replication is directionally consistent, but the wide interval and
instrument mismatch make it supportive at most. The model-implied 94.4%
separation fraction must not be quoted as a bot rate.

### Limitations and next steps

- Residual fraud can remain after deterministic screening; conversely, some
  flagged records may be genuine caregivers.
- A 131-person human reference envelope may not generalize to other recruitment
  periods or populations.
- Project-level instrument and recruitment differences confound every
  4797-versus-4581 classifier.
- The prior LightGBM model and its 142 labeled cases were unavailable in the
  scoped project; recover them and document how the 111 “alleged bot” labels were
  assigned before supervised reuse.
- Confirm the original 135-record selection rule and the first-chosen knowledge
  scoring method with the research team.
- Rotate both REDCap tokens because they previously appeared in plaintext, then
  update only the git-ignored repository `.env`.

### Validation assessment

**Share with caveats.** The primary association and exact regression targets are
reproduced, trust-tier sensitivity is stable, and outputs are privacy-scoped.
The latent taxonomy and the 4581 contamination fraction remain uncertain, so
manuscript language must preserve those caveats."""),
]
notebook.cells.extend(upgrade_cells)

nbformat.validate(notebook)
nbformat.write(notebook, NOTEBOOK_PATH)
print(f"Updated {NOTEBOOK_PATH} with {len(notebook.cells)} cells")

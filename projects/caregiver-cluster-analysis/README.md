# Caregiver Acceptability Cluster Analysis

## What this project does

This project analyzes **1,956 caregiver survey responses** from the Infant Autism
Screening study to answer one question: _Are there distinct caregiver profiles
that predict willingness to screen for autism at the 4-month well-child visit?_

Caregivers are clustered on their **Thoughts, Feelings, and Attitudes (TFA)**
responses using K-Means (k=2). The four characterization families —
Demographics, Values, Autism Knowledge, and Screen vs Not — are deliberately
**held out** of clustering and examined only after cluster assignment, so the
profiles emerge from attitudes alone.

Because the study ran across two REDCap projects (a verified-clean project 4797
with 177 records and a legacy unverified project 4581 with 1,779 records), a
reproducible **trust screen** (rules R1–R10, four tiers, three behavioral
detectors) separates likely-bot records from legitimate respondents before any
substantive analysis.

---

## Quick start

### 1. Set up the environment

```bash
# From the project root:
python3 -m pip install -r requirements.txt
```

### 2. Configure API credentials

The repository-root `.env` file must contain three variables:

```
REDCAP_API_URL=https://your-redcap-instance/api/
TOKEN_CLEAN_4797=<your API token for project 4797>
TOKEN_DIRTY_4581=<your API token for project 4581>
```

Tokens are **never** hardcoded. The pipeline reads them from environment
variables at runtime.

### 3. Run the full pipeline

```bash
python3 -m jupyter nbconvert --execute --to notebook --inplace \
  caregiver_cluster_simple_metrics.ipynb
```

**First run**: pulls records, metadata, and instruments for both projects from
REDCap and caches them as dated Parquet files in `data_cache/`.

**Later runs**: verify and reuse existing caches — no API calls needed.

The pipeline halts automatically if the expected record counts change
(177 for 4797, 1,779 for 4581) or if the verified-human timing reference moves
off the 131 completed 4797 records.

### 4. Run the tests

```bash
python3 -m pytest tests/ -v
```

---

## Directory guide

### Core code (run these)

| File | Purpose |
|------|---------|
| `caregiver_cluster_simple_metrics.ipynb` | **Start here.** The reader-facing summary notebook that runs the full pipeline and displays all results. |
| `caregiver_analysis_pipeline.py` | The 4,600-line reusable pipeline: API ingestion, trust screen, clustering, modeling, and figure generation. All analysis logic lives here. |
| `config.yaml` | Seeds, thresholds, tiers, and modeling configuration. Change parameters here, not in the code. |
| `requirements.txt` | Pinned Python dependencies. |

### Data files

| File | Purpose |
|------|---------|
| `data_cache/` | **Git-ignored.** Dated Parquet API caches with SHA-256 integrity sidecars. Auto-populated on first run. |
| `cleaned_autism_study_data.csv` | Analysis-ready dataset (direct identifiers blanked). |
| `InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv` | Clean 4797 fallback CSV (coded). Used as a validated regression reference. |
| `InfantAutismScreenin-FullDataset_DATA_LABELS_2026-01-20_0940.csv` | Clean 4797 fallback CSV (labeled). |
| `Analysis questions - Sheet3.csv` | Original analysis questions from the research team. |

### Generated outputs

| Directory / File | Purpose |
|------------------|---------|
| `Caregiver Outputs/` | All generated figures (PNG + PDF) and tables (CSV). See the output guide below. |
| `Caregiver Outputs/record_flags.parquet` | **Git-ignored.** Row-level audit artifact with per-record rule flags, tier assignments, and detector scores. No PII. |
| `Caregiver Outputs/output_manifest.csv` | Auto-generated inventory of every output file with row/column counts and SHA-256 hashes. |

### Presentation decks

| File | Purpose |
|------|---------|
| `ESD_Bot_Detection_Review.pptx / .pdf` | Bot detection methodology review deck. |
| `ESD_Caregiver_Cluster_Analysis.pptx / .key` | Primary cluster analysis presentation. |
| `ESD_Caregiver_Cluster_Analysis_10.pptx` | 10-slide condensed version. |
| `ESD_Caregiver_Cluster_and_Trust_Screen.pptx / .pdf` | Combined cluster + trust screen deck. |
| `ESD_Caregiver_Findings_Plain_Language.pptx / .pdf` | Plain-language summary for non-technical audiences. |
| `ESD_Caregiver_Robustness_Update.pptx` | Robustness analysis update deck. |
| `BOT_DETECTION_SLIDE_UPDATE_2026-08-25.md` | Slide content for the demographic signal addendum. |

### Build scripts (for deck generation)

| File | Purpose |
|------|---------|
| `build_deck.py` | Python-pptx script for the full analysis deck. |
| `build_deck.js` | JavaScript equivalent for the full deck. |
| `build_deck_10slide.js` | JavaScript script for the 10-slide version. |
| `build_simple_deck.py` | Simplified deck builder. |
| `build_notebook.py` | Idempotent notebook integration utility. |
| `esd_deck_lib.py` | Shared deck construction helpers (brand colors, layout). |

### Utilities and maintenance

| File | Purpose |
|------|---------|
| `sanitize_local_fallback.py` | One-time utility that blanks direct identifiers (email, ZIP, DOB, occupation) from tracked CSV fallbacks without changing schema. |
| `.gitignore` | Excludes `data_cache/`, `record_flags.parquet`, `__pycache__/`, and `.ipynb_checkpoints/`. |

### Tests

| File | Purpose |
|------|---------|
| `tests/test_branching_logic_audit.py` | Tests for the R8 branching-logic audit rule. |
| `tests/test_tier1_rule_overlap.py` | Tests for the Tier 1 rule overlap tables. |
| `tests/test_bot_declaration_policy.py` | Tests for the bot declaration policy decision table and review queue CSV. |

### Archive

| File | Purpose |
|------|---------|
| `archive/2026-08-27-tier1-implementation/` | Historical prompts and proposals that led to the current tier system. Reference only. |

---

## Output guide

### Figures (in `Caregiver Outputs/`)

Each figure is saved as both 300-DPI PNG and vector PDF.

| Figure | Description |
|--------|-------------|
| `figure_1` | Cluster count diagnostics (elbow, silhouette, gap statistic) |
| `figure_2` | Primary cluster profiles (TFA domain means by cluster) |
| `figure_3` | Screening outcome by cluster |
| `figure_4` | Autism knowledge by cluster |
| `figure_5` | Values characterization by cluster |
| `figure_6` | Demographic characterization by cluster |
| `figure_7` | Timing ECDF (total survey completion time by project) |
| `figure_8` | Response fingerprint heatmap (ordered Likert patterns) |
| `figure_9` | Rule co-occurrence matrix |
| `figure_10` | Behavioral feature space (UMAP + t-SNE by project and tier) |
| `figure_11` | Detector scores and agreement |
| `figure_11b` | PU learning feature importance |
| `figure_12` | TFA clustermap with tier and source annotations |
| `figure_13` | TFA PCA biplot |
| `figure_14` | UMAP / t-SNE sensitivity panel |
| `figure_15` | Consensus matrix |
| `figure_16` | Case-level silhouette plot |
| `figure_17` | TFA domain distributions by cluster |
| `figure_18` | Logistic regression forest plot |
| `figure_19` | Tier-to-cluster alluvial flow |
| `figure_20` | Tier sensitivity panel |
| `figure_21` | Demographic signal enrichment (anomaly concentration) |
| `figure_22` | **NEW** — Tier 1 rule co-occurrence heatmap (meeting-slide ready) |

### Tables (in `Caregiver Outputs/`)

| Table | Description |
|-------|-------------|
| `table_1` | File inventory |
| `table_2` | Data quality summary |
| `table_3` | Cluster diagnostics (BIC, silhouette, gap) |
| `table_4` | Cluster-defining profiles |
| `table_5` / `5b` | Screening outcome / cohort flow |
| `table_6` / `6b` | Knowledge summary / item-level results |
| `table_7` | Values summary |
| `table_8` | Demographics summary |
| `table_9` | Decision summary (legacy) |
| `table_10` | Sensitivity checks |
| `table_11` | ASD family context |
| `table_12` | API cache inventory |
| `table_13` / `13b` | Field intersection / range mismatches |
| `table_14` | Fraud rule definitions (R1–R10) |
| `table_15` / `15b` | Rule false-positive rates / counts by project |
| `table_16` | Trust tier counts by project |
| `table_17` | Contamination identifiability (PU model) |
| `table_18` | Detector feature importance |
| `table_19` | Detector agreement (IF, LOF, PU) |
| `table_20` | Tier sensitivity across inclusion definitions |
| `table_21` | Characterization by definition |
| `table_22` | Firth logistic models |
| `table_23` | GMM diagnostics grid |
| `table_24` | GMM bootstrap LRT |
| `table_25` | Latent profile quality |
| `table_26` | BCH-corrected screening outcome |
| `table_27` | Status-quo regression checks |
| `table_28` | Excluded-case tipping point |
| `table_29` | Power and precision |
| `table_30` | Decision summary |
| `table_31` | Upgrade data quality |
| `table_32` | Color accessibility check |
| `table_33` | Figure map |
| `table_34` | Branching logic audit |
| `table_35` / `35b` | Demographic signal summary / enrichment |
| `table_36` / `36b` / `36c` | Tier 1 record overlap / combo summary / project summary |
| `table_37` | **NEW** — Bot declaration policy decision table |
| `table_38` | **NEW** — Dirty_4581 review queue (≥2 Tier 1 hits, for manual adjudication) |

---

## The trust screen: how bot detection works

### Rules (R1–R10)

The pipeline applies ten deterministic rules to every record. Each rule tests
one specific behavioral or logical signal:

- **R1**: Total survey time below the verified-human floor (11.57 min)
- **R2**: TFA section time below the verified-human floor (7.85 min)
- **R3**: Any instrument time below the clean 1st percentile
- **R4**: Within-block response SD at or below the clean 1st percentile (straightlining)
- **R5**: Exact ordered Likert fingerprint duplicate (≥80% answered)
- **R6**: ≥3 submissions within a clean-derived short burst window
- **R7**: Near-duplicate open text (TF-IDF cosine similarity ≥0.90)
- **R8**: Logical family-information inconsistency (autistic-child follow-ups,
  branching contradictions, impossible age-band counts)
- **R9**: Impossible demographic combination (age, ZIP, first-birth plausibility)
- **R10**: 4797-only instrument-native anti-fraud gate (validation signal, not tier-defining)

### Tiers (1–4)

Rules are combined into four trust tiers:

| Tier | Label | Rule logic |
|------|-------|------------|
| 1 | Confirmed invalid | Any of R1, R2, R5, R8, or R9 fired |
| 2 | High suspicion | ≥2 suspicion rules (R3, R4, R6, R7) fired |
| 3 | Uncertain | Exactly 1 suspicion rule fired |
| 4 | Pass | No rules fired |

### Bot declaration policy (table_37)

The decision table in `table_37_bot_declaration_policy.csv` translates tiers
and hit counts into operational actions:

| Scenario | Action | Gift card |
|----------|--------|-----------|
| Tier 1, ≥3 hits | Auto-exclude | No |
| Tier 1, 2 hits including R8 or R9 | Auto-exclude | No |
| Tier 1, 2 hits timing-only (R1+R2) | Review queue | Pending |
| Tier 1, 1 hit only | Conditional pass | Yes (unless override) |
| Tier 2 (≥2 suspicion rules) | Review queue | Pending |
| Tier 3 (1 suspicion rule) | Conditional pass | Yes |
| Tier 4 (clean) | Pass | Yes |

**Key principle**: We never auto-declare bot from a single rule hit alone.

---

## How to use the review queue

1. Open `Caregiver Outputs/table_38_dirty4581_review_queue.csv` in a spreadsheet.
2. The file contains only dirty_4581 records with ≥2 Tier 1 hits.
3. Records are sorted by severity: highest hit count and logic-rule presence first.
4. For each record, fill in:
   - **adjudication_decision**: `exclude`, `include`, or `flag_for_PI`
   - **adjudication_notes**: Brief rationale for the decision
   - **adjudicator**: Your name or initials
5. Records marked `auto_exclude` in the `policy_action` column can typically be
   confirmed without deep review. Focus manual effort on `review_queue` records.
6. Save the completed CSV for audit trail purposes.

---

## Privacy and data governance

- **No PII in outputs**: The pipeline blocks export of email, ZIP, DOB,
  occupation, and open text in any tracked CSV or figure.
- **Caches are git-ignored**: `data_cache/` and `record_flags.parquet` never
  enter version control.
- **Aggregate only**: All committed outputs contain only aggregate counts,
  percentages, and model parameters. Record IDs are included only in the
  git-ignored audit Parquet and the review queue CSV (which contains only
  REDCap record numbers, not names).

---

## Wording constraints

When writing about this analysis in manuscripts or presentations, use:

- ✅ "suspected contamination cluster"
- ✅ "anomaly concentration signal"
- ✅ "co-enrichment with independent trust-risk rules"

Do **not** use:

- ❌ "American Indian records are bots"
- ❌ "demographic field proves automation"
- ❌ "bot prevalence equals demographic prevalence"

---

## Data snapshot

- **Primary API/cache snapshot**: 2026-07-30
- **Clean CSV fallback date**: 2026-01-20
- **Clean project (4797)**: 177 records, 131 completed-human reference set
- **Dirty project (4581)**: 1,779 records (legacy, unverified recruitment)
- **Combined**: 1,956 records across both projects

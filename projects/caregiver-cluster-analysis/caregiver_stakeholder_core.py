"""Quiet, reproducible calculations for the caregiver stakeholder notebook.

This retains the validated six-domain, two-component Gaussian mixture as one
exploratory summary of overlapping attitudes. It does not select an optimal
number of parent types, establish causality, or validate future predictions.
Twenty posterior imputations and 100 complete-preprocessing bootstrap refits
carry uncertainty into the aggregate summaries. Screening intention, personal
values, demographics, doctor confidence, and procedure ratings are held out of
the grouping model.

``build_analysis(project_dir)`` reads the reconciled workbook and pinned offline
REDCap snapshot without changing them, printing, displaying, or writing files.
Returned respondent-indexed objects are private working data: stakeholder
notebooks should display/export aggregates only, suppress small denominators,
and never expose response keys or individual profile probabilities.
"""

from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.metrics import adjusted_rand_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


SEED = 20261022
IMPUTATIONS = 20
BOOTSTRAPS = 100
MIN_DOMAINS = 4
SNAPSHOT_ID = "20261021T192955575131Z"
EXPECTED_WORKBOOK_SHA = "961ca8e78f77fd4669d63bf6b3a199ae3d0465190597b9976b207d9439d72257"
PROFILE_NAMES = ["Profile 1", "Profile 2"]
STUDIES = {4797: "CAN Registry", 4581: "Global Online", 5749: "Prisma List", 4931: "ICIS"}
SCORE_SPECS = {
    "Positive affect (more positive)": ({"tfa_happy": False, "tfa_comfortable": False}, 2),
    "Emotional distress (less distress)": ({"tfa_scared": True, "tfa_sad": True}, 2),
    "Aversive feelings (less aversion)": ({"tfa_disgusted": True, "tfa_angry": True}, 2),
    "Visit feasibility (more feasible)": ({
        "tfa_difficult": False, "tfa_doable": True,
        "tfa_separate_difficult": False, "tfa_separate_doable": True,
        "tfa_series_difficult": False, "tfa_series_doable": True,
    }, 4),
    "Result usefulness (more useful)": ({"tfa_overall_help": True}, 1),
    "Ethical support (more support)": ({"tfa_scan_any": True, "tfa_scan_mine": True}, 2),
}
METHODS = {
    "tfa_video": "Video observation", "tfa_saliva": "Saliva sampling",
    "tfa_heart": "Heart monitoring", "tfa_observe": "Doctor observation",
    "tfa_blood": "Blood sampling", "tfa_mri": "MRI", "tfa_eeg": "EEG",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", str(value)))).strip()


def _source_helpers(project_dir):
    path = project_dir / "refined_sources.py"
    spec = importlib.util.spec_from_file_location("_caregiver_stakeholder_sources", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load the verified language harmonization helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _numeric(frame, field, low=None, high=None):
    result = pd.to_numeric(frame[field], errors="coerce").astype(float)
    return result.where(result.between(low, high)) if low is not None else result


def _load_cohort(project_dir, source_helpers):
    workbook = project_dir / "Responses Analysis_Revised (3).xlsx"
    workbook_sha = _digest(workbook)
    _require(workbook_sha == EXPECTED_WORKBOOK_SHA,
             "Workbook changed; reconcile its source snapshot in the technical notebook before updating the stakeholder report.")
    snapshot = project_dir / "Caregiver Outputs" / "restricted" / "cache" / SNAPSHOT_ID
    manifest = json.loads((snapshot / "manifest.json").read_text())
    _require(set(manifest["pids"]) == set(STUDIES), "Snapshot PIDs differ from study definitions.")
    for filename, details in manifest["files"].items():
        _require(Path(filename).name == filename, "Unsafe snapshot filename.")
        _require(_digest(snapshot / filename) == details["sha256"],
                 f"Changed snapshot input: {filename}; reconcile the pinned source before analysis.")

    columns = ["Project Key", "REDCap PID", "Record ID", "Final Verification",
               "Verification Check 1", "Study Name", "Survey Completed"]
    workbook_rows = pd.read_excel(workbook, sheet_name="Simplified All Responses",
                                  usecols=columns, dtype=str).fillna("")
    workbook_rows["response_key"] = workbook_rows["Project Key"].str.strip()
    expected_key = (pd.to_numeric(workbook_rows["REDCap PID"]).astype(int).astype(str) + "-" +
                    pd.to_numeric(workbook_rows["Record ID"]).astype(int).astype(str))
    _require(workbook_rows["response_key"].eq(expected_key).all(), "Workbook PID/record key mismatch.")
    _require(workbook_rows["response_key"].is_unique, "Duplicate workbook keys.")
    final_status = workbook_rows["Final Verification"].str.strip().str.casefold()
    _require(final_status.isin(["valid", "not valid"]).all(), "Unknown or blank final-verification decision.")
    verified_rows = workbook_rows.loc[final_status.eq("valid")].set_index("response_key")

    frames, metadata_by_pid = [], {}
    for pid, study in STUDIES.items():
        project = json.loads((snapshot / f"{pid}_project.json").read_text())
        _require(int(project["project_id"]) == pid, "Project identity mismatch.")
        records = pd.DataFrame(json.loads((snapshot / f"{pid}_record.json").read_text()))
        metadata = pd.DataFrame(json.loads((snapshot / f"{pid}_metadata.json").read_text()))
        folded, canonical_metadata, _, _ = source_helpers.fold_verified_twins(records, metadata, pid)
        folded["response_key"] = str(pid) + "-" + folded["record_id"].astype(str).str.strip()
        folded["study"], folded["pid"] = study, pid
        frames.append(folded)
        _require(canonical_metadata["field_name"].is_unique, f"Duplicate canonical metadata fields for PID {pid}.")
        metadata_by_pid[pid] = canonical_metadata.set_index("field_name", drop=False)
    all_raw = pd.concat(frames, ignore_index=True, sort=False).set_index("response_key")
    _require(all_raw.index.is_unique, "Duplicate source PID-record keys.")
    _require(len(verified_rows.index.difference(all_raw.index)) == 0,
             "Final-valid responses lack cached answers; stop and reconcile the source join.")
    raw = verified_rows[["Final Verification", "Verification Check 1"]].join(
        all_raw, how="left", validate="one_to_one")
    completion_fields = ["family_information_form_complete", "values_complete",
                         "tfa_complete", "demographics_complete"]
    _require(all(field in raw and pd.to_numeric(raw[field]).eq(2).all() for field in completion_fields),
             "Raw source completion differs from the final-verified cohort.")
    _require(len(raw) > 0 and raw.index.equals(verified_rows.index), "Final-valid cohort join changed row order or size.")
    raw["verification_route"] = np.where(raw["Verification Check 1"].str.strip().eq("PASS"),
                                         "Initially cleared", "Cleared after verification")
    return raw, metadata_by_pid, manifest, workbook_sha, workbook_rows, final_status


def _score_functions(metadata_by_pid, source_helpers):
    attitude_items = list(dict.fromkeys(q for questions, _ in SCORE_SPECS.values() for q in questions))
    item_ranges, guide_rows = {}, []
    for field in attitude_items + list(METHODS) + ["tfa_trusting"]:
        signatures = []
        for pid, metadata in metadata_by_pid.items():
            _require(field in metadata.index, f"{field} missing from PID {pid}.")
            entry = metadata.loc[field]
            branch = str(entry["branching_logic"]).strip()
            language_gate = pid == 5749 and branch.replace(" ", "") in [
                "[english_spanish]='2'", "[english_spanish]=2"]
            _require(not branch or language_gate,
                     f"Unexpected substantive branching for {field}; review before imputation.")
            choices = source_helpers.choice_map(entry)
            if entry["field_type"] == "slider":
                low, high = float(entry["text_validation_min"]), float(entry["text_validation_max"])
            else:
                allowed = sorted(float(code) for code in choices)
                low, high = min(allowed), max(allowed)
            signatures.append((entry["field_type"], low, high,
                               tuple((k, _plain(v).lower()) for k, v in sorted(choices.items()))))
        _require(len(set(signatures)) == 1, f"Incompatible coding/anchors for {field} across studies.")
        _require(high > low, f"Invalid score range for {field}.")
        item_ranges[field] = low, high
        domain = next((d for d, (questions, _) in SCORE_SPECS.items() if field in questions), "Held out")
        guide_rows.append({
            "Domain": domain, "Field": field, "Question wording": _plain(entry["field_label"]),
            "Raw anchors/options": _plain(entry["select_choices_or_calculations"]), "L": low, "U": high,
            "Reverse": SCORE_SPECS[domain][0][field] if domain != "Held out" else False,
            "Minimum items": SCORE_SPECS[domain][1] if domain != "Held out" else 1,
        })

    def scale_item(frame, field, reverse=False):
        low, high = item_ranges[field]
        values = _numeric(frame, field, low, high)
        entry = metadata_by_pid[next(iter(STUDIES))].loc[field]
        if entry["field_type"] != "slider":
            values = values.where(values.isin([float(code) for code in source_helpers.choice_map(entry)]))
        scaled = 100 * (values - low) / (high - low)
        return 100 - scaled if reverse else scaled

    def make_scores(frame):
        result = {}
        for domain, (items, required) in SCORE_SPECS.items():
            parts = pd.DataFrame({q: scale_item(frame, q, reverse) for q, reverse in items.items()})
            result[domain] = parts.mean(axis=1).where(parts.notna().sum(axis=1).ge(required))
        return pd.DataFrame(result, index=frame.index)

    return scale_item, make_scores, pd.DataFrame(guide_rows)


def _covariates(raw, scale_item):
    result = pd.DataFrame(index=raw.index)
    income = _numeric(raw, "demo_income", 1, 7)
    result["income_band"] = np.select(
        [income.isin([1, 2]), income.eq(3), income.isin([4, 5, 6])],
        ["<=$60k", "$60–100k", ">$100k"], default="Unknown")
    education = _numeric(raw, "demo_cg1education", 1, 9)
    result["education"] = np.select([education.between(1, 6), education.between(7, 9)],
                                     ["No bachelor's", "Bachelor's+"], default="Unknown")
    race = pd.DataFrame({i: _numeric(raw, f"demo_maternalrace___{i}", 0, 1) for i in range(1, 8)})
    ethnicity = pd.DataFrame({i: _numeric(raw, f"demo_maternalethnicity___{i}", 0, 1) for i in range(1, 5)})
    race_known = race[[1, 2, 3, 4, 5, 7]].sum(axis=1)
    non_hispanic = ethnicity[2].eq(1) & ethnicity[[1, 3, 4]].sum(axis=1).eq(0)
    hispanic = ethnicity[1].eq(1) & ethnicity[[2, 3, 4]].sum(axis=1).eq(0)
    white = race[5].eq(1) & race_known.eq(1) & race[6].eq(0)
    black = race[4].eq(1) & race_known.eq(1) & race[6].eq(0)
    result["race_ethnicity"] = np.select(
        [hispanic, non_hispanic & white, non_hispanic & black,
         non_hispanic & race_known.gt(0) & race[6].eq(0)],
        ["Hispanic", "White non-Hispanic", "Black non-Hispanic", "Multiracial or another race"],
        default="Unknown")
    result["study"] = raw.study
    child_ages = pd.DataFrame({i: _numeric(raw, f"fif_childrens_ages___{i}", 0, 1) for i in range(1, 8)})
    result["infant_context"] = child_ages[[1, 2]].eq(1).any(axis=1).astype(float).where(child_ages.eq(1).any(axis=1))
    result["current_context"] = result["infant_context"].map(
        {1.: "Child <=12 months", 0.: "Only older children"}).fillna("Age not reported")
    autistic_child = _numeric(raw, "fif_num_autistic", 0, 4)
    result["autistic_child"] = autistic_child.gt(0).astype(float).where(autistic_child.notna())
    result["trust"] = scale_item(raw, "tfa_trusting")
    result["intention"] = _numeric(raw, "tfa_free_screen", 0, 4)
    names = ["conformity", "tradition", "benevolence", "universalism", "self_direction",
             "stimulation", "hedonism", "achievement", "power", "security"]
    values = pd.DataFrame({name: _numeric(raw, f"q{i}", 1, 6) for i, name in enumerate(names, 1)})
    centered = values.sub(values.mean(axis=1), axis=0).where(values.notna().all(axis=1), axis=0)
    for name in names:
        result[f"value_{name}"] = centered[name]
    return result


def _fit_pipeline(scores, seed):
    imputer = IterativeImputer(sample_posterior=True, max_iter=15, min_value=0, max_value=100,
                               initial_strategy="median", random_state=int(seed), skip_complete=True)
    completed = imputer.fit_transform(scores)
    scaler = StandardScaler().fit(completed)
    z = scaler.transform(completed)
    model = GaussianMixture(n_components=2, covariance_type="tied", n_init=5,
                            max_iter=400, reg_covar=1e-4, random_state=int(seed)).fit(z)
    if not model.converged_:
        raise RuntimeError("Working GMM did not converge; inspect the technical notebook before interpretation.")
    return imputer, scaler, model, completed, scaler.inverse_transform(model.means_)


def _align_order(centers, reference):
    rows, columns = linear_sum_assignment(cdist(centers, reference))
    return rows[np.argsort(columns)]


def _weighted_mean(values, weights):
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    return np.sum(values[valid] * weights[valid]) / weights[valid].sum() if valid.any() else np.nan


def _probability_summary(values, posterior, bootstrap_posteriors, bootstrap_indices):
    values = values.reindex(posterior.index)
    rows = []
    for measure in values.columns:
        observed = values[measure].to_numpy(dtype=float)
        for group, name in enumerate(PROFILE_NAMES):
            estimates = [_weighted_mean(observed[ix], p[ix, group])
                         for p, ix in zip(bootstrap_posteriors, bootstrap_indices)]
            lower, upper = np.nanpercentile(estimates, [2.5, 97.5])
            rows.append({
                "Measure": measure, "Profile": name, "Observed n": int(np.isfinite(observed).sum()),
                "Probability-weighted observed n": float(posterior.loc[np.isfinite(observed), name].sum()),
                "Mean": _weighted_mean(observed, posterior[name]), "Lower 95%": lower, "Upper 95%": upper,
            })
    return pd.DataFrame(rows)


def _verified_adjusted_tables(project_dir, posterior, workbook_sha, manifest):
    """Reuse aggregate adjusted models only after proving the grouping is identical."""
    candidates = sorted((project_dir / "Caregiver Outputs").glob("cluster_refinement_*/provenance.json"), reverse=True)
    compatible = []
    for provenance_path in candidates:
        prior = json.loads(provenance_path.read_text())
        if (prior.get("workbook_sha256") == workbook_sha and prior.get("seed") == SEED
                and prior.get("primary_features") == list(SCORE_SPECS)
                and prior.get("source_snapshot") == SNAPSHOT_ID
                and prior.get("source_files") == manifest["files"]
                and prior.get("imputations") == IMPUTATIONS
                and prior.get("comparison_k") == 2 and prior.get("comparison_covariance") == "tied"):
            compatible.append(provenance_path)
    error = ("Adjusted aggregate results cannot be verified against this exact analysis. "
             "Re-execute the preserved technical notebook with the reconciled input to refresh "
             "its provenance, adjusted associations, and restricted profile probabilities.")
    for provenance_path in compatible:
        directory = provenance_path.parent
        required = [directory / "adjusted_associations.csv", directory / "adjusted_model_audit.csv",
                    directory / "restricted" / "respondent_profile_probabilities.csv"]
        if not all(path.is_file() for path in required):
            continue
        prior_p = pd.read_csv(required[2], index_col=0)
        _require(prior_p.index.is_unique and set(PROFILE_NAMES).issubset(prior_p), error)
        prior_eligible = prior_p.loc[prior_p[PROFILE_NAMES].notna().all(axis=1), PROFILE_NAMES]
        _require(set(prior_eligible.index) == set(posterior.index), error)
        _require(np.allclose(prior_eligible.reindex(posterior.index).to_numpy(), posterior.to_numpy(),
                             rtol=0, atol=1e-8, equal_nan=False), error)
        associations, audit = pd.read_csv(required[0]), pd.read_csv(required[1])
        _require({"Family", "Contrast", "n", "Difference pp", "Refit 95% low", "Refit 95% high"}.issubset(associations), error)
        _require({"Model", "Adjusted n", "Eligible profiles"}.issubset(audit), error)
        _require(audit["Eligible profiles"].eq(len(posterior)).all(), error)
        _require(associations["n"].between(1, len(posterior)).all(), error)
        return associations, audit, directory
    raise ValueError(error)


def build_analysis(project_dir) -> dict:
    """Rebuild the validated exploratory grouping and return private data/aggregate tables.

    No model search or technical notebook outputs are generated. Two profiles
    summarize a continuum of attitudes; neither hard labels nor profile shares
    establish natural parent types. All summary means use observed responses
    with probability weights; bootstrap bands repeat imputation, scaling,
    grouping, and profile matching. Existing adjusted association aggregates are
    accepted only if their provenance and respondent-level probabilities match
    this freshly computed model to an absolute tolerance of 1e-8.
    """
    project_dir = Path(project_dir).expanduser().resolve()
    helpers = _source_helpers(project_dir)
    raw, metadata, manifest, workbook_sha, workbook_rows, final_status = _load_cohort(project_dir, helpers)
    scale_item, make_scores, question_guide = _score_functions(metadata, helpers)
    scores = make_scores(raw)
    _require((scores.isna() | scores.ge(0) & scores.le(100)).all().all(), "Composite scores are outside 0–100.")
    eligible = scores.notna().sum(axis=1).ge(MIN_DOMAINS)
    model_scores = scores.loc[eligible].copy()
    _require(len(model_scores) > 2 and model_scores.notna().any().all(), "Insufficient observed domains for the grouping model.")
    covariates = _covariates(raw, scale_item)

    posterior_draws, bootstrap_posteriors, bootstrap_indices = [], [], []
    bootstrap_rows, bootstrap_failures = [], []
    # Limit small matrix BLAS work without changing global thread settings after this call.
    with threadpool_limits(limits=1):
        for imputation in range(IMPUTATIONS):
            imputer, scaler, gmm, completed, centers = _fit_pipeline(model_scores, SEED + imputation)
            if imputation == 0:
                order = np.argsort(centers.mean(axis=1))
                reference_centers = centers[order]
            else:
                order = _align_order(centers, reference_centers)
            posterior_draws.append(gmm.predict_proba(scaler.transform(completed))[:, order])
        posterior = pd.DataFrame(np.mean(posterior_draws, axis=0), index=model_scores.index, columns=PROFILE_NAMES)
        modal = posterior.idxmax(axis=1)
        pooled_labels = posterior.to_numpy().argmax(axis=1)
        rng = np.random.default_rng(SEED)
        for run in range(BOOTSTRAPS):
            ix = rng.integers(0, len(model_scores), len(model_scores))
            try:
                imputer, scaler, gmm, _, centers = _fit_pipeline(model_scores.iloc[ix], SEED + 1000 + run)
                order = _align_order(centers, reference_centers)
                predicted = gmm.predict_proba(scaler.transform(imputer.transform(model_scores)))[:, order]
                _require(np.isfinite(predicted).all() and np.allclose(predicted.sum(axis=1), 1),
                         "A bootstrap fit returned invalid profile probabilities.")
                bootstrap_posteriors.append(predicted)
                bootstrap_indices.append(ix)
                bootstrap_rows.append({
                    "Run": run + 1, "ARI": adjusted_rand_score(modal, predicted.argmax(axis=1)),
                    "Label agreement": np.mean(predicted.argmax(axis=1) == pooled_labels),
                    "Profile 2 share %": 100 * predicted[ix, 1].mean(),
                })
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
                bootstrap_failures.append({"Run": run + 1, "Failure": type(exc).__name__})
    _require(len(bootstrap_posteriors) >= .9 * BOOTSTRAPS, "Too many failed full-preprocessing bootstrap fits.")
    _require(posterior.index.is_unique and np.isfinite(posterior.to_numpy()).all()
             and np.allclose(posterior.sum(axis=1), 1), "Invalid or misaligned pooled profile probabilities.")
    bootstrap_diagnostics = pd.DataFrame(bootstrap_rows)
    imputation_diagnostics = pd.DataFrame([{
        "Imputation": i + 1, "ARI vs pooled": adjusted_rand_score(modal, p.argmax(axis=1)),
        "Profile 2 share %": 100 * p[:, 1].mean(), "Mean max probability": p.max(axis=1).mean(),
    } for i, p in enumerate(posterior_draws)])

    profile_table = _probability_summary(scores, posterior, bootstrap_posteriors, bootstrap_indices)
    bootstrap_shares = [p[ix].mean(axis=0) * 100 for p, ix in zip(bootstrap_posteriors, bootstrap_indices)]
    share_table = pd.DataFrame({
        "Profile": PROFILE_NAMES, "Modal n": modal.value_counts().reindex(PROFILE_NAMES, fill_value=0).values,
        "Probability-weighted n": posterior.sum().values, "Share %": posterior.mean().values * 100,
        "Lower 95%": np.percentile(bootstrap_shares, 2.5, axis=0),
        "Upper 95%": np.percentile(bootstrap_shares, 97.5, axis=0),
    })
    intention = covariates.intention
    intervention = _numeric(raw, "tfa_intervene", 1, 4)
    held_out = pd.DataFrame({
        "Definitely take free screening (%)": intention.eq(4).astype(float).where(intention.notna()) * 100,
        "Probably/definitely take screening (%)": intention.ge(3).astype(float).where(intention.notna()) * 100,
        "Early support very important (%)": intervention.eq(4).astype(float).where(intervention.notna()) * 100,
        "Doctor confidence (0–100)": covariates.trust,
    })
    heldout_table = _probability_summary(held_out, posterior, bootstrap_posteriors, bootstrap_indices)
    method_values = pd.DataFrame({label: scale_item(raw, field) for field, label in METHODS.items()})
    method_table = _probability_summary(method_values, posterior, bootstrap_posteriors, bootstrap_indices)
    adjusted_associations, adjusted_model_audit, adjusted_source = _verified_adjusted_tables(
        project_dir, posterior, workbook_sha, manifest)
    versions = {package: importlib.metadata.version(package) for package in
                ["pandas", "numpy", "scikit-learn", "scipy", "statsmodels", "openpyxl", "nbformat"]}
    provenance = {
        "seed": SEED, "imputations": IMPUTATIONS, "bootstrap_requested": BOOTSTRAPS,
        "bootstrap_successful": len(bootstrap_posteriors), "bootstrap_failures": bootstrap_failures,
        "workbook_sha256": workbook_sha, "worksheet": "Simplified All Responses",
        "source_snapshot": SNAPSHOT_ID, "source_files": manifest["files"],
        "source_extracted_at_utc": manifest["fetched_at_utc"],
        "primary_features": list(SCORE_SPECS), "comparison_k": 2, "comparison_covariance": "tied",
        "cohort_n": len(raw), "classifiable_n": len(model_scores),
        "workbook_responses_n": len(workbook_rows), "final_not_valid_n": int(final_status.eq("not valid").sum()),
        "unit": "PID-record response, not proven unique individual", "future_data_validated": False,
        "adjusted_aggregate_source": str(adjusted_source), "adjusted_posterior_tolerance": 1e-8,
        "language_helper_sha256": _digest(project_dir / "archive" / "2026-10-12-legacy-workflow" / "refined_sources.py"),
        "versions": versions,
    }
    _require(np.isclose(share_table["Share %"].sum(), 100), "Profile shares do not sum to 100%.")
    _require(np.isclose(share_table["Probability-weighted n"].sum(), len(model_scores)), "Profile weights changed the cohort size.")
    for table in [profile_table, heldout_table, method_table]:
        _require(table["Observed n"].between(0, len(model_scores)).all(), "A profile table has an invalid observed denominator.")
        _require(table["Lower 95%"].le(table["Upper 95%"]).all(), "A bootstrap interval is reversed or missing.")
    return {
        "raw": raw, "covariates": covariates, "scores": scores, "model_scores": model_scores,
        "posterior": posterior, "profile_table": profile_table, "heldout_table": heldout_table,
        "method_table": method_table, "share_table": share_table,
        "bootstrap_diagnostics": bootstrap_diagnostics, "imputation_diagnostics": imputation_diagnostics,
        "bootstrap_posteriors": bootstrap_posteriors, "bootstrap_indices": bootstrap_indices,
        "question_guide": question_guide, "manifest": manifest, "workbook_sha": workbook_sha,
        "versions": versions, "provenance": provenance, "profile_names": PROFILE_NAMES.copy(),
        "adjusted_associations": adjusted_associations, "adjusted_model_audit": adjusted_model_audit,
    }

"""Stakeholder-facing inferential extensions for the 664-caregiver cluster analysis.

The module keeps discovery variables (ten 0-100 attitude scores) separate from
held-out demographics and screening intention. It adds:

* posterior multiple imputation with observed values preserved;
* bounded community-source sensitivity weights (not population weights);
* Gaussian-mixture mixed membership across imputations;
* silhouette, entropy, ARI, and weighted/unweighted bootstrap diagnostics;
* SES/race assignment and classification-confidence fairness audits; and
* aggregate, non-stereotyping stakeholder profiles and composite vignettes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

HIGHER = "Higher acceptability"
CONDITIONAL = "Conditional acceptability"
GROUP_ORDER = [HIGHER, CONDITIONAL]
ACCURACY_SCORE = "OK with a less accurate test"


def bh_adjust(p_values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    result = np.full(len(values), np.nan)
    valid = np.flatnonzero(np.isfinite(values))
    if not len(valid):
        return result
    order = valid[np.argsort(values[valid])]
    ranked = values[order] * len(valid) / np.arange(1, len(valid) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    result[order] = np.minimum(ranked, 1.0)
    return result


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce").reindex(x.index)
    keep = x.notna() & w.notna() & w.gt(0)
    return float(np.average(x[keep], weights=w[keep])) if keep.any() else np.nan


def effective_n(weights: pd.Series) -> float:
    w = pd.to_numeric(weights, errors="coerce").dropna()
    w = w[w.gt(0)]
    return float(w.sum() ** 2 / np.square(w).sum()) if len(w) else np.nan


def community_source_weights(
    study: pd.Series,
    method: str = "sqrt_balance",
    trim_quantiles: tuple[float, float] = (0.02, 0.98),
) -> tuple[pd.Series, pd.DataFrame]:
    """Return bounded source-balance sensitivity weights.

    ``sqrt_balance`` gives each recruitment source a target share proportional
    to sqrt(n), between equal-source weighting and the unweighted sample. These
    are sensitivity weights, not census-calibrated population weights.
    """
    source = study.astype("string")
    counts = source.value_counts(dropna=False)
    observed = counts / counts.sum()
    if method == "equal_source":
        target = pd.Series(1 / len(counts), index=counts.index)
    elif method == "sqrt_balance":
        target = np.sqrt(counts) / np.sqrt(counts).sum()
    elif method == "unweighted":
        target = observed.copy()
    else:
        raise ValueError("method must be sqrt_balance, equal_source, or unweighted")
    factor = target / observed
    weights = source.map(factor).astype(float)
    low, high = weights.quantile(list(trim_quantiles))
    weights = weights.clip(lower=low, upper=high)
    weights = weights / weights.mean()
    audit = pd.DataFrame(
        {
            "records": counts,
            "observed_share_pct": observed * 100,
            "target_share_pct_before_trimming": target * 100,
            "raw_weight": factor,
            "final_mean_weight": weights.groupby(source).mean(),
        }
    ).rename_axis("recruitment_source").reset_index()
    audit["weighting_method"] = method
    audit["effective_n"] = effective_n(weights)
    return weights.rename("community_weight"), audit


def multiple_impute_scores(
    scores: pd.DataFrame,
    m: int = 20,
    seed: int = 20260725,
    minimum_observed: int = 4,
) -> tuple[list[pd.DataFrame], pd.DataFrame]:
    """Create bounded posterior imputations while preserving every observed value."""
    numeric = scores.apply(pd.to_numeric, errors="coerce")
    eligible = numeric.notna().sum(axis=1).ge(minimum_observed)
    model = numeric.loc[eligible]
    if model.empty:
        raise ValueError("No records meet the imputation eligibility rule")
    imputations: list[pd.DataFrame] = []
    for draw in range(m):
        imputer = IterativeImputer(
            estimator=None,
            sample_posterior=True,
            max_iter=30,
            min_value=0,
            max_value=100,
            initial_strategy="median",
            random_state=seed + draw,
            skip_complete=True,
        )
        completed = pd.DataFrame(
            imputer.fit_transform(model), index=model.index, columns=model.columns
        ).clip(0, 100)
        completed = completed.mask(model.notna(), model)
        imputations.append(completed)
    missing = numeric.isna().sum()
    audit = pd.DataFrame(
        {
            "score": numeric.columns,
            "observed_n": numeric.notna().sum().to_numpy(),
            "missing_n": missing.to_numpy(),
            "missing_pct": (missing / len(numeric) * 100).to_numpy(),
        }
    )
    audit["records_total"] = len(numeric)
    audit["records_imputed"] = int(eligible.sum())
    audit["imputations"] = m
    audit["minimum_observed_scores"] = minimum_observed
    return imputations, audit


def _component_order(model: GaussianMixture, scaler: StandardScaler, columns: list[str]) -> tuple[int, int]:
    centers = pd.DataFrame(scaler.inverse_transform(model.means_), columns=columns)
    acceptance = centers.drop(columns=[ACCURACY_SCORE], errors="ignore").mean(axis=1)
    higher = int(acceptance.idxmax())
    conditional = int(acceptance.drop(index=higher).idxmax())
    return higher, conditional


def _entropy_separation(posterior: np.ndarray) -> float:
    k = posterior.shape[1]
    if k <= 1:
        return 1.0
    entropy = -np.sum(posterior * np.log(np.clip(posterior, 1e-12, 1)))
    return float(1 - entropy / (len(posterior) * np.log(k)))


@dataclass
class MixedMembershipResult:
    membership: pd.DataFrame
    model_grid: pd.DataFrame
    imputation_diagnostics: pd.DataFrame
    bootstrap_diagnostics: pd.DataFrame
    profile: pd.DataFrame
    completed_scores: pd.DataFrame


def fit_mixed_membership(
    imputations: list[pd.DataFrame],
    seed: int = 20260725,
    selected_k: int = 2,
    covariance_type: str = "diag",
    k_range: Iterable[int] = range(2, 7),
    bootstrap_repeats: int = 200,
    weights: pd.Series | None = None,
) -> MixedMembershipResult:
    """Pool GMM posteriors over imputations and diagnose weighted sensitivity."""
    if selected_k != 2:
        raise ValueError("Stakeholder names currently require selected_k=2")
    columns = list(imputations[0].columns)
    index = imputations[0].index
    completed = sum(frame for frame in imputations) / len(imputations)
    grid_rows, diag_rows, posterior_draws, modal_draws = [], [], [], []
    for draw, frame in enumerate(imputations):
        scaler = StandardScaler().fit(frame)
        z = scaler.transform(frame)
        for k in k_range:
            g = GaussianMixture(
                n_components=k,
                covariance_type=covariance_type,
                n_init=30,
                max_iter=1000,
                reg_covar=1e-6,
                random_state=seed + draw * 100 + k,
            ).fit(z)
            labels = g.predict(z)
            p = g.predict_proba(z)
            grid_rows.append(
                {
                    "imputation": draw + 1,
                    "k": k,
                    "covariance_type": covariance_type,
                    "bic": g.bic(z),
                    "aic": g.aic(z),
                    "silhouette": silhouette_score(z, labels) if len(np.unique(labels)) > 1 else np.nan,
                    "entropy_separation": _entropy_separation(p),
                    "mean_max_posterior": p.max(axis=1).mean(),
                    "converged": bool(g.converged_),
                }
            )
        model = GaussianMixture(
            n_components=2,
            covariance_type=covariance_type,
            n_init=50,
            max_iter=1000,
            reg_covar=1e-6,
            random_state=seed + draw,
        ).fit(z)
        higher, conditional = _component_order(model, scaler, columns)
        p = model.predict_proba(z)
        ph = p[:, higher]
        modal = (ph >= 0.5).astype(int)
        posterior_draws.append(ph)
        modal_draws.append(modal)
        diag_rows.append(
            {
                "imputation": draw + 1,
                "silhouette_k2": silhouette_score(z, modal),
                "entropy_separation": _entropy_separation(p),
                "mean_max_posterior": np.maximum(ph, 1 - ph).mean(),
                "higher_share_pct": modal.mean() * 100,
            }
        )
    posterior_matrix = np.column_stack(posterior_draws)
    modal_matrix = np.column_stack(modal_draws)
    mean_posterior = posterior_matrix.mean(axis=1)
    membership = pd.DataFrame(
        {
            "posterior_higher": mean_posterior,
            "posterior_conditional": 1 - mean_posterior,
            "membership_sd_across_imputations": posterior_matrix.std(axis=1, ddof=1),
            "max_posterior": np.maximum(mean_posterior, 1 - mean_posterior),
            "modal_profile": np.where(mean_posterior >= 0.5, HIGHER, CONDITIONAL),
            "modal_agreement_across_imputations": np.maximum(modal_matrix.mean(axis=1), 1 - modal_matrix.mean(axis=1)),
        },
        index=index,
    )
    profile = pd.concat(
        [
            completed.loc[membership["modal_profile"].eq(group)].mean().rename(group)
            for group in GROUP_ORDER
        ],
        axis=1,
    )
    profile["Gap"] = profile[HIGHER] - profile[CONDITIONAL]

    scaler = StandardScaler().fit(completed)
    z = scaler.transform(completed)
    reference = GaussianMixture(
        n_components=2,
        covariance_type=covariance_type,
        n_init=100,
        max_iter=1000,
        reg_covar=1e-6,
        random_state=seed,
    ).fit(z)
    ref_higher, _ = _component_order(reference, scaler, columns)
    ref_modal = reference.predict_proba(z)[:, ref_higher] >= 0.5
    rng = np.random.default_rng(seed)
    base_probability = np.repeat(1 / len(index), len(index))
    weighted_probability = base_probability if weights is None else weights.reindex(index).to_numpy(float) / weights.reindex(index).sum()
    boot_rows = []
    for scheme, probability in [("unweighted", base_probability), ("community-weighted", weighted_probability)]:
        for repeat in range(bootstrap_repeats):
            chosen = rng.choice(len(index), size=len(index), replace=True, p=probability)
            model = GaussianMixture(
                n_components=2,
                covariance_type=covariance_type,
                n_init=15,
                max_iter=500,
                reg_covar=1e-6,
                random_state=seed + repeat + (10000 if scheme == "community-weighted" else 0),
            ).fit(z[chosen])
            h, _ = _component_order(model, scaler, columns)
            predicted = model.predict_proba(z)[:, h] >= 0.5
            boot_rows.append(
                {
                    "scheme": scheme,
                    "repeat": repeat + 1,
                    "ari_vs_reference": adjusted_rand_score(ref_modal, predicted),
                    "agreement_vs_reference": max((predicted == ref_modal).mean(), (predicted != ref_modal).mean()),
                    "silhouette": silhouette_score(z, predicted) if len(np.unique(predicted)) > 1 else np.nan,
                    "higher_share_pct": predicted.mean() * 100,
                }
            )
    model_grid = pd.DataFrame(grid_rows)
    imputation_diagnostics = pd.DataFrame(diag_rows)
    imputation_diagnostics["ari_vs_imputation_1"] = [
        adjusted_rand_score(modal_matrix[:, 0], modal_matrix[:, j])
        for j in range(modal_matrix.shape[1])
    ]
    return MixedMembershipResult(
        membership=membership,
        model_grid=model_grid,
        imputation_diagnostics=imputation_diagnostics,
        bootstrap_diagnostics=pd.DataFrame(boot_rows),
        profile=profile,
        completed_scores=completed,
    )


def demographic_strata(frame: pd.DataFrame) -> pd.DataFrame:
    """Create stakeholder-readable SES and race/ethnicity strata from REDCap codes."""
    result = pd.DataFrame(index=frame.index)
    income = pd.to_numeric(frame.get("demo_income"), errors="coerce")
    result["Income"] = pd.cut(
        income,
        bins=[0, 2, 3, 6],
        labels=["$60k or less", "$60k-$100k", "Over $100k"],
    ).astype("string").fillna("Missing/prefer not")
    education = pd.to_numeric(frame.get("demo_cg1education"), errors="coerce")
    result["Education"] = pd.Series(
        np.select(
            [education.le(6), education.ge(7)],
            ["Below bachelor's", "Bachelor's or higher"],
            default="Missing",
        ),
        index=frame.index,
        dtype="string",
    )
    ethnicity = pd.to_numeric(frame.get("demo_maternalethnicity___1"), errors="coerce").eq(1)
    races = pd.DataFrame(
        {
            i: pd.to_numeric(frame.get(f"demo_maternalrace___{i}"), errors="coerce").eq(1)
            for i in range(1, 8)
        },
        index=frame.index,
    )
    race_count = races.sum(axis=1)
    result["Race/ethnicity"] = pd.Series(
        np.select(
            [
                ethnicity,
                races[4] & race_count.eq(1),
                races[5] & race_count.eq(1),
                races[2] & race_count.eq(1),
                race_count.gt(1),
                race_count.eq(0),
            ],
            [
                "Hispanic/Latino (any race)",
                "Non-Hispanic Black",
                "Non-Hispanic White",
                "Non-Hispanic Asian",
                "Multiracial",
                "Unknown/not reported",
            ],
            default="Another race/ethnicity",
        ),
        index=frame.index,
        dtype="string",
    )
    return result


def fairness_audit(
    membership: pd.DataFrame,
    demographics: pd.DataFrame,
    weights: pd.Series,
    completed_scores: pd.DataFrame,
    minimum_n: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Test assignment share, uncertainty, and within-stratum separation parity."""
    data = membership.join(demographics).join(weights).join(completed_scores.add_prefix("score__"))
    detail_rows, test_rows = [], []
    for attribute in demographics.columns:
        eligible_levels = [
            level for level, n in data[attribute].value_counts().items() if n >= minimum_n
        ]
        subset = data[data[attribute].isin(eligible_levels)].copy()
        if len(eligible_levels) < 2:
            continue
        contingency = pd.crosstab(subset[attribute], subset["modal_profile"])
        chi2_p = stats.chi2_contingency(contingency)[1] if contingency.shape[1] == 2 else np.nan
        confidence_groups = [
            subset.loc[subset[attribute].eq(level), "max_posterior"].dropna()
            for level in eligible_levels
        ]
        confidence_p = stats.kruskal(*confidence_groups).pvalue
        shares = []
        confs = []
        for level in eligible_levels:
            part = subset[subset[attribute].eq(level)]
            w = part[weights.name]
            higher = part["modal_profile"].eq(HIGHER).astype(float)
            confidence = part["max_posterior"]
            uncertain = confidence.lt(0.70).astype(float)
            labels = higher.astype(int)
            z = part.filter(like="score__").to_numpy(float)
            silhouette = silhouette_score(z, labels) if labels.nunique() == 2 and len(part) >= minimum_n else np.nan
            share = weighted_mean(higher, w)
            conf = weighted_mean(confidence, w)
            shares.append(share)
            confs.append(conf)
            detail_rows.append(
                {
                    "attribute": attribute,
                    "stratum": level,
                    "n": len(part),
                    "effective_weighted_n": effective_n(w),
                    "weighted_higher_share_pct": share * 100,
                    "weighted_mean_max_posterior": conf,
                    "weighted_uncertain_below_0.70_pct": weighted_mean(uncertain, w) * 100,
                    "within_stratum_silhouette": silhouette,
                }
            )
        test_rows.extend(
            [
                {
                    "attribute": attribute,
                    "test": "Modal profile distribution (chi-square)",
                    "raw_p": chi2_p,
                    "max_gap": (max(shares) - min(shares)) * 100,
                    "gap_unit": "percentage points",
                },
                {
                    "attribute": attribute,
                    "test": "Classification confidence (Kruskal-Wallis)",
                    "raw_p": confidence_p,
                    "max_gap": max(confs) - min(confs),
                    "gap_unit": "posterior probability",
                },
            ]
        )
    tests = pd.DataFrame(test_rows)
    if len(tests):
        tests["fdr_p"] = bh_adjust(tests["raw_p"])
        tests["fairness_flag"] = np.where(
            tests["fdr_p"].lt(0.05), "Review disparity", "No FDR-significant disparity"
        )
    return pd.DataFrame(detail_rows), tests


def stakeholder_profiles(
    result: MixedMembershipResult,
    screen_intent: pd.Series | None = None,
    weights: pd.Series | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build aggregate profiles and composite vignettes without demographic stereotyping."""
    membership = result.membership
    weights = pd.Series(1.0, index=membership.index, name="weight") if weights is None else weights.reindex(membership.index)
    rows, vignettes = [], []
    for group in GROUP_ORDER:
        idx = membership.index[membership["modal_profile"].eq(group)]
        group_mean = result.completed_scores.loc[idx].apply(lambda x: weighted_mean(x, weights.loc[idx]))
        supportive = group_mean.drop(labels=[ACCURACY_SCORE], errors="ignore").sort_values(ascending=False)
        cautious = group_mean.sort_values(ascending=True)
        strengths = supportive.head(3).index.tolist()
        concerns = cautious.head(3).index.tolist()
        n = len(idx)
        row = {
            "profile": group,
            "n": n,
            "share_pct": n / len(membership) * 100,
            "mean_max_posterior": weighted_mean(membership.loc[idx, "max_posterior"], weights.loc[idx]),
            "top_supportive_domains": "; ".join(strengths),
            "top_caution_domains": "; ".join(concerns),
        }
        if screen_intent is not None:
            row["weighted_definitely_screen_pct"] = weighted_mean(screen_intent.loc[idx], weights.loc[idx]) * 100
        rows.append(row)
        supportive = ", ".join(x.lower() for x in strengths[:2])
        cautious = ", ".join(x.lower() for x in concerns[:2])
        if group == HIGHER:
            text = (
                "Composite vignette—not a real caregiver: This parent sees early screening as useful and acceptable, "
                f"with especially strong {supportive}. They may still want clear information where {cautious} is less strong. "
                "A concise offer with transparent next steps is likely to fit this profile."
            )
        else:
            text = (
                "Composite vignette—not a real caregiver: This parent is not simply opposed; support is conditional. "
                f"They show relative strengths in {supportive}, while {cautious} are the clearest friction points. "
                "A low-pressure conversation, explicit uncertainty, burden options, and time for questions are likely to fit this profile."
            )
        vignettes.append({"profile": group, "vignette": text})
    return pd.DataFrame(rows), pd.DataFrame(vignettes)

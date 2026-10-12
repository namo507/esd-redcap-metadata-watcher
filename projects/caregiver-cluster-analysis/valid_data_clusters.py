"""Valid_Data and the caregiver attitude groups for notebook section D.

Valid_Data is every response the bot screen cleared for payment ("Pay Now"),
plus every flagged or excluded response whose owner then passed the Caregiver
Respondent Verification Survey (PID 6277) -- birth year, ZIP code and
education all matching the original survey -- as long as that original survey
was finished. The four studies and the verification survey are pulled live
from REDCap on every run.

The attitude scores are the project's ten locked TFA domains, with each
question's answer range read from the live REDCap metadata instead of typed
in. Two results therefore differ from ``build_cluster_domains``:

* ``tfa_scan_any`` has six answer options in every study, not five, so its
  score can no longer fall below zero.
* "I would not do this kind of test at all" is not a level of accuracy, so it
  is left blank for the accuracy score instead of scoring 125.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from dotenv import dotenv_values
from matplotlib import font_manager
from sklearn.cluster import KMeans

import build_simplified_responses as simplified
import refined_sources as sources
from refined_screening import COMPLETION_FIELDS


# ── Verification survey (PID 6277) ──────────────────────────────────────────

VERIFICATION_PID = 6277
VERIFICATION_TITLE = "Caregiver - Respondent Verification Survey"
VERIFICATION_TOKEN_ENVS = ("TOKEN_VERIFICATION_6277", "TOKEN_CAREGIVER_6277", "TOKEN_6277")
VERIFY_FIELDS = ("verify_birth_year", "verify_zip", "verify_education")
# The same wording as REDCap's own verification_result field.
VERIFIED_LABEL = "All three answers match"
MISMATCH_LABEL = "Needs review: response mismatch"
MISSING_ORIGINAL_LABEL = "Needs review: original answer missing"
AWAITING_LABEL = "Awaiting all three answers"

ROUTE_ORDER = [
    "Valid: cleared by the bot screen (Pay Now)",
    "Valid: confirmed by the verification survey",
    "Waiting: verification not answered yet",
    "Out: verification answers did not match",
    "Out: survey never finished",
    "Out: flagged, never sent verification",
]
BECAME_VALID_ORDER = [
    "Cleared by bot screen (Pay Now)",
    "Verified, was Manual Review",
    "Verified, was Do Not Pay",
    "Verified, was Excluded",
]


def load_verification(project_dir: Path, snapshot_dir: Path, live: bool = True) -> tuple[pd.DataFrame, dict]:
    """The PID 6277 export: pulled live and identity-checked, or re-read offline.

    A live pull is saved (owner-only) beside the four-study snapshot it was
    analysed with, together with its SHA-256 digest, so an offline rerun reads
    back exactly the same verification answers.
    """
    record_path = Path(snapshot_dir) / f"{VERIFICATION_PID}_record.json"
    manifest_path = Path(snapshot_dir) / f"{VERIFICATION_PID}_manifest.json"
    if not live:
        if not manifest_path.is_file():
            raise FileNotFoundError("This snapshot has no saved verification export; rerun with LIVE_PULL = True.")
        details = json.loads(manifest_path.read_text())
        if not record_path.is_file() or sources._digest(record_path) != details["sha256"]:
            raise ValueError("The saved verification export changed after it was pulled.")
        return pd.DataFrame(json.loads(record_path.read_text())), details

    project_dir = Path(project_dir)
    settings = yaml.safe_load((project_dir / "config.yaml").read_text())["redcap"]
    secrets = dotenv_values(project_dir.parents[1] / ".env")
    api_url = str(secrets.get(settings["api_url_env"]) or "").strip()
    token = next((str(secrets[name]).strip() for name in VERIFICATION_TOKEN_ENVS
                  if str(secrets.get(name) or "").strip()), "")
    if not api_url.startswith("https://") or not token:
        raise ValueError("The .env file needs an HTTPS REDCAP_API_URL and TOKEN_VERIFICATION_6277.")
    expected_pid = int(secrets.get("PID_VERIFICATION") or VERIFICATION_PID)
    timeout = int(settings.get("request_timeout_seconds", 120))
    retries = max(1, int(settings.get("request_retries", 3)))
    project = sources._fetch(api_url, token, expected_pid, "project", timeout, retries)
    title = " ".join(str(project.get("project_title", "")).split())
    if int(project.get("project_id", -1)) != expected_pid or title != VERIFICATION_TITLE:
        raise ValueError(f"The verification token does not reach '{VERIFICATION_TITLE}' (PID {expected_pid}).")
    records = sources._fetch(api_url, token, expected_pid, "record", timeout, retries)
    sources._private_json(record_path, records)
    details = {"project_id": expected_pid, "project_title": title, "records": len(records),
               "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
               "sha256": sources._digest(record_path)}
    sources._private_json(manifest_path, details)
    return pd.DataFrame(records), details


def recheck_verification(verification: pd.DataFrame) -> pd.Series:
    """Recompute REDCap's pass/fail from the raw answers: 1 pass, 0 fail, blank undecided.

    Mirrors the project's calculated fields: all three answers present and all
    three equal to the original pass; any answered pair that differs fails.
    Two numbers compare as numbers, so a ZIP that lost its leading zero (2134)
    still matches 02134.
    """
    pairs = []
    for new, old in [("verify_birth_year", "original_birth_year"), ("verify_zip", "original_zip"),
                     ("verify_education", "original_education")]:
        a, b = sources._strings(verification[new]), sources._strings(verification[old])
        x, y = pd.to_numeric(a.astype(str), errors="coerce"), pd.to_numeric(b.astype(str), errors="coerce")
        equal = np.where(x.notna() & y.notna(), x == y, a.astype(str) == b.astype(str))
        pairs.append(pd.Series(np.where(a.eq("") | b.eq(""), np.nan, equal.astype(float)), index=verification.index))
    pairs = pd.concat(pairs, axis=1)
    answered = _all_answered(verification)
    result = pd.Series(np.nan, index=verification.index)
    result[answered & pairs.eq(0).any(axis=1)] = 0.0
    result[answered & pairs.eq(1).all(axis=1)] = 1.0
    return result


def _all_answered(verification: pd.DataFrame) -> pd.Series:
    return pd.concat([sources._strings(verification[field]).ne("") for field in VERIFY_FIELDS], axis=1).all(axis=1)


def verification_results(verification: pd.DataFrame) -> pd.DataFrame:
    """One row per verification record linked to an original response.

    The pass decision is recomputed from the live answers. REDCap stores its
    calculated pass flag when a record is saved, so a stored flag can be out of
    date; ``stale_in_redcap`` marks where the stored flag and the recheck differ.
    """
    pid = sources._strings(verification["source_pid"])
    record = sources._strings(verification["source_record_id"])
    fresh = recheck_verification(verification)
    label = np.select([fresh.eq(1), fresh.eq(0), _all_answered(verification)],
                      [VERIFIED_LABEL, MISMATCH_LABEL, MISSING_ORIGINAL_LABEL], default=AWAITING_LABEL)
    stored = sources._strings(verification["passed_verification"]).map({"1": 1.0, "0": 0.0})
    out = pd.DataFrame({"response_key": pid + "-" + record, "verification": label,
                        "verified": fresh.eq(1), "stale_in_redcap": stored.fillna(-1).ne(fresh.fillna(-1))},
                       index=verification.index).loc[pid.ne("") & record.ne("")]
    if out["response_key"].duplicated().any():
        raise ValueError("Two verification records point at the same original response.")
    return out


# ── Valid_Data ──────────────────────────────────────────────────────────────

def fold_all_responses(src: sources.RefinedSources) -> pd.DataFrame:
    """Every exported response, Spanish answers folded onto English fields, before any exclusion.

    The verification survey also re-checks responses the bot screen set aside
    (the PID 4700 archive copies in 5749 and the 'test'-word responses), so
    their answers are needed alongside the screened ones.
    """
    frames = []
    for pid, study in src.registry.items():
        raw = pd.DataFrame(json.loads((src.snapshot_dir / f"{pid}_record.json").read_text()))
        raw["record_id"] = sources._strings(raw["record_id"])
        folded, *_ = sources.fold_verified_twins(raw, src.raw_metadata_by_pid[pid], pid)
        frames.append(folded.assign(project_id=pid, study=study["sheet_name"]))
    responses = pd.concat(frames, ignore_index=True, sort=False)
    responses = responses.assign(
        response_key=responses["project_id"].astype(str) + "-" + responses["record_id"].astype(str))
    if not responses["response_key"].is_unique:
        raise ValueError("Two responses share a study and record number.")
    return responses


def assign_validity(responses: pd.DataFrame, screen: pd.DataFrame, verification: pd.DataFrame) -> pd.DataFrame:
    """Give every response its bot-screen status, verification result, route and Valid flag.

    ``screen`` carries ``response_key`` and ``Payment_Status`` for screened
    responses; anything missing from it was excluded before screening.
    ``verification`` is the raw PID 6277 export.
    """
    out = responses.copy()
    out["bot_screen"] = out["response_key"].map(screen.set_index("response_key")["Payment_Status"]).fillna("Excluded")

    checked = verification_results(verification).set_index("response_key")
    unknown = set(checked.index) - set(out["response_key"])
    if unknown:
        raise ValueError(f"{len(unknown)} verification records point at responses missing from the studies.")
    out["verification"] = out["response_key"].map(checked["verification"]).fillna("Not invited")
    out["verified"] = out["response_key"].isin(checked.index[checked["verified"]])

    finished = pd.concat([pd.to_numeric(out[field], errors="coerce").eq(2) for field in COMPLETION_FIELDS],
                         axis=1).all(axis=1)
    pay_now = out["bot_screen"].eq("Pay Now")
    out["survey_finished"] = finished
    out["valid"] = finished & (pay_now | out["verified"])
    routes = [  # first match wins
        (out["valid"] & pay_now, "Valid: cleared by the bot screen (Pay Now)"),
        (out["valid"], "Valid: confirmed by the verification survey"),
        (~finished, "Out: survey never finished"),
        (out["verification"].eq(AWAITING_LABEL), "Waiting: verification not answered yet"),
        (out["verification"].str.startswith("Needs review"), "Out: verification answers did not match"),
    ]
    out["route"] = np.select([mask for mask, _ in routes], [route for _, route in routes],
                             default="Out: flagged, never sent verification")
    out["became_valid"] = np.where(pay_now, "Cleared by bot screen (Pay Now)", "Verified, was " + out["bot_screen"])
    return out


def build_valid_data(src: sources.RefinedSources, verification: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All responses with their route into (or out of) Valid_Data, and Valid_Data itself."""
    screen = simplified.build_simplified_records(src)
    screen["response_key"] = screen["PID"].astype(str) + "-" + screen["Record_ID"].astype(str)
    responses = assign_validity(fold_all_responses(src), screen, verification)
    return responses, responses.loc[responses["valid"]].copy()


# ── Attitude scores (the ten locked TFA domains) ────────────────────────────

SCORES: dict[str, tuple[dict[str, bool], int]] = {
    # plain-English score: ({question: reverse-scored?}, answers needed)
    "Feels positive & trusting": ({"tfa_happy": False, "tfa_trusting": False, "tfa_comfortable": False}, 2),
    "Not scared or sad": ({"tfa_scared": True, "tfa_sad": True}, 2),
    "Not angry or disgusted": ({"tfa_disgusted": True, "tfa_angry": True}, 2),
    "Extra visit feels doable": ({"tfa_difficult": False, "tfa_doable": True,
                                  "tfa_separate_difficult": False, "tfa_separate_doable": True,
                                  "tfa_series_difficult": False, "tfa_series_doable": True}, 4),
    "Quick tests feel easy": ({"tfa_video": False, "tfa_saliva": False, "tfa_heart": False,
                               "tfa_observe": False}, 3),
    "Brain scans feel easy": ({"tfa_mri": False, "tfa_eeg": False}, 2),
    "Blood test feels easy": ({"tfa_blood": False}, 1),
    "OK with a less accurate test": ({"tfa_screen_accuracy": False}, 1),
    "Finds the result useful": ({"tfa_overall_help": True}, 1),
    "Feels screening is right": ({"tfa_scan_any": True, "tfa_scan_mine": True}, 2),
}
PROJECT_DOMAIN = dict(zip(SCORES, [
    "Positive affect / clinical trust", "Low distress", "Low aversive affect", "Feasibility across pathways",
    "Low-burden modality openness", "Equipment-intensive openness", "Blood-test openness",
    "Accuracy tolerance", "Perceived screening utility", "Ethical / moral support"]))
ACCURACY_SCORE = "OK with a less accurate test"
NOT_ON_SCALE = {"tfa_screen_accuracy": {"6"}}  # "I would not do this kind of test at all"


def question_ranges(metadata_by_pid: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """Each question's lowest and highest answer, read from every study's live metadata.

    A question whose range differs between studies stops the run rather than
    being scored on two different scales.
    """
    rows = []
    for score, (questions, _) in SCORES.items():
        for question, reverse in questions.items():
            found = set()
            for metadata in metadata_by_pid.values():
                match = metadata.loc[metadata["field_name"].eq(question)]
                if match.empty:
                    continue
                field = match.iloc[0]
                if field["field_type"] == "slider":
                    low = float(field["text_validation_min"] or 0)
                    high = float(field["text_validation_max"] or 100)
                else:
                    codes = [float(code) for code in sources.choice_map(field)
                             if code not in NOT_ON_SCALE.get(question, set())]
                    low, high = min(codes), max(codes)
                found.add((low, high))
            if len(found) != 1:
                raise ValueError(f"{question} is missing or has different answer ranges across studies: {found}")
            low, high = found.pop()
            rows.append({"score": score, "question": question, "low": low, "high": high, "reversed": reverse})
    return pd.DataFrame(rows)


def attitude_scores(frame: pd.DataFrame, ranges: pd.DataFrame) -> pd.DataFrame:
    """The ten attitude scores on a 0-100 scale; each needs its minimum number of answers."""
    scores = pd.DataFrame(index=frame.index)
    for score, (_, needed) in SCORES.items():
        parts = []
        for _, item in ranges.loc[ranges["score"].eq(score)].iterrows():
            answer = sources._strings(frame[item["question"]])
            x = pd.to_numeric(answer.astype(str), errors="coerce").astype(float)
            on_scale = ~answer.isin(NOT_ON_SCALE.get(item["question"], set())).to_numpy(dtype=bool)
            x = x.where(on_scale & x.between(item["low"], item["high"]).to_numpy())
            scaled = (x - item["low"]) / (item["high"] - item["low"]) * 100
            parts.append(100 - scaled if item["reversed"] else scaled)
        answered = pd.concat(parts, axis=1)
        scores[score] = answered.mean(axis=1).where(answered.notna().sum(axis=1) >= needed)
    return scores


def score_guide(ranges: pd.DataFrame) -> pd.DataFrame:
    """One row per score: the questions behind it and which ones are reverse-scored."""
    rows = []
    for score, (questions, needed) in SCORES.items():
        items = ranges.loc[ranges["score"].eq(score)]
        scale = ", ".join(sorted({f"{low:g}-{high:g}" for low, high in zip(items["low"], items["high"])}))
        rows.append({"Score (0-100, higher = more accepting)": score,
                     "Questions": ", ".join(q.replace("tfa_", "") for q in questions),
                     "Reverse-scored": ", ".join(q.replace("tfa_", "") for q, r in questions.items() if r) or "none",
                     "Answer scale": scale, "Answers needed": f"{needed} of {len(questions)}",
                     "Project domain (Sections 4-8)": PROJECT_DOMAIN[score]})
    return pd.DataFrame(rows)


# ── Groups ──────────────────────────────────────────────────────────────────

HIGHER, CONDITIONAL = "Higher acceptability", "Conditional acceptability"
GROUP_ORDER = [HIGHER, CONDITIONAL]


def group_names(k: int) -> list[str]:
    """Names from most to least accepting; the two-group names are the project's."""
    if k == 2:
        return list(GROUP_ORDER)
    return [f"Group {rank} of {k}" for rank in range(1, k + 1)]


@dataclass
class GroupFit:
    labels: pd.Series
    model: KMeans
    fill: pd.Series
    center: pd.Series
    spread: pd.Series
    names: dict[int, str]

    def standardized(self, scores: pd.DataFrame) -> pd.DataFrame:
        return (scores.fillna(self.fill) - self.center) / self.spread

    def assign(self, scores: pd.DataFrame) -> pd.Series:
        """Put new caregivers in the nearest group of this fit."""
        return pd.Series(self.model.predict(self.standardized(scores)), index=scores.index).map(self.names)


def fit_groups(scores: pd.DataFrame, k: int = 2, seed: int = 20260725, n_init: int = 100) -> GroupFit:
    """K-means on standardized scores; groups are named by average acceptance (accuracy score left out)."""
    fill = scores.median()
    filled = scores.fillna(fill)
    center, spread = filled.mean(), filled.std(ddof=0)
    model = KMeans(n_clusters=k, n_init=n_init, random_state=seed).fit((filled - center) / spread)
    raw = pd.Series(model.labels_, index=scores.index)
    acceptance = scores.drop(columns=[ACCURACY_SCORE]).groupby(raw).mean().mean(axis=1)
    names = dict(zip(acceptance.sort_values(ascending=False).index, group_names(k)))
    return GroupFit(raw.map(names), model, fill, center, spread, names)


def bootstrap_agreement(scores: pd.DataFrame, labels: pd.Series, k: int, seed: int, repeats: int = 200) -> np.ndarray:
    """For each resample: refit, re-assign everyone, and record the share who keep their group."""
    rng = np.random.default_rng(seed)
    agreement = []
    for repeat in range(repeats):
        sample = scores.iloc[rng.integers(0, len(scores), len(scores))].reset_index(drop=True)
        refit = fit_groups(sample, k=k, seed=seed + repeat, n_init=10)
        agreement.append(float((refit.assign(scores) == labels).mean()))
    return np.array(agreement)


def compare_groups(measures: dict[str, pd.Series], groups: pd.Series) -> pd.DataFrame:
    """Higher minus Conditional for each held-out measure, with a 95% range.

    Yes/no measures are compared as percentages (difference in points), the
    rest as means. Range = difference ± 1.96 × √(var_H/n_H + var_C/n_C).
    """
    rows = []
    for measure, values in measures.items():
        values = values.astype(float)
        higher = values[groups.eq(HIGHER)].dropna()
        conditional = values[groups.eq(CONDITIONAL)].dropna()
        is_share = bool(values.dropna().isin([0, 1]).all())
        unit = 100 if is_share else 1
        ddof = 0 if is_share else 1  # p(1 - p) for a share, the sample variance for a mean
        difference = higher.mean() - conditional.mean()
        margin = 1.96 * np.sqrt(higher.var(ddof=ddof) / len(higher) + conditional.var(ddof=ddof) / len(conditional))
        rows.append({"Measure": measure, "Shown as": "% of group" if is_share else "average",
                     HIGHER: higher.mean() * unit, CONDITIONAL: conditional.mean() * unit,
                     "Difference": difference * unit,
                     "95% range low": (difference - margin) * unit, "95% range high": (difference + margin) * unit,
                     "Clear difference?": "Yes" if abs(difference) > margin else "No",
                     "n Higher": len(higher), "n Conditional": len(conditional)})
    return pd.DataFrame(rows)


# ── ESD Lab chart style ─────────────────────────────────────────────────────

BLUE, SCIENCE_BLUE, COOL_BLUE = "#3366FF", "#91BAF4", "#E6EEFC"  # discovery, science, cool blue
ORANGE, INK = "#F57F00", "#000000"  # confident orange, jet
GROUP_COLORS = {HIGHER: BLUE, CONDITIONAL: ORANGE}


def use_brand_style(repo_dir: Path) -> str:
    """Libre Franklin, jet text and a cool-blue hairline grid; returns the font family in use."""
    family = "sans-serif"
    for path in sorted((Path(repo_dir) / "assets" / "fonts").glob("LibreFranklin-*.ttf")):
        if "Variable" not in path.name:  # matplotlib reads the variable font at its thinnest weight
            font_manager.fontManager.addfont(str(path))
            family = "Libre Franklin"
    plt.rcParams.update({
        "font.family": family, "font.size": 10.5, "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": COOL_BLUE, "axes.linewidth": 1,
        "axes.grid": False, "grid.color": COOL_BLUE, "grid.linewidth": 1, "grid.linestyle": "-",
        "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
        "axes.titlesize": 13, "axes.titlelocation": "left", "axes.titlepad": 12,
        "figure.dpi": 120, "figure.facecolor": "white", "axes.facecolor": "white", "legend.frameon": False,
    })
    return family


def _subtitle(ax: plt.Axes, text: str) -> None:
    ax.text(0, 1.01, text, transform=ax.transAxes, fontsize=9.5, alpha=0.7, va="bottom")


def plot_routes(responses: pd.DataFrame) -> plt.Figure:
    """Where every response ended up; the two Valid_Data routes are highlighted."""
    counts = responses["route"].value_counts().reindex(ROUTE_ORDER, fill_value=0)
    total, valid = int(counts.sum()), int(counts.iloc[:2].sum())
    fig, ax = plt.subplots(figsize=(10.5, 3.8))
    y = np.arange(len(counts))
    ax.barh(y, counts.to_numpy(), height=0.6,
            color=[BLUE if route.startswith("Valid") else SCIENCE_BLUE for route in counts.index])
    for row, n in zip(y, counts.to_numpy()):
        ax.text(n + total * 0.006, row, f"{n:,}  ({n / total:.0%})", va="center", fontsize=10)
    ax.set_yticks(y, counts.index)
    ax.invert_yaxis()
    ax.set_xlim(0, counts.max() * 1.2)
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.set_xlabel("Responses")
    ax.set_title(f"Valid_Data = {valid:,} of {total:,} responses ({valid / total:.0%})", pad=22)
    _subtitle(ax, "Dark blue = in Valid_Data. Light blue = not (yet) valid.")
    fig.tight_layout()
    return fig


def plot_k_choice(silhouette_by_k: dict[int, float], chosen_k: int) -> plt.Figure:
    """Average silhouette for each number of groups; the chosen number is highlighted."""
    ks, values = list(silhouette_by_k), list(silhouette_by_k.values())
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.bar([str(k) for k in ks], values, width=0.55, color=[BLUE if k == chosen_k else SCIENCE_BLUE for k in ks])
    for index, value in enumerate(values):
        ax.text(index, value + max(values) * 0.02, f"{value:.3f}", ha="center", fontsize=10)
    ax.set_ylim(0, max(values) * 1.18)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.set_xlabel("Number of groups (k)")
    ax.set_ylabel("Average silhouette")
    best = ks[int(np.argmax(values))]
    ax.set_title(f"{best} groups give the cleanest split" if best == chosen_k
                 else f"Best k is {best}; the pre-registered k = {chosen_k} is used", pad=22)
    _subtitle(ax, "Higher bar = caregivers sit closer to their own group than to the next one.")
    fig.tight_layout()
    return fig


def plot_profiles(profile: pd.DataFrame, sizes: pd.Series) -> plt.Figure:
    """Dumbbell chart: each score's average in both groups, largest gap at the top."""
    order = (profile[HIGHER] - profile[CONDITIONAL]).sort_values().index
    data = profile.loc[order]
    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    y = np.arange(len(data))
    ax.hlines(y, data[CONDITIONAL], data[HIGHER], color=INK, alpha=0.18, linewidth=2)
    for group in GROUP_ORDER:
        ax.scatter(data[group], y, s=95, color=GROUP_COLORS[group], edgecolor="white", linewidth=2, zorder=3,
                   label=f"{group} (n = {sizes[group]:,}, {sizes[group] / sizes.sum():.0%})")
    for row, (_, values) in zip(y, data.iterrows()):
        ax.text(103, row, f"{values[HIGHER] - values[CONDITIONAL]:+.0f}", va="center", fontsize=10)
    ax.text(103, len(data) - 0.35, "Gap", fontsize=9.5, alpha=0.7, va="bottom")
    ax.set_yticks(y, data.index)
    ax.set_xlim(0, 108)
    ax.set_xticks(range(0, 101, 20))
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.set_xlabel("Average score (0 = least accepting, 100 = most accepting)")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handletextpad=0.3, fontsize=10)
    ax.set_title("What separates the two groups", pad=34)
    fig.tight_layout()
    return fig


def plot_outcomes(comparison: pd.DataFrame, headline: str, traits: list[str]) -> plt.Figure:
    """Left: the headline share by group with its 95% range. Right: trait gaps in points."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(12.5, 4.8), gridspec_kw={"width_ratios": [1, 1.7]})
    top = comparison.set_index("Measure").loc[headline]
    for index, group in enumerate(GROUP_ORDER):
        left.bar(index, top[group], width=0.55, color=GROUP_COLORS[group])
        left.text(index, top[group] + 2, f"{top[group]:.0f}%", ha="center", fontsize=12, fontweight="bold")
    left.set_xticks([0, 1], [f"{g.replace(' ', chr(10), 1)}\n(n = {top['n ' + g.split()[0]]:,})"
                             for g in GROUP_ORDER])
    left.set_ylim(0, 100)
    left.yaxis.grid(True)
    left.set_axisbelow(True)
    left.set_ylabel("% of group")
    left.set_title(f"{headline}", fontsize=12, pad=22)
    _subtitle(left, f"Gap {top['Difference']:+.0f} points (95% range {top['95% range low']:.0f} to "
                    f"{top['95% range high']:.0f})")

    data = comparison.set_index("Measure").loc[traits].iloc[::-1]
    y = np.arange(len(data))
    right.barh(y, data["Difference"], height=0.55,
               color=[BLUE if value > 0 else ORANGE for value in data["Difference"]])
    right.errorbar(data["Difference"], y, xerr=[data["Difference"] - data["95% range low"],
                                                data["95% range high"] - data["Difference"]],
                   fmt="none", ecolor=INK, elinewidth=1, capsize=3)
    for row, value, high, low in zip(y, data["Difference"], data["95% range high"], data["95% range low"]):
        right.text(high + 1.2 if value > 0 else low - 1.2, row, f"{value:+.0f}", va="center",
                   ha="left" if value > 0 else "right", fontsize=10)
    reach = max(abs(data["95% range low"]).max(), abs(data["95% range high"]).max()) + 8
    right.set_xlim(-reach, reach)
    right.set_ylim(-1.3, len(data) - 0.5)
    for x, side, group in [(-reach + 1, "left", CONDITIONAL), (reach - 1, "right", HIGHER)]:
        right.text(x, -1.0, f"More common in {group.split()[0]}", ha=side, va="center", fontsize=9.5, alpha=0.7)
    right.axvline(0, color=INK, linewidth=1)
    right.set_yticks(y, data.index)
    right.xaxis.grid(True)
    right.set_axisbelow(True)
    right.set_xlabel("Difference in percentage points (Higher minus Conditional)")
    right.set_title("Who is in each group", fontsize=12, pad=22)
    _subtitle(right, "Black line = 95% range. A range that crosses 0 means no clear difference.")
    fig.tight_layout()
    return fig


def plot_group_mix(panels: dict[str, pd.DataFrame]) -> plt.Figure:
    """100% bars of group membership, one panel per way of slicing the caregivers."""
    heights = [len(table) for table in panels.values()]
    fig, axes = plt.subplots(len(panels), 1, figsize=(10.5, 1.0 + 0.55 * sum(heights) + 0.6 * len(panels)),
                             gridspec_kw={"height_ratios": heights})
    for ax, (title, table) in zip(np.atleast_1d(axes), panels.items()):
        share = table[GROUP_ORDER].div(table[GROUP_ORDER].sum(axis=1), axis=0) * 100
        y = np.arange(len(share))
        left = np.zeros(len(share))
        for group in GROUP_ORDER:
            ax.barh(y, share[group], left=left, height=0.62, color=GROUP_COLORS[group],
                    edgecolor="white", linewidth=2, label=group)
            for row, (start, width) in enumerate(zip(left, share[group])):
                if width >= 9:
                    ax.text(start + width / 2, row, f"{width:.0f}%", ha="center", va="center", fontsize=10,
                            color="white" if group == HIGHER else INK, fontweight="bold")
            left = left + share[group].to_numpy()
        ax.set_yticks(y, [f"{name}  (n = {int(n):,})" for name, n in zip(share.index, table[GROUP_ORDER].sum(axis=1))])
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xticks([])
        ax.spines["bottom"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_title(title, fontsize=12)
    np.atleast_1d(axes)[0].legend(loc="lower right", bbox_to_anchor=(1, 1.0), ncol=2, fontsize=10)
    fig.tight_layout()
    return fig

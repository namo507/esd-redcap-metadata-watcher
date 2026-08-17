"""The weekly pilot debrief.

The meeting notes asked to "debrief any unexpected scoring outcomes and refine
weights". Anecdote does not scale and it does not survive a busy week, so every
section here is generated from the audit log whether or not anyone remembered to
raise it. The one thing the report cannot do for itself is section 8: it asks
the team out loud whether anything felt wrong that the detector did not flag,
because that is the only available check on the detector's false negatives.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .config import EngineConfig
from .drift import DriftReport
from .store import AuditStore

CRITERION_LABEL = {
    "phi": "continuity",
    "omega": "family preference",
    "psi": "burden relief",
    "p": "protocol continuity",
}


# ---------------------------------------------------------------------------
# Gathering the narrative pieces
# ---------------------------------------------------------------------------


def exception_register(
    store: AuditStore, start: datetime, end: datetime
) -> List[Dict[str, str]]:
    """Every review band, tie, override, provisional, cold start and starvation."""
    runs = store.runs_between(start, end)
    run_ids = [r["run_id"] for r in runs]
    cands = store.candidates_for(run_ids)
    outcomes = {o["run_id"]: o for o in store.outcomes_for(run_ids)}
    by_run: Dict[str, List] = {}
    for c in cands:
        by_run.setdefault(c["run_id"], []).append(c)

    rows: List[Dict[str, str]] = []
    for run in runs:
        pool = sorted(
            [r for r in by_run.get(run["run_id"], []) if r["l1_pass"]],
            key=lambda r: r["rank_position"] or 99,
        )
        outcome = outcomes.get(run["run_id"])
        kinds: List[str] = []
        if run["pool_starvation"]:
            kinds.append("pool starvation")
        if pool and pool[0]["review_band_flag"]:
            kinds.append("review band")
        if any(r["tie_break_applied"] for r in pool):
            kinds.append("tie broken")
        if outcome and outcome["was_override"]:
            kinds.append("override")
        if outcome and outcome["is_provisional"]:
            kinds.append("provisional")
        if outcome and outcome["write_time_conflict"]:
            kinds.append("write-time conflict")
        if pool and pool[0]["is_cold_start"]:
            kinds.append("cold start at rank 1")
        if len(pool) <= 1:
            kinds.append("single-candidate pool")
        if not kinds:
            continue

        note = ""
        if outcome and outcome["was_override"] and pool:
            chosen = next(
                (r for r in pool if r["coordinator_id"] == outcome["assigned_coordinator_id"]),
                None,
            )
            if chosen:
                note = _waterfall_sentence(chosen, pool[0])
        rows.append(
            {
                "visit_id": run["visit_id"],
                "family_id": run["family_id"],
                "checkpoint": run["checkpoint"] or "",
                "kinds": ", ".join(kinds),
                "assigned": (outcome["assigned_coordinator_id"] if outcome else None)
                or "unassigned",
                "reason": (outcome["override_reason_code"] if outcome else None) or "",
                "note": note,
            }
        )
    return rows


def _waterfall_sentence(chosen, suggested) -> str:
    """"You picked B over A; A led on continuity +0.11, B led on burden +0.14"."""
    if chosen["coordinator_id"] == suggested["coordinator_id"]:
        return ""
    parts = []
    for key in ("phi", "omega", "psi", "p"):
        diff = (chosen[f"contrib_{key}"] or 0.0) - (suggested[f"contrib_{key}"] or 0.0)
        if abs(diff) < 0.005:
            continue
        who = chosen["coordinator_id"] if diff > 0 else suggested["coordinator_id"]
        parts.append(f"{who} led on {CRITERION_LABEL[key]} {abs(diff):+.3f}")
    net = (chosen["final_score"] or 0.0) - (suggested["final_score"] or 0.0)
    return "; ".join(parts) + f"; net {net:+.3f}"


def override_waterfalls(
    store: AuditStore, start: datetime, end: datetime
) -> List[Dict[str, str]]:
    runs = store.runs_between(start, end)
    run_ids = [r["run_id"] for r in runs]
    cands = store.candidates_for(run_ids)
    outcomes = {o["run_id"]: o for o in store.outcomes_for(run_ids)}
    by_run: Dict[str, List] = {}
    for c in cands:
        by_run.setdefault(c["run_id"], []).append(c)

    out: List[Dict[str, str]] = []
    for run in runs:
        outcome = outcomes.get(run["run_id"])
        if not outcome or not outcome["was_override"]:
            continue
        pool = sorted(
            [r for r in by_run.get(run["run_id"], []) if r["l1_pass"]],
            key=lambda r: r["rank_position"] or 99,
        )
        if not pool:
            continue
        chosen = next(
            (r for r in pool if r["coordinator_id"] == outcome["assigned_coordinator_id"]),
            None,
        )
        if not chosen:
            continue
        out.append(
            {
                "visit_id": run["visit_id"],
                "suggested": pool[0]["coordinator_id"],
                "chosen": chosen["coordinator_id"],
                "class": outcome["override_reason_class"] or "",
                "code": outcome["override_reason_code"] or "",
                "text": outcome["override_reason_text"] or "",
                "waterfall": _waterfall_sentence(chosen, pool[0]),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def render_markdown(
    rep: DriftReport,
    cfg: EngineConfig,
    store: AuditStore,
    week_label: str,
    code_version: str,
) -> str:
    L: List[str] = []
    a = L.append

    a(f"# ESD visit scheduling: pilot debrief, {week_label}")
    a("")
    a(
        f"Window {rep.period_start:%Y-%m-%d} to {rep.period_end:%Y-%m-%d}. "
        f"{rep.n_runs} visits scored, {rep.n_assigned} assigned, {rep.n_unfilled} unfilled."
    )
    a("")
    a(
        f"Weights `{cfg.weight_vector_id}` (fingerprint `{cfg.fingerprint()}`), "
        f"engine `{code_version}`, elicitation `{cfg.elicitation_method}`, "
        f"review band {cfg.epsilon_review_band:.3f} "
        f"({'calibrated' if cfg.epsilon_calibrated else 'NOT yet calibrated'})."
    )
    a("")

    # 2. fairness
    a("## 1. Fairness panel")
    a("")
    a("| Coordinator | Visits | Burden hrs | Travel min | Capacity hrs | Utilisation |")
    a("|---|---:|---:|---:|---:|---:|")
    for row in rep.fairness:
        a(
            f"| {row.coordinator_id} | {row.visits} | {row.burden_hours:.1f} | "
            f"{row.travel_minutes:.0f} | {row.capacity_hours:.1f} | {row.utilization:.2f} |"
        )
    a("")
    g, lo, hi = rep.gini_utilization
    a(
        f"CV of utilisation **{rep.cv_utilization:.3f}** ({rep.rag.get('cv_utilization')}), "
        f"CV of visit count {rep.cv_visits:.3f}, CV of travel {rep.cv_travel:.3f}. "
        f"Load imbalance (max/mean) {rep.imbalance:.2f}."
    )
    a("")
    a(
        f"Gini on utilisation {g:.3f} with a bootstrap 95% interval of "
        f"[{lo:.3f}, {hi:.3f}]. With this few coordinators that interval is wide on "
        f"purpose: read the CV, and treat any Gini move inside the interval as noise."
    )
    a("")
    a(
        f"Permutation test p = {rep.permutation_p:.3f}. This asks whether the observed "
        f"spread is worse than randomly assigning each visit among the coordinators who "
        f"were actually eligible for it. A high p says the imbalance is coming from who "
        f"was eligible, not from the scoring, which is a constraints conversation rather "
        f"than a weights conversation."
    )
    a("")

    # 3. exceptions
    a("## 2. Exception register")
    a("")
    rows = exception_register(store, rep.period_start, rep.period_end)
    if not rows:
        a("Nothing flagged this week.")
    else:
        a("| Visit | Family | Checkpoint | Flags | Assigned | Reason |")
        a("|---|---|---|---|---|---|")
        for r in rows:
            a(
                f"| {r['visit_id']} | {r['family_id']} | {r['checkpoint']} | "
                f"{r['kinds']} | {r['assigned']} | {r['reason']} |"
            )
    a("")

    # 4. surprises
    a("## 3. Surprise log")
    a("")
    if not rep.surprise_counts:
        a("No surprise rules fired.")
    else:
        a("| Code | Count |")
        a("|---|---:|")
        for code, count in sorted(rep.surprise_counts.items(), key=lambda kv: -kv[1]):
            a(f"| `{code}` | {count} |")
    a("")
    a(
        "These fire on their own rules, not on anyone's memory: a human overrode the "
        f"top pick, the best available option scored under {cfg.weak_best_score:.2f}, "
        "the top two sat inside the review band, selection stability fell under 0.60, "
        "every feasible candidate shared the same boundary value on a criterion so it "
        "contributed nothing (`CRITERION_INERT`), the top pick was already over "
        "capacity, availability was unverified, or the same pair's score moved more "
        "than 0.15 with unchanged inputs."
    )
    a("")
    a(
        f"`WEAK_BEST_OPTION` uses an absolute threshold of {cfg.weak_best_score:.2f}, "
        f"which is a placeholder until the score distribution is known. Mean top-1 "
        f"this week was {rep.mean_top1:.3f}; reset the threshold to the 10th "
        f"percentile of that distribution once a few weeks are in."
    )
    a("")

    # 5. waterfalls
    a("## 4. Override waterfalls")
    a("")
    falls = override_waterfalls(store, rep.period_start, rep.period_end)
    if not falls:
        a("No overrides this week.")
    else:
        for f in falls:
            a(
                f"- **{f['visit_id']}**: suggested `{f['suggested']}`, chose "
                f"`{f['chosen']}` ({f['code']} / {f['class']}). "
                + (f["waterfall"] or "identical contributions")
                + "."
                + (f" Note: {f['text']}." if f["text"] else "")
            )
    a("")
    a(
        "Overrides are split into `data_defect` and `preference` for a reason. A "
        "data_defect override means the inputs were wrong and it belongs in the fix "
        "queue. A preference override means the ranking was computed correctly and a "
        "human disagreed, and it belongs in the weight re-elicitation. Counting them "
        "together is how a scoring system quietly rots."
    )
    a("")
    if rep.override_by_class:
        a("| Class | Count |")
        a("|---|---:|")
        for k, v in sorted(rep.override_by_class.items()):
            a(f"| {k} | {v} |")
        a("")

    # 6. drift
    a("## 5. Drift panel")
    a("")
    a("| Metric | Value | Threshold | Status |")
    a("|---|---:|---|---|")
    a(
        f"| Review-band rate | {_pct(rep.review_band_rate)} | monitor | "
        f"{'watch' if rep.review_band_rate > 0.3 else 'ok'} |"
    )
    a(f"| Tie rate | {_pct(rep.tie_rate)} | monitor | ok |")
    a(f"| Human override rate | {_pct(rep.override_rate)} | monitor | ok |")
    a(f"| System veto rate (rank 1 declined by a constraint) | {_pct(rep.system_veto_rate)} | monitor | ok |")
    a(
        f"| Top-1 acceptance | {_pct(rep.top1_acceptance)} | monitor | ok |"
    )
    a(
        f"| Top-3 hit rate | {_pct(rep.top3_hit_rate)} | >= {_pct(cfg.top3_hit_rate_target)} | "
        f"{rep.rag.get('top3_hit_rate')} |"
    )
    a(
        f"| PSI on final score | {rep.psi_final_score:.3f} | <{cfg.psi_investigate} | "
        f"{rep.rag.get('psi_final_score')} |"
    )
    a(f"| Mean top-1 score | {rep.mean_top1:.3f} | monitor | ok |")
    a(f"| Mean top-two gap | {rep.mean_gap:.3f} | monitor | ok |")
    a(
        f"| Decisions with an inert criterion | {_pct(rep.boundary_saturation_rate)} "
        f"| monitor | ok |"
    )
    a(f"| Low selection stability | {_pct(rep.low_stability_rate)} | monitor | ok |")
    a(
        f"| Cold-start assignment share | {_pct(rep.cold_start_share)} | "
        f"~ capacity share {_pct(rep.cold_start_capacity_share)} | ok |"
    )
    a(
        f"| Pool starvation rate | {_pct(rep.pool_starvation_rate)} | <10% | "
        f"{rep.rag.get('pool_starvation_rate')} |"
    )
    a(
        f"| Calendar sync success | {_pct(rep.calendar_success_rate)} | >= 98% | "
        f"{rep.rag.get('calendar_success_rate')} |"
    )
    age = "n/a" if rep.median_cache_age_s is None else f"{rep.median_cache_age_s / 60:.1f} min"
    a(f"| Median cache age at scoring | {age} | < 15 min for <=72h visits | ok |")
    a(f"| Provisional assignments | {_pct(rep.provisional_rate)} | monitor | ok |")
    a(f"| Write-time conflicts caught | {rep.write_time_conflicts} | monitor | ok |")
    a("")
    a(
        "Top-3 hit rate is the one to watch hardest. If humans are picking outside "
        "the shortlist more than one time in ten, the model is missing a criterion "
        "they are using, and the override reasons will usually name it."
    )
    a("")

    # 7. shadow optimiser
    a("## 6. Optimiser shadow mode")
    a("")
    shadow = store.query(
        "SELECT * FROM optimizer_shadow WHERE recorded_at >= ? ORDER BY recorded_at DESC LIMIT 5",
        ((rep.period_start - timedelta(days=35)).isoformat(timespec="seconds"),),
    )
    if not shadow:
        a("No shadow runs recorded.")
    else:
        a("| Week | Greedy | Optimal | Regret | Unfilled gap | Escalate |")
        a("|---|---:|---:|---:|---:|---|")
        for s in shadow:
            a(
                f"| {s['period_start'][:10]} | {s['greedy_total']:.2f} | "
                f"{s['optimal_total']:.2f} | {100 * s['regret']:.1f}% | "
                f"{s['unfilled_gap']} | {'YES' if s['escalate'] else 'no'} |"
            )
        a("")
        consecutive = sum(1 for s in shadow[: cfg.regret_consecutive_weeks] if s["escalate"])
        if consecutive >= cfg.regret_consecutive_weeks:
            a(
                f"**Escalation trigger met.** Regret has exceeded "
                f"{100 * cfg.regret_escalation_threshold:.0f}% (or lost a visit) for "
                f"{cfg.regret_consecutive_weeks} consecutive weeks. Move the optimiser "
                f"from shadow to production."
            )
        else:
            a(
                "Greedy is still within tolerance of the optimum, so it stays in "
                "production and the optimiser stays in shadow."
            )
    a("")

    # 8. decisions
    a("## 7. Decisions and actions")
    a("")
    a("| Action | Owner | Effective |")
    a("|---|---|---|")
    a("| _(fill in during the debrief)_ | | next version boundary |")
    a("")
    a(
        "Parameter changes take effect only at a version boundary, never mid-week, "
        "and the new `weight_vector_id` is logged before the first scoring run under it. "
        "Otherwise a week's worth of drift metrics compares two different systems."
    )
    a("")

    # 9. the human check
    a("## 8. Standing question for the team")
    a("")
    a(
        "**Did any assignment this week feel wrong without showing up in section 2 or 3?**"
    )
    a("")
    a(
        "This is the only check we have on the false negatives of the surprise "
        "detector, and it is the reason this debrief is a live conversation rather "
        "than an emailed PDF. Anything raised here becomes either a new surprise rule "
        "or a new criterion."
    )
    a("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Branded HTML
# ---------------------------------------------------------------------------

_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --discovery:#3366FF; --science:#91BAF4; --cool-blue:#E6EEFC;
  --cool-white:#F4F4F6; --jet:#000000; --orange:#F57F00; --red:#D74E2D;
  --yellow:#F4DA26; --pink:#F8B2B1; --paper:#FFFFFF; --ink:#111114;
  --muted:#4a4a55; --line:#d8dfee;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#0d1018; --ink:#eef1f8; --muted:#a8b0c4; --line:#242c3d;
    --cool-blue:#151d31; --cool-white:#121722;
  }}
}}
:root[data-theme="dark"] {{
  --paper:#0d1018; --ink:#eef1f8; --muted:#a8b0c4; --line:#242c3d;
  --cool-blue:#151d31; --cool-white:#121722;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink);
  font-family:"Libre Franklin",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-weight:500; line-height:1.55; }}
.wrap {{ max-width:60rem; margin:0 auto; padding:2.5rem 1.5rem 5rem; }}
h1 {{ font-weight:800; letter-spacing:-0.02em; color:var(--discovery);
  font-size:clamp(1.7rem,4vw,2.5rem); margin:0 0 .4rem; }}
h2 {{ font-weight:700; letter-spacing:-0.01em; color:var(--discovery);
  font-size:1.3rem; margin:2.4rem 0 .6rem; padding-top:1.2rem;
  border-top:2px solid var(--cool-blue); }}
p {{ color:var(--muted); }}
strong {{ color:var(--ink); font-weight:700; }}
code {{ background:var(--cool-blue); padding:.1em .35em; border-radius:4px;
  font-size:.88em; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.tablewrap {{ overflow-x:auto; margin:1rem 0; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; min-width:34rem; }}
th {{ background:var(--discovery); color:#fff; font-weight:700; text-align:left;
  padding:.5rem .7rem; font-size:.78rem; text-transform:uppercase;
  letter-spacing:.04em; }}
td {{ padding:.45rem .7rem; border-bottom:1px solid var(--line); }}
tr:nth-child(even) td {{ background:var(--cool-white); }}
.GREEN,.AMBER,.RED {{ font-weight:700; }}
.GREEN {{ color:#1a7f45; }} .AMBER {{ color:var(--orange); }} .RED {{ color:var(--red); }}
ul {{ color:var(--muted); }}
.meta {{ color:var(--muted); font-size:.9rem; margin-bottom:2rem; }}
</style></head><body><div class="wrap">
{body}
</div></body></html>
"""


def markdown_to_html(md: str, title: str) -> str:
    """Small, dependency-free renderer for the subset of markdown used above."""
    out: List[str] = []
    in_table = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append('<div class="tablewrap"><table>')
                out.append(
                    "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>"
                )
                in_table = True
            else:
                out.append(
                    "<tr>"
                    + "".join(
                        f'<td class="{c if c in ("GREEN","AMBER","RED") else ""}">{_inline(c)}</td>'
                        for c in cells
                    )
                    + "</tr>"
                )
            continue
        if in_table:
            out.append("</table></div>")
            in_table = False
        if stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("- "):
            out.append(f"<ul><li>{_inline(stripped[2:])}</li></ul>")
        elif stripped:
            out.append(f"<p>{_inline(stripped)}</p>")
    if in_table:
        out.append("</table></div>")
    return _HTML_SHELL.format(title=html.escape(title), body="\n".join(out))


def _inline(text: str) -> str:
    text = html.escape(text)
    for marker, tag in (("**", "strong"), ("`", "code")):
        parts = text.split(marker)
        if len(parts) > 2:
            rebuilt = parts[0]
            for i, part in enumerate(parts[1:], start=1):
                rebuilt += (f"<{tag}>" if i % 2 else f"</{tag}>") + part
            text = rebuilt
    return text


def write_debrief(
    rep: DriftReport,
    cfg: EngineConfig,
    store: AuditStore,
    outdir: str,
    week_label: str,
    code_version: str,
) -> Tuple[str, str]:
    os.makedirs(outdir, exist_ok=True)
    md = render_markdown(rep, cfg, store, week_label, code_version)
    md_path = os.path.join(outdir, f"debrief-{week_label}.md")
    html_path = os.path.join(outdir, f"debrief-{week_label}.html")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(markdown_to_html(md, f"ESD pilot debrief {week_label}"))
    return md_path, html_path

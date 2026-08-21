"""Command line entry point.

    python -m esd_scheduler init          write config/engine.json
    python -m esd_scheduler demo          build the synthetic lab and score a week
    python -m esd_scheduler score V001    explain one visit's ranking
    python -m esd_scheduler plan-week     greedy vs optimiser, with regret
    python -m esd_scheduler sync          pull calendars and log the SLO
    python -m esd_scheduler drift         weekly drift metrics
    python -m esd_scheduler debrief       write the weekly debrief (md + html)
    python -m esd_scheduler calibrate     calibrate the review band from the log
    python -m esd_scheduler sensitivity   OAT, criticality, redundancy
    python -m esd_scheduler ahp FILE      derive weights from pairwise judgments
    python -m esd_scheduler verify-graph  run the privacy probes against a live token
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import __version__
from .calendarsync import MockProvider, provider_from_env
from .config import DEFAULT_CONFIG_PATH, EngineConfig, load_config
from .demo import build_lab
from .drift import weekly_drift
from .engine import commit_assignment, plan_week, score_visit
from .models import ComponentScores
from .ranking import CRITERIA, calibrate_epsilon
from .report import write_debrief
from .sensitivity import (
    ahp_weights,
    conditional_logit,
    criticality,
    criticality_summary,
    oat_sensitivity,
    redundancy_matrix,
)
from .store import AuditStore

DB_DEFAULT = os.path.join("data", "esd_scheduler.db")
REPORT_DIR = os.path.join("reports")


def _monday(now: datetime) -> datetime:
    d = now - timedelta(days=now.weekday())
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    cfg = load_config(args.config)
    cfg.validate()
    cfg.save(args.config)
    store = AuditStore(args.db)
    store.record_config(cfg)
    store.close()
    print(f"wrote {args.config} and initialised {args.db}")
    print(f"weight vector {cfg.weight_vector_id}  fingerprint {cfg.fingerprint()}")
    return 0


def cmd_demo(args) -> int:
    cfg = load_config(args.config)
    now = _monday(datetime.now()) + timedelta(hours=9)
    state, visits = build_lab(now)
    store = AuditStore(args.db)
    store.record_config(cfg)
    provider = MockProvider(blocks=getattr(state, "demo_blocks", {}), clock=lambda: now)

    print(f"synthetic lab: {len(state.coordinators)} coordinators, "
          f"{len(state.families)} families, {len(visits)} open visits\n")

    # Simulated human behaviour, so the debrief has overrides to render. Real
    # pilots get these from the scheduler; the reason codes come from the same
    # closed vocabulary either way.
    import random as _random

    rng = _random.Random(cfg.rng_seed)
    scripted = [
        ("family_request", "family asked for the coordinator they met at intake"),
        ("calendar_data_wrong", "Outlook showed free, coordinator was actually on a home visit"),
        ("training_opportunity", "pairing the new hire with a straightforward checkpoint"),
    ]
    override_at = set(rng.sample(range(len(visits)), min(len(scripted), len(visits))))
    script = dict(zip(sorted(override_at), scripted))

    assigned = unfilled = provisional = overrides = 0
    for index, visit in enumerate(visits):
        pool = score_visit(visit, state, cfg, now)
        forced = code = text = None
        if args.simulate_overrides and index in script and len(pool.candidates) >= 2:
            forced = pool.candidates[1].coordinator_id
            code, text = script[index]
            overrides += 1
        run_id, chosen, notes = commit_assignment(
            pool,
            state,
            cfg,
            store,
            provider=provider,
            chosen_coordinator_id=forced,
            override_reason_code=code,
            override_reason_text=text,
            overridden_by="scheduler" if forced else None,
            now=now,
        )
        if chosen:
            assigned += 1
            if chosen.feasibility.provisional:
                provisional += 1
            flag = " [REVIEW]" if chosen.review_band_flag else ""
            prov = " [PROVISIONAL]" if chosen.feasibility.provisional else ""
            flag += " [OVERRIDE]" if forced else ""
            print(
                f"{visit.visit_id} {visit.family_id} {visit.protocol:5s} "
                f"-> {chosen.coordinator_name:6s} {chosen.final_score:.3f}"
                f"  pool={len(pool.candidates)}{flag}{prov}"
            )
        else:
            unfilled += 1
            if pool.candidates:
                # Feasible people existed; a fairness constraint vetoed each of
                # them. That is a capacity finding, not an eligibility finding,
                # and the debrief needs to be able to tell the two apart.
                why = "; ".join(notes) or "all candidates vetoed"
            else:
                reasons = sorted({c.feasibility.fail_reason or "?" for c in pool.rejected})
                why = "no eligible candidate: " + ", ".join(reasons[:3])
            print(f"{visit.visit_id} {visit.family_id} -> MANUAL ({why})")

    greedy, optimal, report, _ = plan_week(visits, state, cfg, now)
    store.record_shadow(
        report, _monday(now).isoformat(), (_monday(now) + timedelta(days=7)).isoformat(),
        optimal.rounds,
    )
    print(
        f"\nassigned {assigned}, manual {unfilled}, provisional {provisional}, "
        f"simulated overrides {overrides}"
        f"\nshadow optimiser: greedy {report.greedy_total:.2f} vs optimal "
        f"{report.optimal_total:.2f}, regret {100 * report.regret:.1f}%, "
        f"unfilled gap {report.unfilled_gap}, escalate={report.escalate}"
    )
    store.close()
    return 0


def cmd_score(args) -> int:
    cfg = load_config(args.config)
    now = _monday(datetime.now()) + timedelta(hours=9)
    state, visits = build_lab(now)
    visit = next((v for v in visits if v.visit_id == args.visit_id), None)
    if visit is None:
        print(f"no such visit {args.visit_id}", file=sys.stderr)
        return 1
    pool = score_visit(visit, state, cfg, now)

    print(f"{visit.visit_id}  family {visit.family_id}  {visit.protocol} {visit.checkpoint}")
    print(f"window {visit.window_start:%Y-%m-%d %H:%M} to {visit.window_end:%Y-%m-%d %H:%M}")
    print(f"sigma {pool.family_sigma}   review band {pool.epsilon_used:.3f}\n")

    print("Layer 1")
    for cand in pool.rejected:
        print(f"  x {cand.coordinator_name:6s} {cand.feasibility.fail_reason}")
    for cand in pool.candidates:
        flags = ",".join(cand.feasibility.soft_flags) or "-"
        print(f"  + {cand.coordinator_name:6s} slot {cand.feasibility.slot_start:%a %H:%M}  {flags}")

    print("\nLayer 2 and 3")
    header = f"  {'rank':<5}{'who':<8}{'phi':>7}{'omega':>7}{'psi':>7}{'p':>5}{'score':>8}{'stab':>7}"
    print(header)
    for cand in pool.candidates:
        c = cand.components
        print(
            f"  {cand.rank_position:<5}{cand.coordinator_name:<8}"
            f"{c.phi:>7.3f}{c.omega:>7.3f}{c.psi:>7.3f}{c.p:>5.0f}"
            f"{cand.final_score:>8.3f}"
            f"{(cand.selection_stability or 0):>7.2f}"
        )
    if pool.surprise_codes:
        print("\nsurprises: " + ", ".join(pool.surprise_codes))
    return 0


def cmd_plan_week(args) -> int:
    cfg = load_config(args.config)
    now = _monday(datetime.now()) + timedelta(hours=9)
    state, visits = build_lab(now)
    greedy, optimal, report, _ = plan_week(visits, state, cfg, now)

    print(f"greedy   total {greedy.total_score:.3f}  unfilled {len(greedy.unfilled)}")
    print(f"optimal  total {optimal.total_score:.3f}  unfilled {len(optimal.unfilled)}"
          f"  repair rounds {optimal.rounds}")
    print(f"regret   {100 * report.regret:.2f}%   unfilled gap {report.unfilled_gap}")
    print(f"verdict  {report.note}")
    print(f"escalate to the optimiser in production: {report.escalate}")

    if args.verbose:
        print("\nper coordinator (optimal):")
        for cid, opts in sorted(optimal.per_coordinator().items()):
            total = sum(o.duration_hours for o in opts)
            print(f"  {cid}  {len(opts)} visits  {total:.1f} h")
    store = AuditStore(args.db)
    store.record_shadow(
        report, _monday(now).isoformat(), (_monday(now) + timedelta(days=7)).isoformat(),
        optimal.rounds,
    )
    store.close()
    return 0


def cmd_sync(args) -> int:
    cfg = load_config(args.config)
    now = _monday(datetime.now()) + timedelta(hours=9)
    state, _ = build_lab(now)
    store = AuditStore(args.db)
    try:
        provider = provider_from_env(getattr(state, "demo_blocks", {}))
    except RuntimeError as exc:
        print(f"calendar provider not configured: {exc}", file=sys.stderr)
        store.close()
        return 2

    ids = sorted(state.coordinators)
    start, end = now, now + timedelta(days=21)
    t0 = time.time()
    snaps = provider.fetch(ids, start, end)
    latency = int((time.time() - t0) * 1000 / max(1, len(ids)))
    ok = 0
    for cid in ids:
        snap = snaps.get(cid)
        store.record_sync(
            cid,
            provider.name,
            "delta",
            now,
            latency,
            bool(snap and snap.sync_ok),
            None if (snap and snap.sync_ok) else (snap.error_code if snap else "missing"),
            len(snap.blocks) if snap else 0,
        )
        ok += 1 if snap and snap.sync_ok else 0
    print(f"synced {ok}/{len(ids)} coordinators via {provider.name} in ~{latency} ms each")
    store.close()
    return 0 if ok == len(ids) else 1


def _week_bounds(args) -> tuple:
    end = _monday(datetime.now()) + timedelta(days=7)
    if args.week_start:
        start = datetime.fromisoformat(args.week_start)
        end = start + timedelta(days=7)
    else:
        start = end - timedelta(days=7)
    return start, end


def cmd_drift(args) -> int:
    cfg = load_config(args.config)
    store = AuditStore(args.db)
    start, end = _week_bounds(args)
    rep = weekly_drift(
        store, cfg, start, end,
        baseline_start=start - timedelta(days=28), baseline_end=start,
    )
    print(f"window {start:%Y-%m-%d} .. {end:%Y-%m-%d}")
    print(f"runs {rep.n_runs}  assigned {rep.n_assigned}  unfilled {rep.n_unfilled}")
    print(f"CV utilisation {rep.cv_utilization:.3f} [{rep.rag.get('cv_utilization')}]"
          f"  imbalance {rep.imbalance:.2f}  permutation p {rep.permutation_p:.3f}")
    g, lo, hi = rep.gini_utilization
    print(f"Gini utilisation {g:.3f}  bootstrap 95% [{lo:.3f}, {hi:.3f}]")
    print(f"review band {100 * rep.review_band_rate:.0f}%  ties {100 * rep.tie_rate:.0f}%"
          f"  overrides {100 * rep.override_rate:.0f}%")
    print(f"top-1 acceptance {100 * rep.top1_acceptance:.0f}%"
          f"  top-3 hit {100 * rep.top3_hit_rate:.0f}% [{rep.rag.get('top3_hit_rate')}]")
    print(f"PSI {rep.psi_final_score:.3f} [{rep.rag.get('psi_final_score')}]"
          f"  mean top-1 {rep.mean_top1:.3f}  mean gap {rep.mean_gap:.3f}")
    print(f"cold-start share {100 * rep.cold_start_share:.0f}%"
          f" vs capacity share {100 * rep.cold_start_capacity_share:.0f}%")
    print(f"pool starvation {100 * rep.pool_starvation_rate:.0f}%"
          f"  calendar success {100 * rep.calendar_success_rate:.0f}%")
    if rep.surprise_counts:
        print("surprises: " + ", ".join(f"{k}={v}" for k, v in sorted(rep.surprise_counts.items())))
    store.close()
    return 0


def cmd_debrief(args) -> int:
    cfg = load_config(args.config)
    store = AuditStore(args.db)
    start, end = _week_bounds(args)
    rep = weekly_drift(
        store, cfg, start, end,
        baseline_start=start - timedelta(days=28), baseline_end=start,
    )
    label = args.label or f"{start:%Y-W%V}"
    md, html_path = write_debrief(rep, cfg, store, args.outdir, label, __version__)
    print(f"wrote {md}")
    print(f"wrote {html_path}")
    store.close()
    return 0


def _decisions_from_store(store: AuditStore, limit: int = 500):
    """Rebuild ComponentScores per decision straight from the audit log."""
    rows = store.query(
        "SELECT * FROM candidate_score WHERE l1_pass = 1 ORDER BY run_id LIMIT ?", (limit * 8,)
    )
    grouped: Dict[str, Dict[str, ComponentScores]] = {}
    for r in rows:
        grouped.setdefault(r["run_id"], {})[r["coordinator_id"]] = ComponentScores(
            phi=r["phi_continuity"] or 0.0,
            omega=r["omega_preference"] or 0.0,
            psi=r["psi_burden_relief"] or 0.0,
            p=r["p_checkpoint"] or 0.0,
            k_prior_visits=r["k_prior_visits"] or 0,
        )
    return grouped


def cmd_calibrate(args) -> int:
    cfg = load_config(args.config)
    store = AuditStore(args.db)
    grouped = _decisions_from_store(store)
    decisions = [d for d in grouped.values() if len(d) >= 2]
    if not decisions:
        print("no multi-candidate decisions logged yet", file=sys.stderr)
        store.close()
        return 1
    eps, diagnostics = calibrate_epsilon(decisions, cfg)
    inside = sum(1 for d in diagnostics if d["gap"] < eps)
    print(f"{len(decisions)} decisions replayed")
    print(f"calibrated review band epsilon* = {eps:.3f} "
          f"(was {cfg.epsilon_review_band:.3f})")
    print(f"{inside}/{len(diagnostics)} decisions ({100 * inside / len(diagnostics):.0f}%) "
          f"fall inside it and route to a human")
    if args.write:
        cfg.epsilon_review_band = eps
        cfg.epsilon_calibrated = True
        cfg.save(args.config)
        store.record_config(cfg)
        print(f"updated {args.config}")
    store.close()
    return 0


def cmd_sensitivity(args) -> int:
    cfg = load_config(args.config)
    store = AuditStore(args.db)
    grouped = _decisions_from_store(store)
    decisions = [d for d in grouped.values() if len(d) >= 2]
    if not decisions:
        print("no decisions logged yet", file=sys.stderr)
        store.close()
        return 1

    print(f"one-at-a-time perturbation, {len(decisions)} decisions\n")
    print(f"  {'criterion':<10}{'delta':>7}{'reversals':>11}{'mean tau':>10}")
    for r in oat_sensitivity(decisions, cfg, args.delta):
        print(f"  {r.criterion:<10}{r.delta:>+7.2f}{100 * r.reversal_rate:>10.0f}%"
              f"{r.mean_tau:>10.3f}")

    print("\ncriticality: smallest weight change that flips the top pick")
    print(f"  {'criterion':<10}{'n flips':>9}{'median':>9}{'p10':>9}")
    for criterion, s in criticality_summary(criticality(decisions, cfg)).items():
        med = "n/a" if s["n"] == 0 else f"{s['median']:.3f}"
        p10 = "n/a" if s["n"] == 0 else f"{s['p10']:.3f}"
        print(f"  {criterion:<10}{int(s['n']):>9}{med:>9}{p10:>9}")

    rows = [c for d in decisions for c in d.values()]
    print("\nredundancy check (|rho| > 0.6 is a merge candidate)")
    for (a, b), rho in redundancy_matrix(rows).items():
        mark = "  <-- merge candidate" if abs(rho) > 0.6 else ""
        print(f"  {a:>6} vs {b:<6} rho {rho:+.3f}{mark}")

    outcomes = {
        o["run_id"]: o["assigned_coordinator_id"]
        for o in store.query("SELECT * FROM assignment_outcome WHERE assigned_coordinator_id IS NOT NULL")
    }
    pairs = [
        (grouped[rid], cid) for rid, cid in outcomes.items()
        if rid in grouped and cid in grouped[rid] and len(grouped[rid]) >= 2
    ]
    if len(pairs) >= 20:
        fit = conditional_logit(pairs)
        print(f"\nrevealed preference (conditional logit, n={fit.n_decisions})")
        for name in CRITERIA:
            print(f"  {name:<10} beta {fit.beta[name]:+.3f}   implied weight "
                  f"{fit.normalized_weights[name]:.3f}   stated {getattr(cfg.weights, name):.3f}")
    else:
        print(f"\nrevealed preference: only {len(pairs)} usable decisions, "
              f"need 50-100 before the logit estimates settle")
    store.close()
    return 0


def cmd_verify_graph(args) -> int:
    """Run the break-it protocol from ESD-Graph-Privacy-RESEARCH-REPORT.md."""
    from .graphcheck import render, run_probes

    token = os.environ.get("ESD_CAL_TOKEN")
    map_path = os.environ.get("ESD_CAL_MAP")
    if not token:
        print("ESD_CAL_TOKEN is not set. Get a delegated token from Graph "
              "Explorer or your sign-in flow and export it.", file=sys.stderr)
        return 2
    mailboxes = {}
    if map_path and os.path.exists(map_path):
        with open(map_path, "r", encoding="utf-8") as fh:
            mailboxes = json.load(fh)
    elif args.mailbox:
        mailboxes = {m: m for m in args.mailbox}
    else:
        print("Set ESD_CAL_MAP or pass --mailbox at least once.", file=sys.stderr)
        return 2

    if args.allow_write_probe:
        print("WARNING: the write probe attempts to create a real calendar event")
        print("on another person's mailbox. If the privacy guarantee is broken,")
        print("an event WILL be created (and then deleted). Only run this against")
        print("a mailbox you are authorised to test.\n")

    report = run_probes(token, mailboxes, allow_write_probe=args.allow_write_probe)
    print(render(report))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(render(report))
        print(f"\nwrote {args.out}")
    return 0 if report.ok else 1


def cmd_ahp(args) -> int:
    """Input: JSON {"names": [...], "matrices": [[[...]]]} one matrix per respondent."""
    with open(args.file, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    names = payload["names"]
    matrices = payload["matrices"]
    from .sensitivity import aggregate_judgments

    aggregated = aggregate_judgments(matrices) if len(matrices) > 1 else matrices[0]
    result = ahp_weights(aggregated, names)
    print(f"{len(matrices)} respondent(s), aggregated by geometric mean of judgments")
    print(f"lambda_max {result.lambda_max:.4f}  CI {result.consistency_index:.4f}  "
          f"CR {result.consistency_ratio:.4f}  "
          f"{'ACCEPTABLE' if result.acceptable else 'REJECT: CR >= 0.10, re-ask the respondent'}")
    for name, w in result.weights.items():
        print(f"  {name:<10} {w:.3f}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def cmd_import_calendar(args) -> int:
    """Read an Outlook PDF print from the command line, at its honest tier."""
    from datetime import datetime as _dt

    from .calendar_import import ColorMap, import_pdf
    from .demo import build_lab
    from .store import AuditStore

    now = _dt.now()
    state, _ = build_lab(now.replace(hour=9, minute=0, second=0, microsecond=0))
    result = import_pdf(args.file, coordinators=state.coordinators, now=now,
                        year_hint=now.year)

    print(f"file       : {result.source_file}")
    print(f"view       : {result.view_type}  (tier {result.tier})")
    print(f"range      : {result.date_range}")
    print(f"calendars  : {', '.join(result.calendar_names) or 'not printed'}")
    print(f"entries    : {result.entry_count}")
    print(f"blocks     : {len(result.blocks)}  ({result.pending_review} awaiting review)")
    print(f"schedulable: {'yes' if result.schedulable else 'NO - workload signal only'}")
    if result.hues_seen:
        print("colours    : " + ", ".join(
            f"{h}={n}" for h, n in sorted(result.hues_seen.items())))
    if not ColorMap.load().confirmed:
        print("\ncolour map is not confirmed, so nothing was attributed to a person.")
    if result.blockers:
        print("\nBLOCKERS:")
        for b in result.blockers:
            print(f"  - {b}")
    if result.notes:
        print("\nNOTES:")
        for n in result.notes:
            print(f"  - {n}")

    if args.record:
        store = AuditStore(args.db)
        store.record_import(result)
        store.close()
        print(f"\nrecorded to {args.db}")
    return 0



def cmd_import_inbox(args) -> int:
    """Import every calendar PDF dropped in the inbox, then file it away.

    This is the unattended half of the upload feature: a coordinator drops a
    print into ``data/inbox`` and the scheduled job picks it up, records it in
    the audit store and moves the original into ``data/uploads`` so the same
    file is never imported twice. Nothing is deleted -- an import that went
    wrong has to remain inspectable.
    """
    import shutil
    from datetime import datetime as _dt

    from .calendar_import import import_pdf
    from .demo import build_lab
    from .store import AuditStore

    inbox = args.inbox
    processed = args.processed
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(processed, exist_ok=True)

    pdfs = sorted(
        os.path.join(inbox, n) for n in os.listdir(inbox)
        if n.lower().endswith(".pdf") and not n.startswith(".")
    )
    if not pdfs:
        print("inbox empty")
        return 0

    now = _dt.now()
    state, _ = build_lab(now.replace(hour=9, minute=0, second=0, microsecond=0))
    store = AuditStore(args.db)
    failures = 0
    try:
        for path in pdfs:
            name = os.path.basename(path)
            try:
                result = import_pdf(path, coordinators=state.coordinators,
                                    now=_dt.now(), year_hint=now.year)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAILED {name}: {type(exc).__name__}: {exc}")
                continue

            store.record_import(result)
            print(
                f"{name}: {result.view_type} view, tier {result.tier}, "
                f"{result.entry_count} entries, {len(result.blocks)} blocks, "
                f"{len(result.unavailable)} absence notice(s)"
            )
            for note in result.unresolved_names:
                print(f"  UNRESOLVED NAME {note['name']}: {note['reason']}")
            for blocker in result.blockers:
                print(f"  BLOCKER {blocker}")

            stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
            shutil.move(path, os.path.join(processed, f"{stamp}-{name}"))
    finally:
        store.close()
    return 1 if failures else 0


def cmd_audit(args) -> int:
    """Summarise what the board has been told and what it decided.

    Auditing is not a report someone writes at the end; it is the log answering
    for itself. Everything here is read straight from the append-only store.
    """
    from .store import AuditStore

    store = AuditStore(args.db)
    try:
        imports = [dict(r) for r in store.imports(limit=args.limit)]
        blocks = [dict(r) for r in store.import_blocks()]
        runs = store.query(
            "SELECT COUNT(*) AS n FROM scoring_run")
        outcomes = store.query(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN override_reason_code IS NOT NULL "
            "THEN 1 ELSE 0 END) AS overrides FROM assignment_outcome")

        print("CALENDAR IMPORTS")
        if not imports:
            print("  none recorded")
        for row in imports:
            print(f"  {row['uploaded_at'][:16]}  {row['view_type']:<10} "
                  f"tier {row['tier']}  {row['entry_count']:>4} entries  "
                  f"{row['block_count']:>3} blocks  "
                  f"{'schedulable' if row['schedulable'] else 'workload only'}")
            print(f"      {row['source_file']}")

        reviewed = sum(1 for b in blocks if b["reviewed"])
        rejected = sum(1 for b in blocks if b["reviewed"] and not b["confirmed"])
        print()
        print("EVIDENCE")
        print(f"  blocks recorded : {len(blocks)}")
        print(f"  in force        : {reviewed - rejected}")
        print(f"  rejected on review: {rejected}")
        if reviewed:
            print(f"  correction rate : {rejected / reviewed:.1%}")

        print()
        print("DECISIONS")
        print(f"  scoring runs    : {runs[0]['n'] if runs else 0}")
        if outcomes and outcomes[0]["n"]:
            n = outcomes[0]["n"]
            ov = outcomes[0]["overrides"] or 0
            print(f"  assignments     : {n}")
            print(f"  overridden      : {ov} ({ov / n:.1%})")
        else:
            print("  assignments     : 0")
    finally:
        store.close()
    return 0



def cmd_doctor(args) -> int:
    """Report what is installed and what each missing piece would cost you.

    Written to be useful rather than merely pass or fail: everything here is
    optional to *something*, so a missing package is reported with the feature
    it disables and the one command that fixes it.
    """
    import importlib
    import shutil

    checks = [
        ("fitz", "PyMuPDF", "reading calendar PDFs",
         "pip3 install --user PyMuPDF", True),
        ("cv2", "opencv-python", "reading calendar screenshots",
         "pip3 install --user opencv-python", False),
        ("numpy", "numpy", "fitting the time axis on a screenshot",
         "pip3 install --user numpy", False),
        ("PIL", "Pillow", "opening screenshots",
         "pip3 install --user Pillow", False),
        ("pytesseract", "pytesseract", "reading a screenshot's hour column",
         "pip3 install --user pytesseract", False),
        ("pptx", "python-pptx", "rebuilding the slide deck",
         "pip3 install --user python-pptx", False),
    ]

    missing_required = 0
    print("PYTHON PACKAGES")
    for module, package, purpose, fix, required in checks:
        try:
            importlib.import_module(module)
            version = ""
            try:
                import importlib.metadata as md
                version = md.version(package)
            except Exception:  # noqa: BLE001
                pass
            print(f"  ok       {package:16s} {version:10s} {purpose}")
        except ImportError:
            tag = "MISSING " if required else "optional"
            print(f"  {tag} {package:16s} {'':10s} {purpose}")
            print(f"           -> {fix}")
            if required:
                missing_required += 1

    print()
    print("COMMAND-LINE TOOLS")
    if shutil.which("tesseract"):
        import subprocess
        out = subprocess.run(["tesseract", "--version"], capture_output=True,
                             text=True).stdout.splitlines()
        print(f"  ok       tesseract        {out[0].split()[-1] if out else '':10s} "
              "reads a screenshot's hour column automatically")
    else:
        print("  optional tesseract                  reads a screenshot's hour column")
        print("           -> brew install tesseract")
        print("           without it, screenshots still import; you state the hours")

    print()
    print("CONFIGURATION")
    for path, what in (
        (os.path.join("config", "protocol-schedule.json"), "checkpoint due dates"),
        (os.path.join("config", "reliability-matrix.json"), "who is signed off on what"),
        (os.path.join("config", "calendar-roles.json"), "what each calendar means"),
        (os.path.join("config", "calendar-colors.json"), "colour to person fallback"),
    ):
        if not os.path.exists(path):
            print(f"  missing  {os.path.basename(path):26s} {what}")
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            state = "confirmed" if raw.get("confirmed") else "PROVISIONAL"
        except (OSError, ValueError):
            state = "unreadable"
        print(f"  {state:9s} {os.path.basename(path):26s} {what}")

    print()
    if missing_required:
        print(f"{missing_required} required package missing. Calendar uploads will fail.")
        return 1
    print("Everything needed for calendar uploads is installed.")
    return 0



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="esd_scheduler", description=__doc__)
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p.add_argument("--db", default=DB_DEFAULT)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    s = sub.add_parser("demo")
    s.add_argument(
        "--no-simulate-overrides",
        dest="simulate_overrides",
        action="store_false",
        help="skip the scripted human overrides used to exercise the debrief",
    )
    s.set_defaults(func=cmd_demo, simulate_overrides=True)

    s = sub.add_parser("score")
    s.add_argument("visit_id")
    s.set_defaults(func=cmd_score)

    s = sub.add_parser("plan-week")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_plan_week)

    sub.add_parser("sync").set_defaults(func=cmd_sync)

    s = sub.add_parser("drift")
    s.add_argument("--week-start")
    s.set_defaults(func=cmd_drift)

    s = sub.add_parser("debrief")
    s.add_argument("--week-start")
    s.add_argument("--label")
    s.add_argument("--outdir", default=REPORT_DIR)
    s.set_defaults(func=cmd_debrief)

    s = sub.add_parser("calibrate")
    s.add_argument("--write", action="store_true")
    s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("sensitivity")
    s.add_argument("--delta", type=float, default=0.05)
    s.set_defaults(func=cmd_sensitivity)

    s = sub.add_parser("ahp")
    s.add_argument("file")
    s.set_defaults(func=cmd_ahp)

    s = sub.add_parser("import-calendar",
                       help="read an Outlook calendar PDF print")
    s.add_argument("file")
    s.add_argument("--record", action="store_true",
                   help="also write the import and its blocks to the audit store")
    s.add_argument("--db", default=os.path.join("data", "visitboard.db"))
    s.set_defaults(func=cmd_import_calendar)

    s = sub.add_parser("import-inbox",
                       help="import every PDF dropped in data/inbox")
    s.add_argument("--inbox", default=os.path.join("data", "inbox"))
    s.add_argument("--processed", default=os.path.join("data", "uploads"))
    s.add_argument("--db", default=os.path.join("data", "visitboard.db"))
    s.set_defaults(func=cmd_import_inbox)

    sub.add_parser("doctor",
                   help="check dependencies and configuration"
                   ).set_defaults(func=cmd_doctor)

    s = sub.add_parser("audit", help="what was imported, decided and overridden")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--db", default=os.path.join("data", "visitboard.db"))
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("verify-graph")
    s.add_argument("--mailbox", action="append",
                   help="mailbox to probe; repeatable. Overridden by ESD_CAL_MAP")
    s.add_argument("--allow-write-probe", action="store_true",
                   help="also attempt an event create (T5). Off by default: it "
                        "writes to a real mailbox if the guarantee is broken")
    s.add_argument("--out", help="also write the report to this path")
    s.set_defaults(func=cmd_verify_graph)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

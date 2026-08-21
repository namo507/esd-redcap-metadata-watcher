"""Append-only audit store (SQLite).

The single most important design decision in this file: **we log the whole
feasible pool, not just the winner.** Every coordinator considered for every
visit gets a row. It costs about five rows per decision and it is what makes
these possible after the fact:

  * conditional-logit weight recovery from what humans actually chose
  * rank-reversal and sensitivity replay under new weights
  * "why wasn't Kali offered?" answered in one query
  * counterfactual re-scoring without re-querying anyone's calendar

None of it can be reconstructed later if only the chosen coordinator was stored.

No UPDATE, no DELETE. Corrections are new rows. Every row carries the weight
vector id and the config fingerprint that produced it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .config import EngineConfig
from .models import CandidateScore, RankedPool

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS weight_vector (
    weight_vector_id   TEXT PRIMARY KEY,
    config_fingerprint TEXT NOT NULL,
    w_phi              REAL NOT NULL,
    w_omega            REAL NOT NULL,
    w_psi              REAL NOT NULL,
    w_p                REAL NOT NULL,
    gamma_travel       REAL NOT NULL,
    kappa_visits       REAL NOT NULL,
    tau_days           REAL NOT NULL,
    epsilon_review     REAL NOT NULL,
    epsilon_calibrated INTEGER NOT NULL,
    n_min_visits       INTEGER NOT NULL,
    m_prior_visits     REAL NOT NULL,
    elicitation_method TEXT NOT NULL,
    consistency_ratio  REAL,
    n_respondents      INTEGER,
    effective_from     TEXT,
    approved_by        TEXT,
    config_json        TEXT NOT NULL,
    recorded_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scoring_run (
    run_id               TEXT PRIMARY KEY,
    visit_id             TEXT NOT NULL,
    protocol             TEXT NOT NULL,
    checkpoint           TEXT,
    family_id            TEXT NOT NULL,
    family_sigma         INTEGER NOT NULL,
    scored_at            TEXT NOT NULL,
    visit_start          TEXT,
    visit_end            TEXT,
    visit_duration_hr    REAL,
    weight_vector_id     TEXT NOT NULL,
    config_fingerprint   TEXT NOT NULL,
    scoring_code_version TEXT NOT NULL,
    pool_size_total      INTEGER NOT NULL,
    pool_size_feasible   INTEGER NOT NULL,
    pool_starvation      INTEGER NOT NULL,
    most_constrained_rank INTEGER,
    optimizer_mode       TEXT NOT NULL,
    epsilon_used         REAL NOT NULL,
    halt_reason          TEXT,
    surprise_codes       TEXT
);

CREATE TABLE IF NOT EXISTS candidate_score (
    candidate_id              TEXT PRIMARY KEY,
    run_id                    TEXT NOT NULL,
    coordinator_id            TEXT NOT NULL,
    coordinator_name          TEXT,
    -- Layer 1
    l1_window_match           INTEGER NOT NULL,
    l1_open_slot              INTEGER NOT NULL,
    l1_no_calendar_clash      INTEGER NOT NULL,
    l1_no_family_conflict     INTEGER NOT NULL,
    l1_credential_match       INTEGER NOT NULL,
    l1_calendar_fresh         INTEGER NOT NULL,
    l1_ramp_ok                INTEGER NOT NULL,
    l1_pass                   INTEGER NOT NULL,
    l1_fail_reason            TEXT,
    missing_credentials       TEXT,
    calendar_status           TEXT,
    calendar_cache_age_s      REAL,
    soft_flags                TEXT,
    slot_start                TEXT,
    slot_end                  TEXT,
    -- Layer 2 raw inputs (replay without re-querying anything)
    k_prior_visits            INTEGER,
    days_since_family_contact REAL,
    committed_hours           REAL,
    capacity_hours            REAL,
    travel_minutes            REAL,
    burden_hours              REAL,
    utilization               REAL,
    prior_checkpoint_flag     INTEGER,
    n_c_total_visits          INTEGER,
    is_cold_start             INTEGER,
    -- Layer 2 derived
    phi_continuity            REAL,
    phi_raw_R                 REAL,
    omega_preference          REAL,
    psi_burden_relief         REAL,
    p_checkpoint              REAL,
    contrib_phi               REAL,
    contrib_omega             REAL,
    contrib_psi               REAL,
    contrib_p                 REAL,
    final_score               REAL,
    score_int_scaled          INTEGER,
    -- Layer 3
    rank_position             INTEGER,
    gap_to_next               REAL,
    review_band_flag          INTEGER,
    in_shortlist              INTEGER,
    selection_stability       REAL,
    tie_break_applied         INTEGER,
    tie_break_rule            TEXT,
    tie_break_seed            INTEGER,
    FOREIGN KEY (run_id) REFERENCES scoring_run(run_id)
);

CREATE TABLE IF NOT EXISTS assignment_outcome (
    run_id                 TEXT PRIMARY KEY,
    assigned_coordinator_id TEXT,
    assigned_rank          INTEGER,
    was_override           INTEGER NOT NULL DEFAULT 0,
    override_reason_code   TEXT,
    override_reason_class  TEXT,
    override_reason_text   TEXT,
    overridden_by          TEXT,
    is_provisional         INTEGER NOT NULL DEFAULT 0,
    confirmed_at           TEXT,
    write_time_conflict    INTEGER NOT NULL DEFAULT 0,
    decided_at             TEXT NOT NULL,
    visit_completed        INTEGER,
    no_show                INTEGER,
    protocol_deviation     INTEGER,
    family_satisfaction    INTEGER,
    FOREIGN KEY (run_id) REFERENCES scoring_run(run_id)
);

CREATE TABLE IF NOT EXISTS calendar_sync_log (
    sync_id        TEXT PRIMARY KEY,
    coordinator_id TEXT NOT NULL,
    provider       TEXT NOT NULL,
    sync_type      TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    latency_ms     INTEGER,
    success        INTEGER NOT NULL,
    error_code     TEXT,
    events_changed INTEGER
);

CREATE TABLE IF NOT EXISTS calendar_import (
    import_id     TEXT PRIMARY KEY,
    uploaded_at   TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    source_hash   TEXT NOT NULL,
    view_type     TEXT NOT NULL,
    tier          INTEGER NOT NULL,
    schedulable   INTEGER NOT NULL,
    date_range    TEXT,
    entry_count   INTEGER NOT NULL,
    block_count   INTEGER NOT NULL,
    blockers      TEXT,
    notes         TEXT,
    payload       TEXT
);

CREATE TABLE IF NOT EXISTS calendar_import_block (
    block_id       TEXT PRIMARY KEY,
    import_id      TEXT NOT NULL,
    run_id         TEXT NOT NULL,
    coordinator_id TEXT NOT NULL,
    start_ts       TEXT NOT NULL,
    end_ts         TEXT NOT NULL,
    reviewed       INTEGER NOT NULL DEFAULT 0,
    confirmed      INTEGER NOT NULL DEFAULT 1,
    reviewed_by    TEXT,
    reviewed_at    TEXT,
    source_hash    TEXT
);

CREATE TABLE IF NOT EXISTS optimizer_shadow (
    shadow_id        TEXT PRIMARY KEY,
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    greedy_total     REAL NOT NULL,
    optimal_total    REAL NOT NULL,
    regret           REAL NOT NULL,
    greedy_unfilled  INTEGER NOT NULL,
    optimal_unfilled INTEGER NOT NULL,
    unfilled_gap     INTEGER NOT NULL,
    escalate         INTEGER NOT NULL,
    rounds           INTEGER,
    recorded_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_run_scored_at ON scoring_run(scored_at);
CREATE INDEX IF NOT EXISTS ix_cand_run ON candidate_score(run_id);
CREATE INDEX IF NOT EXISTS ix_cand_coord ON candidate_score(coordinator_id);
CREATE INDEX IF NOT EXISTS ix_outcome_decided ON assignment_outcome(decided_at);
"""

OVERRIDE_REASON_CODES = {
    # preference: the ranking was computed correctly, a human disagreed
    "family_request": "preference",
    "coordinator_request": "preference",
    "clinical_judgment": "preference",
    "training_opportunity": "preference",
    # data_defect: the system's inputs were wrong. These are bugs, not signal
    # about the weights, and conflating the two is how a scoring system rots.
    "calendar_data_wrong": "data_defect",
    "credential_data_wrong": "data_defect",
    "travel_data_wrong": "data_defect",
    "history_data_wrong": "data_defect",
    # external: neither the model nor the data, just the world
    "family_cancelled": "external",
    "weather_or_transport": "external",
    "other": "external",
    # system: the engine itself declined rank 1. Not a human disagreeing, and
    # counting it as an override would inflate the one metric the weights are
    # judged on.
    "system_constraint_veto": "system",
    "system_write_time_conflict": "system",
}


class AuditStore:
    def __init__(self, path: str = "data/esd_scheduler.db") -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False plus one lock, because the Visitboard serves
        # each HTTP request on its own thread while sharing one store. SQLite
        # handles that fine; what it does not tolerate is two threads
        # interleaving on a single connection, so every statement goes through
        # _exec / _commit / query, and those are the only places that touch it.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._commit()

    # -- serialised access ---------------------------------------------------

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self.conn.execute(sql, params)

    def _commit(self) -> None:
        with self._lock:
            self.conn.commit()

    # -- config --------------------------------------------------------------

    def record_config(self, cfg: EngineConfig) -> None:
        w = cfg.weights
        self._exec(
            """INSERT OR REPLACE INTO weight_vector VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cfg.weight_vector_id,
                cfg.fingerprint(),
                w.phi,
                w.omega,
                w.psi,
                w.p,
                cfg.gamma_travel,
                cfg.kappa_visits,
                cfg.tau_days,
                cfg.epsilon_review_band,
                int(cfg.epsilon_calibrated),
                cfg.n_min_visits,
                cfg.m_prior_visits,
                cfg.elicitation_method,
                cfg.consistency_ratio,
                cfg.n_respondents,
                cfg.effective_from,
                cfg.approved_by,
                json.dumps(cfg.to_dict(), sort_keys=True),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._commit()

    # -- decisions -----------------------------------------------------------

    def record_pool(
        self,
        pool: RankedPool,
        cfg: EngineConfig,
        code_version: str,
        most_constrained_rank: Optional[int] = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        self._exec(
            """INSERT INTO scoring_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                pool.visit.visit_id,
                pool.visit.protocol,
                pool.visit.checkpoint,
                pool.visit.family_id,
                pool.family_sigma,
                pool.scored_at.isoformat(timespec="seconds"),
                pool.visit.window_start.isoformat(timespec="seconds"),
                pool.visit.window_end.isoformat(timespec="seconds"),
                pool.visit.duration_hours,
                pool.weight_vector_id,
                pool.config_fingerprint,
                code_version,
                len(pool.all_rows()),
                len(pool.candidates),
                int(pool.pool_starvation),
                most_constrained_rank,
                cfg.optimizer_mode,
                pool.epsilon_used,
                pool.halt_reason,
                json.dumps(pool.surprise_codes),
            ),
        )
        for cand in pool.all_rows():
            self._insert_candidate(run_id, cand)
        self._commit()
        return run_id

    def _insert_candidate(self, run_id: str, cand: CandidateScore) -> None:
        f = cand.feasibility
        c = cand.components
        self._exec(
            "INSERT INTO candidate_score VALUES ("
            + ",".join(["?"] * 48)
            + ")",
            (
                str(uuid.uuid4()),
                run_id,
                cand.coordinator_id,
                cand.coordinator_name,
                int(f.window_match),
                int(f.open_slot),
                int(f.no_calendar_clash),
                int(f.no_family_conflict),
                int(f.credential_match),
                int(f.calendar_fresh),
                int(f.ramp_ok),
                int(f.passed),
                f.fail_reason,
                ",".join(sorted(f.missing_credentials)) or None,
                f.calendar_status,
                f.calendar_cache_age_s,
                ",".join(f.soft_flags) or None,
                f.slot_start.isoformat(timespec="seconds") if f.slot_start else None,
                f.slot_end.isoformat(timespec="seconds") if f.slot_end else None,
                c.k_prior_visits,
                c.days_since_family_contact,
                c.committed_hours,
                c.capacity_hours,
                c.travel_minutes,
                c.burden_hours,
                c.utilization if c.utilization != float("inf") else None,
                int(c.p >= 1.0),
                c.n_c_total_visits,
                int(c.is_cold_start),
                c.phi,
                c.phi_raw_R,
                c.omega,
                c.psi,
                c.p,
                cand.contributions.get("phi"),
                cand.contributions.get("omega"),
                cand.contributions.get("psi"),
                cand.contributions.get("p"),
                cand.final_score,
                int(round(cand.final_score * 10_000)),
                cand.rank_position,
                cand.gap_to_next,
                int(cand.review_band_flag),
                int(cand.in_shortlist),
                cand.selection_stability,
                int(cand.tie_break_applied),
                cand.tie_break_rule,
                cand.tie_break_seed,
            ),
        )

    def record_outcome(
        self,
        run_id: str,
        assigned_coordinator_id: Optional[str],
        assigned_rank: Optional[int],
        *,
        human_override: bool = False,
        override_reason_code: Optional[str] = None,
        override_reason_text: Optional[str] = None,
        overridden_by: Optional[str] = None,
        is_provisional: bool = False,
        write_time_conflict: bool = False,
        decided_at: Optional[datetime] = None,
    ) -> None:
        # An override is a *human* choosing against the ranking. The engine
        # skipping rank 1 for a fairness veto or a write-time race is a system
        # event, and mixing the two would corrupt the override rate, which is
        # the headline signal for whether the weights are wrong.
        was_override = bool(human_override)
        reason_class = (
            OVERRIDE_REASON_CODES.get(override_reason_code)
            if override_reason_code
            else None
        )
        if was_override and override_reason_code is None:
            # Force the taxonomy. An unexplained override is a lost data point.
            override_reason_code, reason_class = "other", "external"
        elif not was_override and assigned_rank and assigned_rank > 1 and not override_reason_code:
            override_reason_code = (
                "system_write_time_conflict" if write_time_conflict else "system_constraint_veto"
            )
            reason_class = "system"
        self._exec(
            """INSERT OR REPLACE INTO assignment_outcome
               (run_id, assigned_coordinator_id, assigned_rank, was_override,
                override_reason_code, override_reason_class, override_reason_text,
                overridden_by, is_provisional, confirmed_at, write_time_conflict,
                decided_at, visit_completed, no_show, protocol_deviation,
                family_satisfaction)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)""",
            (
                run_id,
                assigned_coordinator_id,
                assigned_rank,
                int(was_override),
                override_reason_code,
                reason_class,
                override_reason_text,
                overridden_by,
                int(is_provisional),
                None,
                int(write_time_conflict),
                (decided_at or datetime.now()).isoformat(timespec="seconds"),
            ),
        )
        self._commit()

    def record_sync(
        self,
        coordinator_id: str,
        provider: str,
        sync_type: str,
        started_at: datetime,
        latency_ms: int,
        success: bool,
        error_code: Optional[str] = None,
        events_changed: Optional[int] = None,
    ) -> None:
        self._exec(
            "INSERT INTO calendar_sync_log VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                coordinator_id,
                provider,
                sync_type,
                started_at.isoformat(timespec="seconds"),
                latency_ms,
                int(success),
                error_code,
                events_changed,
            ),
        )
        self._commit()

    def record_shadow(self, report, period_start: str, period_end: str, rounds: int = 1) -> None:
        self._exec(
            "INSERT INTO optimizer_shadow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                period_start,
                period_end,
                report.greedy_total,
                report.optimal_total,
                report.regret,
                report.greedy_unfilled,
                report.optimal_unfilled,
                report.unfilled_gap,
                int(report.escalate),
                rounds,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._commit()

    # -- reads ---------------------------------------------------------------

    def record_import(self, result) -> None:
        """Persist an upload and its blocks. Blocks land unreviewed by design."""
        import json as _json
        import uuid as _uuid

        d = result.to_dict()
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO calendar_import (import_id, uploaded_at, "
                "source_file, source_hash, view_type, tier, schedulable, date_range, "
                "entry_count, block_count, blockers, notes, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    d["import_id"], d["uploaded_at"], d["source_file"], d["source_hash"],
                    d["view_type"], d["tier"], 1 if d["schedulable"] else 0,
                    d["date_range"], d["entry_count"], d["block_count"],
                    _json.dumps(d["blockers"]), _json.dumps(d["notes"]), _json.dumps(d),
                ),
            )
            for block in result.blocks:
                self.conn.execute(
                    "INSERT OR REPLACE INTO calendar_import_block (block_id, import_id, "
                    "run_id, coordinator_id, start_ts, end_ts, reviewed, confirmed, "
                    "source_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        _uuid.uuid4().hex[:12], d["import_id"], block.run_id,
                        block.coordinator_id, block.start.isoformat(),
                        block.end.isoformat(), 1 if block.reviewed else 0,
                        1 if block.confirmed else 0, block.source_hash,
                    ),
                )
            self.conn.commit()

    def review_block(self, block_id: str, confirmed: bool, reviewer: str) -> bool:
        """Record a human decision on one parsed block."""
        from datetime import datetime as _dt

        with self._lock:
            cur = self.conn.execute(
                "UPDATE calendar_import_block SET reviewed=1, confirmed=?, "
                "reviewed_by=?, reviewed_at=? WHERE block_id=?",
                (1 if confirmed else 0, reviewer, _dt.now().isoformat(timespec="seconds"),
                 block_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def import_fingerprint(self) -> tuple:
        """Cheap "has anything been imported since I last looked" probe.

        The dashboard is not the only thing that writes imports -- the inbox job
        does too -- so a long-running board has to notice work done behind its
        back rather than serving a snapshot from startup.
        """
        row = self.query(
            "SELECT COUNT(*) AS n, COALESCE(MAX(uploaded_at), '') AS last "
            "FROM calendar_import"
        )
        blocks = self.query(
            "SELECT COUNT(*) AS n FROM calendar_import_block "
            "WHERE reviewed=1 AND confirmed=1"
        )
        if not row:
            return (0, "", 0)
        return (row[0]["n"], row[0]["last"], blocks[0]["n"] if blocks else 0)

    def latest_import(self) -> Optional[dict]:
        """The most recent import's full payload, as it was recorded."""
        import json as _json

        # rowid breaks the tie. uploaded_at is second-resolution, so two
        # imports in the same second order arbitrarily and the board can end up
        # showing the earlier one as "latest".
        rows = self.query(
            "SELECT payload FROM calendar_import "
            "ORDER BY uploaded_at DESC, rowid DESC LIMIT 1")
        if not rows:
            return None
        try:
            return _json.loads(rows[0]["payload"])
        except (TypeError, ValueError):
            return None

    def imports(self, limit: int = 25) -> List[sqlite3.Row]:
        return self.query(
            "SELECT * FROM calendar_import ORDER BY uploaded_at DESC, rowid DESC "
            "LIMIT ?", (limit,)
        )

    def import_blocks(self, import_id: Optional[str] = None) -> List[sqlite3.Row]:
        if import_id:
            return self.query(
                "SELECT * FROM calendar_import_block WHERE import_id=? "
                "ORDER BY coordinator_id, start_ts", (import_id,))
        return self.query(
            "SELECT * FROM calendar_import_block ORDER BY start_ts")

    def confirmed_blocks(self) -> List[sqlite3.Row]:
        """Only reviewed-and-confirmed blocks are evidence."""
        return self.query(
            "SELECT * FROM calendar_import_block WHERE reviewed=1 AND confirmed=1 "
            "ORDER BY coordinator_id, start_ts")

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, params))

    def runs_between(self, start: datetime, end: datetime) -> List[sqlite3.Row]:
        return self.query(
            "SELECT * FROM scoring_run WHERE scored_at >= ? AND scored_at < ? ORDER BY scored_at",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        )

    def candidates_for(self, run_ids: Sequence[str]) -> List[sqlite3.Row]:
        if not run_ids:
            return []
        marks = ",".join("?" * len(run_ids))
        return self.query(
            f"SELECT * FROM candidate_score WHERE run_id IN ({marks}) ORDER BY run_id, rank_position",
            tuple(run_ids),
        )

    def outcomes_for(self, run_ids: Sequence[str]) -> List[sqlite3.Row]:
        if not run_ids:
            return []
        marks = ",".join("?" * len(run_ids))
        return self.query(
            f"SELECT * FROM assignment_outcome WHERE run_id IN ({marks})", tuple(run_ids)
        )

    def close(self) -> None:
        with self._lock:
            self.conn.close()

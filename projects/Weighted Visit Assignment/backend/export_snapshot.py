"""Freeze the engine's output into a static snapshot for the hosted board.

The hosted site runs on Cloudflare Workers with no Python, so it cannot call the
engine. Rather than reimplement scheduling in TypeScript, we run the real engine
here and bake its answers into a data module.

What is baked: the roster, every visit, and the full ranked pool for each visit
including everyone who was ruled out and why. What is NOT baked: any scheduling
logic. The site renders these answers and recomputes exactly one thing when an
assignment is made, the burden term, because that is the only quantity an
assignment changes inside a week.

    python3 -m backend.export_snapshot > dashboard/app/board-data.ts
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.session import LabSession  # noqa: E402
from esd_scheduler import __version__ as ENGINE_VERSION  # noqa: E402

HEADER = '''// GENERATED FILE - do not edit by hand.
//
// Produced by:  python3 -m backend.export_snapshot > app/board-data.ts
//
// Every score, ranking and exclusion reason below came from the Python engine
// in esd_scheduler/. The site does not recompute them; it renders them. The one
// exception is the burden term, which is recomputed when a visit is assigned,
// because that is the only quantity an assignment changes within a week. The
// formula is pinned against these baked values by a test.
//
// Roster names are the real lab roster. Every attribute attached to them is
// synthetic demonstration data.

'''


TYPES = """export type Criterion = {
  key: "phi" | "omega" | "psi" | "p";
  label: string;
  help: string;
  value: number;
  weight: number;
  contribution: number;
};

export type Candidate = {
  id: string;
  name: string;
  initials: string;
  rank: number;
  score: number;
  gap_to_next: number | null;
  review_band: boolean;
  confidence: string;
  stability: number | null;
  contributions: Criterion[];
  leads_on: string;
  slot: string | null;
  travel_minutes: number;
  prior_visits: number;
  utilization: number;
  did_previous_checkpoint: boolean;
  provisional: boolean;
  soft_flags: string[];
  blocked_by: string[];
  assignable: boolean;
};

export type VisitSummary = {
  id: string;
  family_id: string;
  protocol: string;
  checkpoint: string;
  title: string;
  family_label: string;
  date: string;
  day_label: string;
  window: string;
  duration_hours: number;
  status: string;
  assigned_to: string | null;
  assigned_id: string | null;
  provisional: boolean;
  was_override: boolean;
};

export type VisitDetail = {
  visit: VisitSummary;
  family_preference: string;
  named_preference: string[];
  required_attributes: string[];
  candidates: Candidate[];
  /* The first candidate a human can actually take. Differs from rank 1 when a
     fairness veto blocks the top-scoring person. The client recomputes this
     after assignments; the baked value is the opening state. */
  recommended_id: string | null;
  top_rank_blocked: boolean;
  excluded: { id: string; name: string; reason: string }[];
  review_band: number;
  close_call: boolean;
  notices: { tone: string; code: string; message: string }[];
};

export type RosterEntry = {
  id: string;
  name: string;
  initials: string;
  credentials: string[];
  capacity_hours: number;
  effective_capacity_hours: number;
  committed_hours: number;
  utilization: number;
  visits_this_week: number;
  is_new: boolean;
};

export type Board = {
  meta: {
    engineVersion: string;
    weightVectorId: string;
    configFingerprint: string;
    reviewBand: number;
    weekOf: string;
    weights: { phi: number; omega: number; psi: number; p: number };
    gammaTravel: number;
    readsTitles: boolean;
    authMode: string;
  };
  roster: RosterEntry[];
  reasonCodes: { code: string; label: string; cls: string }[];
  visits: VisitDetail[];
};
"""


def main() -> int:
    session = LabSession(os.path.join("data", "snapshot-build.db"))
    payload = {
        "meta": {
            "engineVersion": ENGINE_VERSION,
            "weightVectorId": session.cfg.weight_vector_id,
            "configFingerprint": session.cfg.fingerprint(),
            "reviewBand": round(session.cfg.epsilon_review_band, 3),
            "weekOf": session.now.strftime("%Y-%m-%d"),
            "weights": session.cfg.weights.as_dict(),
            "gammaTravel": session.cfg.gamma_travel,
            "readsTitles": False,
            "authMode": session.cfg.graph_auth_mode,
        },
        "roster": session.roster(),
        "reasonCodes": session.reason_codes(),
        "visits": [],
    }
    for visit_id in session.order:
        detail = session.candidates(visit_id)
        detail.pop("assigned", None)
        payload["visits"].append(detail)

    body = json.dumps(payload, indent=2, default=str)
    sys.stdout.write(HEADER)
    # Declared types rather than `as const`: literal-narrowing every id and
    # status string makes the consuming component fight the data instead of
    # render it.
    sys.stdout.write(TYPES)
    sys.stdout.write("\nexport const BOARD: Board = ")
    sys.stdout.write(body)
    sys.stdout.write(";\n")
    session.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

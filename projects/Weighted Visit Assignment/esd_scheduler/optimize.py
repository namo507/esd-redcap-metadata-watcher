"""Batch optimisation: the two nested problems, and when to escalate to them.

Problem A, one coordinator and one day. Weighted interval scheduling:

    max  sum_j x_j S(c, j)
    s.t. selected intervals do not overlap,  x in {0,1}

    Sort by end time, p(j) = last non-overlapping index before j,
    DP[j] = max(DP[j-1], S(c,j) + DP[p(j)]).  O(n log n), exact.

Intervals are inflated by travel, so "non-overlapping" means the coordinator can
actually get from one to the next.

Problem B, the whole team over a week. Assignment with capacities:

    max  sum_c sum_v S(c,v) x_cv  -  Pi * sum_v (1 - sum_c x_cv)
    s.t. sum_c x_cv <= 1,  sum_v d_v x_cv <= Cap_c,  x_cv <= F(c,v),
         plus no-overlap within each (coordinator, day)

Why not the Hungarian algorithm: it solves the square, one-visit-per-person case.
ESD has unequal part-time capacities, several visits per coordinator per week,
and visits that may go unfilled. Hungarian needs dummy padding and node cloning
for all three. Min-cost flow expresses them natively, and the constraint matrix
is totally unimodular so the LP relaxation is already integral.

What min-cost flow cannot express is intra-day overlap, which is exactly problem
A. So the two are solved together: flow allocates at (coordinator, day)
granularity, the DP checks each bucket, and any bucket the DP cannot honour has
its capacity cut and the flow re-solved. That is a logic-based Benders loop, and
at this scale it settles in one or two rounds.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCALE = 10_000  # score -> integer cost; logged, because rounding can flip near-ties


# ---------------------------------------------------------------------------
# Problem A: weighted interval scheduling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    key: str
    start: datetime
    end: datetime
    weight: float


def weighted_interval_schedule(items: Sequence[Interval]) -> Tuple[List[Interval], float]:
    """Exact DP. Returns (chosen, total weight)."""
    if not items:
        return [], 0.0
    ordered = sorted(items, key=lambda it: (it.end, it.start, it.key))
    n = len(ordered)
    ends = [it.end for it in ordered]

    # p(j): index of the last interval that finishes at or before ordered[j] starts
    p = [-1] * n
    for j, it in enumerate(ordered):
        lo, hi, best = 0, j - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ends[mid] <= it.start:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        p[j] = best

    dp = [0.0] * (n + 1)
    for j in range(1, n + 1):
        take = ordered[j - 1].weight + dp[p[j - 1] + 1]
        dp[j] = max(dp[j - 1], take)

    chosen: List[Interval] = []
    j = n
    while j > 0:
        take = ordered[j - 1].weight + dp[p[j - 1] + 1]
        if take >= dp[j - 1] - 1e-12 and abs(dp[j] - take) < 1e-12:
            chosen.append(ordered[j - 1])
            j = p[j - 1] + 1
        else:
            j -= 1
    chosen.reverse()
    return chosen, dp[n]


def greedy_interval_schedule(items: Sequence[Interval]) -> Tuple[List[Interval], float]:
    """Take the highest-weight interval that still fits. The status quo."""
    chosen: List[Interval] = []
    for it in sorted(items, key=lambda x: (-x.weight, x.key)):
        if all(not (it.start < c.end and c.start < it.end) for c in chosen):
            chosen.append(it)
    return sorted(chosen, key=lambda x: x.start), sum(c.weight for c in chosen)


def dp_trigger_threshold(day_hours: float, mean_visit_hours: float) -> float:
    """Candidate count at which conflicts start to bite.

    Expected pairwise conflicts among n candidates placed in a day of length D
    with mean travel-inflated duration d is about C(n,2) * 2d/D. Setting that to
    one gives n < 1 + sqrt(D/d). For an 8 hour day and 2.5 hour effective
    visits that is under three, so a fourth candidate in a day already means
    greedy is leaving score on the table.
    """
    if mean_visit_hours <= 0:
        return float("inf")
    return 1.0 + math.sqrt(day_hours / mean_visit_hours)


# ---------------------------------------------------------------------------
# Min-cost flow (successive shortest paths with SPFA; costs may be negative)
# ---------------------------------------------------------------------------


class MinCostFlow:
    def __init__(self) -> None:
        self.n = 0
        self.graph: List[List[List[int]]] = []  # [to, cap, cost, rev_index]
        self.index: Dict[str, int] = {}

    def node(self, name: str) -> int:
        if name not in self.index:
            self.index[name] = self.n
            self.graph.append([])
            self.n += 1
        return self.index[name]

    def add_edge(self, u: str, v: str, cap: int, cost: int) -> None:
        iu, iv = self.node(u), self.node(v)
        self.graph[iu].append([iv, cap, cost, len(self.graph[iv])])
        self.graph[iv].append([iu, 0, -cost, len(self.graph[iu]) - 1])

    def flow(self, source: str, sink: str, want: int) -> Tuple[int, int]:
        s, t = self.node(source), self.node(sink)
        total_flow = 0
        total_cost = 0
        INF = float("inf")
        while total_flow < want:
            dist = [INF] * self.n
            inq = [False] * self.n
            prev_v = [-1] * self.n
            prev_e = [-1] * self.n
            dist[s] = 0
            queue = [s]
            inq[s] = True
            while queue:
                u = queue.pop(0)
                inq[u] = False
                for ei, edge in enumerate(self.graph[u]):
                    to, cap, cost, _ = edge
                    if cap > 0 and dist[u] + cost < dist[to] - 1e-12:
                        dist[to] = dist[u] + cost
                        prev_v[to], prev_e[to] = u, ei
                        if not inq[to]:
                            inq[to] = True
                            queue.append(to)
            if dist[t] == INF:
                break
            # Push as much as this path allows.
            push = want - total_flow
            v = t
            while v != s:
                push = min(push, self.graph[prev_v[v]][prev_e[v]][1])
                v = prev_v[v]
            v = t
            while v != s:
                edge = self.graph[prev_v[v]][prev_e[v]]
                edge[1] -= push
                self.graph[v][edge[3]][1] += push
                v = prev_v[v]
            total_flow += push
            total_cost += push * dist[t]
        return total_flow, total_cost

    def saturated(self, u: str, v: str) -> bool:
        if u not in self.index or v not in self.index:
            return False
        iu, iv = self.index[u], self.index[v]
        for edge in self.graph[iu]:
            if edge[0] == iv and edge[2] != 0 and edge[1] == 0:
                return True
        return False

    def flow_on(self, u: str, v: str) -> int:
        """Flow pushed on the forward arc u->v (residual of the reverse arc)."""
        if u not in self.index or v not in self.index:
            return 0
        iu, iv = self.index[u], self.index[v]
        for edge in self.graph[iu]:
            if edge[0] == iv:
                return self.graph[iv][edge[3]][1]
        return 0


# ---------------------------------------------------------------------------
# Problem B: team-week assignment
# ---------------------------------------------------------------------------


@dataclass
class Option:
    """One feasible (visit, coordinator, day) placement with its score and slot."""

    visit_id: str
    coordinator_id: str
    day: date
    score: float
    slot_start: datetime
    slot_end: datetime
    duration_hours: float


@dataclass
class AssignmentPlan:
    assignment: Dict[str, Option] = field(default_factory=dict)   # visit_id -> Option
    unfilled: List[str] = field(default_factory=list)
    total_score: float = 0.0
    rounds: int = 1
    method: str = "mcmf"

    def per_coordinator(self) -> Dict[str, List[Option]]:
        out: Dict[str, List[Option]] = defaultdict(list)
        for opt in self.assignment.values():
            out[opt.coordinator_id].append(opt)
        return dict(out)


def greedy_plan(
    options: Sequence[Option],
    visit_ids: Sequence[str],
    weekly_capacity: Dict[str, int],
) -> AssignmentPlan:
    """Most-constrained-first greedy, the cheap production default.

    Ordering by the size of the feasible pool costs one line and captures much
    of what full optimisation buys, because greedy's worst failure is burning a
    scarce credential (the only ADOS-certified coordinator) on a visit that any
    generalist could have taken.
    """
    by_visit: Dict[str, List[Option]] = defaultdict(list)
    for opt in options:
        by_visit[opt.visit_id].append(opt)

    order = sorted(
        visit_ids,
        key=lambda vid: (len({o.coordinator_id for o in by_visit.get(vid, [])}), vid),
    )
    plan = AssignmentPlan(method="greedy")
    used: Dict[str, int] = defaultdict(int)
    booked: Dict[str, List[Tuple[datetime, datetime]]] = defaultdict(list)

    for vid in order:
        candidates = sorted(by_visit.get(vid, []), key=lambda o: (-o.score, o.coordinator_id))
        placed = False
        for opt in candidates:
            if used[opt.coordinator_id] >= weekly_capacity.get(opt.coordinator_id, 0):
                continue
            if any(
                opt.slot_start < e and s < opt.slot_end
                for s, e in booked[opt.coordinator_id]
            ):
                continue
            plan.assignment[vid] = opt
            plan.total_score += opt.score
            used[opt.coordinator_id] += 1
            booked[opt.coordinator_id].append((opt.slot_start, opt.slot_end))
            placed = True
            break
        if not placed:
            plan.unfilled.append(vid)
    return plan


def mcmf_plan(
    options: Sequence[Option],
    visit_ids: Sequence[str],
    weekly_capacity: Dict[str, int],
    daily_capacity: Optional[Dict[Tuple[str, date], int]] = None,
    unfilled_penalty: float = 1.0,
    max_rounds: int = 5,
) -> AssignmentPlan:
    """Min-cost flow at (coordinator, day) granularity, repaired by the DP.

    Repair loop: solve the flow, then run problem A inside every bucket it
    filled. If the DP cannot honour the count the flow assumed, cut that
    bucket's capacity to what the DP could fit and solve again.
    """
    day_cap: Dict[Tuple[str, date], int] = dict(daily_capacity or {})
    default_day_cap = 3
    plan = AssignmentPlan(method="mcmf")

    for round_index in range(1, max_rounds + 1):
        net = MinCostFlow()
        S, T = "S", "T"
        buckets: Set[Tuple[str, date]] = set()

        for vid in visit_ids:
            net.add_edge(S, f"V:{vid}", 1, 0)
            # Slack arc: leaving a visit unfilled costs Pi.
            net.add_edge(f"V:{vid}", T, 1, int(round(unfilled_penalty * SCALE)))

        for opt in options:
            bucket = (opt.coordinator_id, opt.day)
            buckets.add(bucket)
            net.add_edge(
                f"V:{opt.visit_id}",
                f"CD:{opt.coordinator_id}:{opt.day.isoformat()}",
                1,
                -int(round(opt.score * SCALE)),
            )

        for cid, day in buckets:
            cap = day_cap.get((cid, day), default_day_cap)
            net.add_edge(f"CD:{cid}:{day.isoformat()}", f"C:{cid}", max(0, cap), 0)
        for cid, cap in weekly_capacity.items():
            net.add_edge(f"C:{cid}", T, max(0, int(cap)), 0)

        net.flow(S, T, len(visit_ids))

        # Read the assignment back off the saturated visit -> bucket arcs.
        chosen: Dict[str, Option] = {}
        best_for_arc: Dict[Tuple[str, str], Option] = {}
        for opt in options:
            key = (f"V:{opt.visit_id}", f"CD:{opt.coordinator_id}:{opt.day.isoformat()}")
            prev = best_for_arc.get(key)
            if prev is None or opt.score > prev.score:
                best_for_arc[key] = opt
        for (u, v), opt in best_for_arc.items():
            if net.flow_on(u, v) > 0:
                chosen[opt.visit_id] = opt

        # Repair: DP inside every bucket the flow used.
        violations = 0
        by_bucket: Dict[Tuple[str, date], List[Option]] = defaultdict(list)
        for opt in chosen.values():
            by_bucket[(opt.coordinator_id, opt.day)].append(opt)
        for bucket, opts in by_bucket.items():
            intervals = [
                Interval(o.visit_id, o.slot_start, o.slot_end, o.score) for o in opts
            ]
            keep, _ = weighted_interval_schedule(intervals)
            if len(keep) < len(opts):
                violations += 1
                day_cap[bucket] = len(keep)
                keep_ids = {k.key for k in keep}
                for o in opts:
                    if o.visit_id not in keep_ids:
                        chosen.pop(o.visit_id, None)

        plan = AssignmentPlan(
            assignment=chosen,
            unfilled=[v for v in visit_ids if v not in chosen],
            total_score=sum(o.score for o in chosen.values()),
            rounds=round_index,
            method="mcmf",
        )
        if violations == 0:
            break
    return plan


# ---------------------------------------------------------------------------
# Shadow-mode regret
# ---------------------------------------------------------------------------


@dataclass
class RegretReport:
    greedy_total: float
    optimal_total: float
    regret: float
    greedy_unfilled: int
    optimal_unfilled: int
    unfilled_gap: int
    escalate: bool
    note: str = ""


def regret(
    greedy: AssignmentPlan, optimal: AssignmentPlan, threshold: float = 0.03
) -> RegretReport:
    """Measure what greedy costs, so escalation is a number and not an argument.

    Run this every week from day one with the optimiser in shadow mode: it
    computes and logs but changes nothing. Escalate to the optimiser in
    production only when the measured regret earns it.
    """
    denom = optimal.total_score if optimal.total_score > 0 else 1.0
    value = (optimal.total_score - greedy.total_score) / denom
    gap = len(greedy.unfilled) - len(optimal.unfilled)
    escalate = value > threshold or gap >= 1
    note = (
        "greedy is leaving measurable score on the table"
        if escalate
        else "greedy is within tolerance of the optimum"
    )
    return RegretReport(
        greedy_total=greedy.total_score,
        optimal_total=optimal.total_score,
        regret=value,
        greedy_unfilled=len(greedy.unfilled),
        optimal_unfilled=len(optimal.unfilled),
        unfilled_gap=gap,
        escalate=escalate,
        note=note,
    )

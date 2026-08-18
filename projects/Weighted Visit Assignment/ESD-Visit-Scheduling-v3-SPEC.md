# ESD Weighted Visit Assignment, v3

**Specification, implementation and operating manual.**
Supersedes `visit-scheduling-architecture` (v1) and `ESD-Visit-Scheduling-v2`.

Version 3.0.0 · 17 August 2026 · engine at `esd_scheduler/` · status: pilot-ready,
weights provisional pending elicitation.

---

## 0. What changed and why

The meeting notes set four next steps. All four are implemented:

| Meeting note | Where it landed |
|---|---|
| Explore calendar data integration via Microsoft Graph / API | `calendarsync.py`: Graph `getSchedule` and Google `freeBusy` adapters, a staleness policy with three freshness classes, a write-time recheck, and a circuit breaker |
| Travel → convert to a workload metric | Merged into one prospective-burden term Ψ, with the exchange rate γ made explicit and elicitable |
| Family history + recency merged for continuity | Merged into one continuity index Φ |
| Debrief unexpected scoring outcomes and refine weights | `ranking.detect_surprises`, `drift.py`, `report.py`: unexpected outcomes are *detected events* with named rules, not anecdotes |

Three findings drove the rest of the redesign.

**Finding 1 — the v2 weights implied one hour of driving was worth about thirteen
hours of clinic work.** Under min–max normalisation, marginal sensitivity is
weight ÷ range:

$$\frac{\partial S/\partial T_{\text{hr}}}{\partial S/\partial H_{\text{hr}}}
=\frac{w_{\text{travel}}\cdot 60}{R_T}\cdot\frac{R_H}{w_{\text{workload}}}
=\frac{0.15\times 60}{70}\cdot\frac{20}{0.20}\approx 12.9$$

Nobody would defend that ratio out loud, so v3 states the exchange rate as a
single elicited coefficient γ instead of leaving it implicit in two weights and
two ranges.

**Finding 2 — pool-relative normalisation caused the cold-start bug, not missing
history.** Normalising against "the busiest teammate" makes every score
non-stationary: the same 30-minute travel difference is worth three times as
much on a day when the pool happens to be tight. It is also what handed a new
coordinator a 1.0. v3 normalises against *capacity*.

**Finding 3 — 0.65 of the total weight sat on one latent construct split across
three slots.** Decomposing one construct into several criteria inflates its
elicited total (splitting bias). Merging first and eliciting second is the only
order that gives a defensible answer, and it drops the exercise from ten
pairwise comparisons to six.

---

## 1. The scoring function

### Layer 0 — calendar freshness gate

| Visit horizon | Hard (block) | Soft (flag) |
|---|---|---|
| ≤ 72 h | 15 min | 60 min |
| 3 – 14 d | 4 h | 24 h |
| > 14 d | 24 h | 72 h |

$$\text{class}(c) = \begin{cases}
\textsf{fresh} & \text{age} \le T_{\text{hard}} \\
\textsf{stale} & T_{\text{hard}} < \text{age} \le T_{\text{soft}} \\
\textsf{expired} & \text{age} > T_{\text{soft}} \text{ or sync failed}
\end{cases}$$

`fresh` scores and auto-commits. `stale` scores normally but the assignment
becomes **provisional**: family notification is blocked until a human confirms.
`expired` fails Layer 1.

Staleness is never a score penalty. A penalty would make a stale-but-ideal
candidate lose for a reason unrelated to fit and would quietly corrupt the
metric every other diagnostic is built on.

Additional rules: delta sync every 5 minutes, full reconcile nightly, a
**write-time recheck** of the single winning pair immediately before commit (one
API call, closes the fifteen-minute race that causes double-booking), and a
circuit breaker that halts the whole run if more than 20% of the team is
unverifiable.

### Layer 0a — privacy floor (free/busy only)

Delegated auth, read-only, free/busy only. The engine refuses to start on
anything else.

$$\text{scopes}(token) \subseteq \{\texttt{Calendars.Read.Shared}\}
\quad\wedge\quad \text{roles}(token) = \emptyset$$

`calendarsync.assert_least_privilege` decodes the token and fails closed on
either condition. A `roles` claim means application permissions are live, and
app-only calendar access **cannot be reduced to free/busy**: Microsoft publishes
no calendar app role below `Calendars.Read`, which reads subject and body. Mail
has a `ReadBasic` variant; calendars do not.

Whether `subject` arrives at all is decided by the Exchange **calendar sharing
level**, not by the OAuth scope. At the tenant default (`AvailabilityOnly`) the
server omits it; at `LimitedDetails` it sends it. The engine therefore strips
`subject`, `location` and `isPrivate` at ingestion **regardless of what
arrives**, so a sharing change made by one person cannot quietly widen what the
lab holds. Nothing sensitive reaches `BusyBlock`, and therefore nothing reaches
the audit log.

Verification is a command, not a promise:

```bash
make verify-graph        # T1-T9 probes against a live token
```

Full reasoning, the IT ticket, and the ten-test break-it protocol are in
[`ESD-Graph-Privacy-RESEARCH-REPORT.md`](ESD-Graph-Privacy-RESEARCH-REPORT.md).

**Two corrections that came out of the research.**

`availabilityView` is `0` = free, `1` = tentative, `2` = busy, `3` = out of
office. A widely-circulated Copilot answer states the reverse ("busy is zero,
available is three"); building on it inverts the scheduler and books visits into
exactly the slots people are busy. Pinned by test.

`workingHours` arrives in the same `getSchedule` response, so **blank is not
free**. An empty 19:00 is outside the working envelope and is not availability;
an empty 10:00 is inside it and is. Free time now requires *digit 0 **and**
inside `workingHours`*, with Outlook's envelope taking precedence over the
locally configured pattern.

### Layer 1 — hard eligibility

$$F(c,v) = W \wedge A \wedge \neg X \wedge \neg E \wedge K \wedge \text{Cal} \wedge \text{Ramp}$$

| | Predicate | Note |
|---|---|---|
| W | date window match | visit window overlaps declared working hours |
| A | open slot | a free block of `duration + round-trip travel` exists |
| X | no calendar clash | nothing hard-booked over it (`busy`, `oof`) |
| E | no family conflict | hard exclusion list; checked first, it is the most sensitive |
| K | credential match | $\text{Req}(\text{protocol}) \subseteq \text{Cred}(c)$, table-driven |
| Cal | calendar fresh | Layer 0 |
| Ramp | onboarding cap | new hires capped at *q* visits/week |

Slots are travel-inflated. Without that the engine will happily schedule two
visits forty minutes apart across town.

Ramp is a **constraint, not a score adjustment**. "Do not overload a new hire in
week one" is a policy, and policies belong where they cannot be traded away
against a good continuity score.

If the feasible pool would be empty, calendar-unavailable candidates are
re-admitted — never credential or exclusion failures — each flagged
`availability_unverified` and requiring manual confirmation.

### Layer 2 — four criteria

$$S(c,v) = w_\Phi \Phi + w_\Omega \Omega + w_\Psi \Psi + w_P P, \qquad \textstyle\sum w = 1$$

**Φ — continuity index** (absorbs v2's family history and family-level recency)

$$R(c,v) = \underbrace{\left(1 - e^{-k_{cf}/\kappa}\right)}_{\text{saturating familiarity}}
\cdot \underbrace{e^{-\Delta_{cf}/\tau}}_{\text{freshness decay}}$$

$$\Phi = \tfrac{1+\sigma_f}{2}R + \tfrac{1-\sigma_f}{2}(1-R)$$

$\kappa = 2$ (1 visit → 0.39, 3 → 0.78, 5 → 0.92), $\tau = 75$ days,
$\sigma_f = +1$ continuity preferred (lab default), $-1$ fresh face preferred.

**This is where cold start dies by construction.** $k=0 \Rightarrow R=0$ exactly.
$\Delta_{cf}$ is *undefined* when they have never met, and it is never evaluated,
because the product has already zeroed. The v2 bug was treating "undefined" as
"extreme"; the fix is defining the semantics, not imputing a value.

Flipping at the index level rather than at the familiarity level means the
fresh-face case correctly rewards a long gap since last contact, not just a low
visit count.

**Ω — family preference** (its own criterion, per the meeting notes)

| Situation | Value |
|---|---|
| coordinator explicitly requested | 1.00 |
| family named someone else | 0.35 |
| soft avoid | 0.00 |
| **no preference on record** | **0.50 (neutral)** |
| attribute requirement (e.g. Spanish) | ± 0.25 by share satisfied |

Neutral, not high, when data is missing: absent data must never move a ranking.
Hard exclusions never reach here — they are Layer 1 and cannot be outscored.

**Ψ — burden relief** (absorbs v2's workload and travel)

$$B(c,v) = \hat H_c + d_v + \gamma\frac{T(c,v)}{60}, \qquad
u = \frac{B}{\mathrm{Cap}_c(t)}, \qquad
\Psi = 1 - \mathrm{clip}(u, 0, 1)$$

Two changes that matter. It is **prospective**: $d_v$ and $T(c,v)$ are the
marginal cost of *this* visit, where v2 held a stock (hours booked) and a
marginal cost (this trip) as two separate normalised terms. And it is
**capacity-referenced**, not pool-referenced, which handles part-time
coordinators natively and makes the score stationary week to week.

γ is the burden-equivalence coefficient, elicited in one question: *"how many
extra minutes of clinic time would you accept to avoid ten minutes of driving?"*
Take the team median ÷ 10. Expect 1.5–3.

The merge is a **repricing, and the lab must see the price**:

$$w^{\text{eff}}_{\text{travel}} = w_\Psi\cdot\frac{\gamma R_T/60}{R_H+\gamma R_T/60}$$

| γ | effective travel weight | vs v2's 0.15 |
|---|---|---|
| 1 (pure time) | 0.019 | ÷ 7.8 |
| 2 (default) | 0.037 | ÷ 4.1 |
| 3 | 0.053 | ÷ 2.8 |
| 12.9 | 0.150 | ÷ 1.0 |

If the team objects that travel should matter more than γ justifies, that
objection is about **equity of travel distribution**, not burden. Equity is a
constraint, not a criterion — see §3.

**P — protocol continuity** (binary: did this person run the previous checkpoint)

Kept separate from Φ despite the correlation, because it is a *measurement
validity* claim (same ADOS rater) owned by the PI, not a relationship claim
owned by the scheduler. They must be able to move independently, and if the PI
ever mandates same-rater checkpoints, P becomes a Layer 1 constraint without
disturbing Φ.

**Cold start, correctly split by data type**

| Data type | Example | Handling |
|---|---|---|
| Observed and true | scheduled hours = 0 | **No shrinkage.** An empty calendar really is empty. Imputing a median starves someone who needs work. |
| Undefined by construction | $\Delta_{cf}$ when $k=0$ | **Define, don't impute.** $R = 0$ via the product form. |
| Estimated with noise | per-coordinator visit-duration multiplier, no-show rate | **Shrinkage belongs here, and only here.** |

The proposed "borrow the team median until $N_{\min}$" is wrong for the first
two of the three. It also has a hard cliff at $N_{\min}$ that discontinuously
re-ranks on the $N_{\min}$-th visit, and it is gameable.

Instead, capacity ramps:

$$\mathrm{Cap}_c(t) = \mathrm{Cap}_c^{\text{full}} \cdot
\min\!\left(1, \frac{n_c + n_0}{N_{\min} + n_0}\right)$$

Honest about the thing that genuinely is lower early on — throughput — and
smooth, with no cliff. For the genuinely estimated parameters, use empirical-Bayes
shrinkage $\hat\theta_c = \lambda_c \bar\theta_c + (1-\lambda_c)\theta_0$ with
$\lambda_c = n_c/(n_c+m)$. Both the method-of-moments route
($\hat m = \hat\sigma^2_{\text{within}}/\hat\sigma^2_{\text{between}}$) and the
precision route ($\mathrm{SE} \le 0.5\sigma_{\text{between}}$) give
$N_{\min} \approx 4m$. With 4–8 coordinators, $\hat\sigma^2_{\text{between}}$ is
badly estimated and can go negative, so **do not fit m from pilot data**: use a
weakly informative $m = 5$, report sensitivity at $m \in \{3,5,10\}$, and revisit
after ~200 logged visits.

### Layer 3 — ranking and the calibrated review band

Shortlist top-K = 3. The review band is **measured, not guessed**:

$$\varepsilon^\star = \min\left\{\varepsilon : \Pr\big[\text{top-1 flips} \mid w \sim \mathrm{Dir}(\alpha)\big] \le 0.10 \text{ for decisions outside } \varepsilon\right\}$$

Run `python -m esd_scheduler calibrate`. On the synthetic pilot data it returned
$\varepsilon^\star = 0.020$, i.e. the v2 guess of 0.05 was too conservative and
was sending twice as many decisions to a human as the weights actually warrant.
A real pilot could move it either way — that is the point of measuring it.

Every decision also carries a **selection stability**, $\Pr(\text{top-1})$ over
the weight simplex. A leader who wins 55% of the simplex mass is not the same
decision as one who wins 99%, even at the same point gap. Stability below 0.60
routes to a human.

Tie-break order: protocol continuity → family-history direction → **uniform
random with the seed logged**. The random rule is not a cop-out: inside a true
tie the alternatives are indifferent by the lab's own criteria, so randomising
is defensible on equipoise grounds and gives unconfounded data on whether
continuity actually moves outcomes. Ties become a free experiment.

### Provisional weights

$w_\Phi = 0.45$, $w_\Omega = 0.15$, $w_\Psi = 0.30$, $w_P = 0.10$.

Family history keeps the highest weight, as the meeting notes require. These are
analyst-assigned and **must not survive the pilot unvalidated** — see §4.

---

## 2. Batch optimisation

### Problem A — one coordinator, one day

$$\max_{x\in\{0,1\}^J} \sum_j x_j S(c,j) \quad\text{s.t. selected intervals do not overlap}$$

Sort by end time, $p(j) = $ last non-overlapping index before $j$,
$DP[j] = \max\big(DP[j-1],\; S(c,j) + DP[p(j)]\big)$. $O(n\log n)$, exact,
about fifteen lines. Intervals are travel-inflated.

**Trigger threshold, derived.** Expected pairwise conflicts among $n$ candidates
in a day of length $D$ with mean travel-inflated duration $\bar d$ is roughly
$\binom{n}{2}\cdot 2\bar d/D$. Setting that to one:

$$n < 1 + \sqrt{D/\bar d}$$

For $D = 8$h, $\bar d = 2.5$h that is $n < 2.8$ — conflicts appear at the
**fourth candidate per coordinator per day**, or about **>10 open visits per
coordinator per week**. ESD is already at or past that, and the DP is cheaper to
write than the analysis for skipping it. It is implemented.

### Problem B — whole team, whole week

$$\max_x \sum_c\sum_v S(c,v)x_{cv} - \Pi\sum_v\Big(1-\sum_c x_{cv}\Big)$$
$$\text{s.t. } \sum_c x_{cv}\le 1;\quad \sum_v d_v x_{cv}\le \mathrm{Cap}_c;\quad x_{cv}\le F(c,v);\quad \text{no-overlap}(c,\text{day})$$

| | Hungarian | Min-cost flow | CP-SAT |
|---|---|---|---|
| More than one visit per coordinator | ✗ needs node cloning | ✓ native | ✓ |
| Unequal / part-time capacity | ✗ | ✓ | ✓ |
| Leave a visit unfilled | ✗ dummy padding | ✓ slack arc, cost Π | ✓ |
| Convex overtime cost | ✗ | ✓ parallel arcs | ✓ |
| **Intra-day time conflicts** | ✗ | **✗** | ✓ |

**Hungarian is the wrong shape** — it is the special case $\mathrm{Cap}_c = 1$,
$|C| = |V|$, which ESD never satisfies. Min-cost flow is the right relaxation
and fits the capacity structure exactly (transportation problem, totally
unimodular, so the LP relaxation is already integral). What it cannot express is
intra-day overlap — which is precisely Problem A.

So the implementation solves them **together**: the flow allocates at
(coordinator, day) granularity, the DP checks every bucket it filled, and any
bucket the DP cannot honour has its capacity cut and the flow re-solved. That is
a logic-based Benders loop; at this scale it settles in one or two rounds.
`optimize.mcmf_plan` does this in pure stdlib — no OR-Tools dependency. If the
lab later wants richer constraints (transition-time matrices, explicit fairness
objectives), CP-SAT is the natural upgrade and subsumes both problems.

**Escalation is measured, not argued.** The optimiser runs in **shadow mode**
from day one: it computes, it logs to `optimizer_shadow`, and it changes
nothing.

$$\text{regret}_t = \frac{\sum S^{\text{opt}} - \sum S^{\text{greedy}}}{\sum S^{\text{opt}}}$$

Escalate to the optimiser in production when regret > 3% **or** the unfilled gap
≥ 1, for two consecutive weeks. On the synthetic pilot week: greedy 4.59 vs
optimal 5.03, **regret 8.8%**, unfilled gap 0 — already over the threshold on
week one.

**The free intermediate fix, already in production greedy:** process visits
**most-constrained-first** (ascending feasible-pool size). One line. Greedy's
worst failure is burning a scarce credential — the only ADOS-certified
coordinator — on a visit any generalist could take, and this captures much of
the optimiser's gain at zero cost.

---

## 3. Fairness as a constraint

Encoding "nobody should always draw the long drives" as a weighted criterion
makes it tradeable against everything else, which is exactly what fairness is
supposed to prevent. So it lives in `engine.fairness_violations` as a veto:

- reject if the assignment would push utilisation above 1.0
- reject a **long drive** for someone already over their travel share: the
  prospective 4-week travel share exceeds 1.4 × their capacity share **and** the
  trip is longer than the team's typical recent trip

The second rule went through two wrong versions before it worked, and both
failures are worth recording because they look correct in code review.

*Veto anyone already over their share* is a **ratchet**. Every visit is refused,
including the short ones that would bring their average down, so the only escape
is waiting for the rolling window to move. In the first pilot run it starved one
coordinator of all work for a week.

*Veto only if this trip pushes the share up* fails the opposite way. If one
person holds nearly all the recent travel their share is already ≈1.0, no
further trip can raise it, and the cap silently stops firing exactly when it is
most needed.

Comparing the trip against the team's typical trip avoids both. It also states
in one sentence a coordinator can act on: **when you are over your share of the
driving, you keep getting work, but you stop getting the long drives.**

The cap will not fire at all below `travel_cap_min_trips` (8) logged trips across
`travel_cap_min_coordinators` (3) people in the window. A constraint that can
deny someone work must not fire on two or three observations, where the "typical
trip" is just whatever the last person happened to drive.

A veto is a **system event**, not a human override. The audit log records it as
`system_constraint_veto` with class `system`, so it never inflates the override
rate — which is the headline signal for whether the weights are wrong.

---

## 4. Weight validation

Restructuring first is what makes this cheap: four non-redundant criteria need
six pairwise comparisons, three of which are near-automatic, so it is a
twenty-minute meeting rather than an afternoon.

**AHP** — Saaty 1–9, individually (not by group consensus, which anchors on the
PI), principal eigenvector, $CR = CI/RI < 0.10$ with $RI_4 = 0.90$. Aggregate by
the **geometric mean of the judgments (AIJ)**, not the arithmetic mean of the
resulting weights: the geometric mean preserves reciprocity, the arithmetic mean
does not. `python -m esd_scheduler ahp judgments.json`.

**Fuzzy extension, only if inter-rater spread exceeds 2×** — triangular fuzzy
numbers, **Buckley's fuzzy geometric mean**, centroid defuzzification.
Explicitly **avoid Chang's extent analysis**: it is known to assign zero weight
to non-dominated criteria, a documented failure mode that would silently delete
a criterion here.

**DEMATEL** — if the team wants the interdependence structure.
$N = Z/\max_i\sum_j z_{ij}$, $T = N(I-N)^{-1}$, prominence $r+c$ as the weight
basis, relation $r-c$ as the **redundancy diagnostic**: a criterion with strongly
negative $r-c$ is an *effect* driven by the others and should not carry
independent weight. That is the empirical test for whether v2's recency was ever
more than a lagging shadow of family history and workload.

**Revealed preference — the best long-run answer, and free.** Every scheduling
decision is a discrete choice over the feasible pool. Fit McFadden's conditional
logit:

$$\Pr(c \mid \mathcal{C}_v) = \frac{\exp(\beta^\top z_{cv})}{\sum_{c'}\exp(\beta^\top z_{c'v})}$$

Normalised $\hat\beta$ = the weights the schedulers actually use. Needs ~50–100
decisions with ≥3 alternatives. **This is why the audit log records the whole
feasible pool rather than only the winner** — logging just the chosen
coordinator makes this permanently impossible, and it costs nothing to do it
right on day one.

**Sensitivity, three layers** (`python -m esd_scheduler sensitivity`):

1. **OAT** — each weight ± 0.05, others renormalised, every logged decision
   replayed. Reports rank-reversal rate and mean Kendall τ. Above ~15% reversal
   on a single 0.05 nudge, the weights are not identified at the precision the
   ranking is being presented with; widen the band rather than pretend.
2. **Criticality** (Triantaphyllou–Sánchez) — the smallest weight change that
   flips the top pick, per decision per criterion, by bisection. If the median
   flip distance is under the review band, present the top two as a tie band.
3. **Global Monte Carlo** — Dirichlet over the simplex, giving the per-decision
   selection stability and the calibrated $\varepsilon^\star$.

Plus a **redundancy check**: pairwise Spearman across criterion values,
$|\rho| > 0.6$ flags a merge candidate. On the synthetic pilot, Φ vs P came back
at $\rho = +0.597$ — just under the line, and worth watching: if real data pushes
it over, protocol continuity should fold into Φ or become a Layer 1 constraint.

**Timing: run the elicitation at the end of pilot week 3.** Enough shared
experience for the judgments to mean something, enough runway to act.

---

## 5. Tracking layer

### Audit log schema

Five tables, append-only. No UPDATE, no DELETE; corrections are new rows. Every
row carries `weight_vector_id` and `config_fingerprint`, so any past decision can
be replayed under new weights without re-querying a single calendar.

**`scoring_run`** — one row per scoring event (21 columns)

| Column | Type | Purpose |
|---|---|---|
| `run_id` | TEXT PK | Immutable handle for the decision |
| `visit_id`, `family_id`, `protocol`, `checkpoint` | TEXT | What was being scheduled |
| `family_sigma` | INTEGER | Continuity direction in force |
| `scored_at`, `visit_start`, `visit_end`, `visit_duration_hr` | TEXT / REAL | Anchors every recency computation and interval replay |
| `weight_vector_id`, `config_fingerprint`, `scoring_code_version` | TEXT | Separates a weight change from a code change |
| `pool_size_total`, `pool_size_feasible` | INTEGER | Layer 1 attrition |
| `pool_starvation` | INTEGER | **≤1 feasible candidate**: the most under-noticed failure, since the score is irrelevant when there is one option |
| `most_constrained_rank` | INTEGER | Position in the constrained-first ordering |
| `optimizer_mode`, `epsilon_used`, `halt_reason` | TEXT / REAL | Reproducibility |
| `surprise_codes` | TEXT (JSON) | Which detector rules fired |

**`candidate_score`** — one row per **coordinator in the pool**, feasible or not (48 columns)

| Group | Columns | Purpose |
|---|---|---|
| Identity | `candidate_id`, `run_id`, `coordinator_id`, `coordinator_name` | |
| Layer 1 | `l1_window_match`, `l1_open_slot`, `l1_no_calendar_clash`, `l1_no_family_conflict`, `l1_credential_match`, `l1_calendar_fresh`, `l1_ramp_ok`, `l1_pass`, `l1_fail_reason`, `missing_credentials` | Answers "why wasn't Sanjana offered?" in one query |
| Calendar | `calendar_status`, `calendar_cache_age_s`, `soft_flags`, `slot_start`, `slot_end` | SLO panel; correlates errors with cache age |
| Raw inputs | `k_prior_visits`, `days_since_family_contact` (**NULL when k=0, never 0**), `committed_hours`, `capacity_hours`, `travel_minutes`, `burden_hours`, `utilization`, `prior_checkpoint_flag`, `n_c_total_visits`, `is_cold_start` | Full replay without re-querying anything |
| Derived | `phi_continuity`, `phi_raw_R`, `omega_preference`, `psi_burden_relief`, `p_checkpoint` | |
| Contributions | `contrib_phi`, `contrib_omega`, `contrib_psi`, `contrib_p`, `final_score`, `score_int_scaled` | Powers the override waterfall; the integer cost is logged because rounding can flip near-ties |
| Layer 3 | `rank_position`, `gap_to_next`, `review_band_flag`, `in_shortlist`, `selection_stability`, `tie_break_applied`, `tie_break_rule`, `tie_break_seed` | The seed makes the randomised-tie experiment valid |

**`assignment_outcome`** — one row per run (16 columns)

| Column | Type | Purpose |
|---|---|---|
| `assigned_coordinator_id`, `assigned_rank` | TEXT / INTEGER | NULL = unfilled |
| `was_override` | INTEGER | **Human** overrode the ranking |
| `override_reason_code` | TEXT | Closed vocabulary |
| `override_reason_class` | TEXT | `data_defect` \| `preference` \| `external` \| `system` — **the split that matters** |
| `override_reason_text`, `overridden_by` | TEXT | Accountability |
| `is_provisional`, `confirmed_at` | INTEGER / TEXT | Stale-calendar path |
| `write_time_conflict` | INTEGER | Commit-time recheck caught a race |
| `visit_completed`, `no_show`, `protocol_deviation`, `family_satisfaction` | INTEGER | Outcome linkage: the only thing that tests whether $w_\Phi$ buys anything real |

`data_defect` overrides go to the bug queue. `preference` overrides go to weight
re-elicitation. Counting them together is how a scoring system quietly rots.

**`weight_vector`** — versioned config with `elicitation_method`,
`consistency_ratio`, `n_respondents`, `approved_by` and the full config JSON.
Weights become an auditable artefact, and the rule "changes only at a version
boundary" becomes enforceable.

**`calendar_sync_log`** — provider, sync type, latency, success, error code.
Powers the SLO panel and root-causes `calendar_data_wrong` overrides.

**`optimizer_shadow`** — weekly greedy vs optimal, regret, unfilled gap,
escalate flag.

### Drift metrics, weekly

**Fairness.** CV and max/mean load-imbalance ratio on capacity-normalised
utilisation are the headline numbers. Gini is reported with a bootstrap 95%
interval attached: it is badly biased and high-variance below about ten units,
and ESD has four to eight coordinators, so a swing from 0.11 to 0.19 is noise.
Coordinators with **zero** visits appear as zero rows — a missing row reads as
"not on the team", and the zero row *is* the fairness signal.

A **permutation test** answers the question that matters when the numbers look
uneven: randomly reassign each visit among the coordinators who were actually
eligible for it and compare the CV. High p means the imbalance comes from who was
eligible, not from the scoring — a constraints conversation, not a weights one.
On the synthetic pilot: CV 1.06 (RED), permutation p = 0.011, so that imbalance
is *not* explained by eligibility, and it needs acting on.

**Decision quality.** Review-band rate, tie rate, human override rate, system
veto rate, top-1 acceptance, **top-3 hit rate** (target > 90% — below that the
model is missing a criterion the humans are using, and the override reasons
usually name it), and shadow regret.

**Distribution shift.** PSI on the final score and each component vs the week-1
baseline (> 0.10 investigate, > 0.25 act); mean top-1 score; mean top-two gap;
share of decisions with an **inert criterion** (every feasible candidate pinned
to the same boundary value, so the criterion contributed nothing — counting
individual candidates at a boundary instead would flag every coordinator who has
simply never met the family, which is normal).

**Cold start and data health.** Share of assignments to coordinators below
$N_{\min}$ vs their capacity share; calendar sync success rate; median cache age
at scoring; provisional rate; write-time conflicts caught.

**Outcome linkage.** No-show, completion, protocol deviation and family
satisfaction, stratified by continuity level — with randomised tie-breaking
supplying the unconfounded arm. Flag to the IRB as a QI activity.

### The weekly debrief

`python -m esd_scheduler debrief` writes markdown and branded HTML with eight
sections: header with version fingerprints, fairness panel, exception register,
surprise log, override waterfalls, drift panel, decisions and actions, and the
standing question.

**Surprises are detected, not remembered.** Named rules:
`HUMAN_OVERRODE_TOP`, `WEAK_BEST_OPTION`, `INSIDE_REVIEW_BAND`,
`LOW_SELECTION_STABILITY`, `CRITERION_INERT`, `TOP_PICK_OVER_CAPACITY`,
`UNVERIFIED_AVAILABILITY`, `POOL_STARVATION`, `NO_FEASIBLE_CANDIDATE`,
`UNEXPLAINED_SCORE_SHIFT`.

**Waterfalls turn anecdote into data.** Every override renders as, for example:
*"V011: suggested C03, chose C06 (calendar_data_wrong / data_defect). C03 led on
continuity +0.113; C06 led on burden relief +0.006; C03 led on protocol
continuity +0.100; net −0.207."*

**Section 8 asks the team out loud: did any assignment feel wrong without showing
up in sections 2 or 3?** That is the only available check on the detector's
false negatives, and it is why the debrief is a live conversation rather than an
emailed PDF.

---

## 6. Running it

```bash
make init        # write config/engine.json, create the append-only audit db
make demo        # synthetic lab: 7 coordinators, 12 families, 16 visits
make test        # 25 correctness anchors incl. the hand-computed reference case
make week        # greedy vs optimiser with measured regret
make debrief     # reports/debrief-<week>.md and .html
```

### Automation

```bash
make install-automation      # ./automation/install.sh --dry to preview first
```

Four user-level launchd agents, no admin rights, nothing touched outside the
project directory:

| Job | Schedule | What it does |
|---|---|---|
| `calsync` | every 5 min | Delta pull; keeps the ≤72 h freshness class inside its 15 min hard threshold with room to spare |
| `reconcile` | nightly 02:00 | Full reconcile plus an append-only integrity check (a shrinking row count means something outside this tool touched the database) |
| `shadow` | Monday 06:45 | Shadow optimiser, records regret before the debrief renders |
| `debrief` | Monday 07:00 | Drift + debrief, ahead of the lab meeting |

Removal: `make uninstall-automation`. It leaves `data/`, `reports/` and `logs/`
in place — the audit log outlives the automation.

The debrief job **reports** a recalibrated review band but never writes it.
Weights and bands change only at a version boundary, with a human approving.

### Live calendars

Put credentials in `config/calendar.env` (gitignored):

```bash
ESD_CAL_PROVIDER=msgraph        # or google
ESD_CAL_TOKEN=<bearer token>
ESD_CAL_MAP=config/calendar-map.json   # coordinator_id -> mailbox / calendarId
```

Graph needs an app registration with `Calendars.Read` (application permission).
`token_provider` is a callable, so token refresh is the operator's to handle.
Note that Google `freeBusy` returns busy intervals with no status detail, so
every block is read as a hard `busy` — the safe reading, and a reason to prefer
Graph where the lab has a choice.

### Real data

Swap the demo builder for a loader producing the same `LabState`. The nine
inputs are: coordinators, families, protocols, availability, busy blocks,
exclusions, visit history, travel minutes, open visits. Everything downstream is
unchanged.

---

## 7. Roadmap

**P0 — before pilot week 1 (done in this build)**

| Item | Status |
|---|---|
| Log the whole feasible pool, not just the winner | ✅ `store.record_pool` |
| Capacity-referenced normalisation, drop pool min–max | ✅ |
| $k=0 \Rightarrow R=0$; delete coordinator-level idle time | ✅ |
| Calendar write-time recheck on commit | ✅ |
| Stale calendar ⇒ Layer 1 fail / provisional, never a score penalty | ✅ |
| Most-constrained-first greedy ordering | ✅ |
| Onboarding cap as a Layer 1 constraint | ✅ |

**P1 — pilot weeks 1–3 (done in this build)**

Continuity index Φ; burden merge Ψ with γ; capacity ramp; interval DP;
shadow optimiser with regret; automated weekly debrief with the surprise
detector; travel-equity constraint; redundancy diagnostics.

**P2 — pilot weeks 3–6 (tooling ready, needs the humans)**

| Item | Owner |
|---|---|
| Elicit γ: one question to the team | scheduler, week 1 |
| AHP elicitation, 6 comparisons, CR < 0.10 | PI + team, end of week 3 |
| Run the sensitivity suite on real logged decisions | analyst |
| Recalibrate $\varepsilon^\star$ and `weak_best_score` from the real distribution | analyst |
| Set `capacity_hours_week` per coordinator from actual FTE | lab manager |
| Decide whether Φ vs P at ρ ≈ 0.6 warrants a merge | PI |

**P3 — post-pilot**

CP-SAT in production, gated on shadow regret > 3% for two consecutive weeks
(the synthetic week already shows 8.8%, so this is likely). Conditional-logit
revealed-preference weights once 50–100 decisions are logged. Randomised
tie-break outcome linkage. A learned travel-time model with time-of-day effects.

**Explicit non-recommendations:** Hungarian algorithm (wrong shape); Chang's
extent analysis for fuzzy AHP (documented zero-weight failure); ANP (overkill at
four criteria); Gini as the headline fairness metric below n = 10.

---

## 8. Known limits

- **The weights are not yet validated.** Everything else is machinery for fixing
  that; until §4 runs, treat rankings as a shortlist, not a decision.
- **γ = 2 is a placeholder** until the team answers the one question.
- **`weak_best_score` = 0.20 is a placeholder** until the real score
  distribution is known.
- **The synthetic lab is not the real lab.** Its capacity shortfall and its
  RED fairness reading are properties of the generator, and they demonstrate the
  monitoring works rather than describing ESD.
- **PHI.** `candidate_score` holds family-linked scheduling data. Keep it in the
  identified-data environment, not alongside de-identified REDCap exports, and
  agree an explicit retention window with the IRB (suggested: pilot + 2 years).

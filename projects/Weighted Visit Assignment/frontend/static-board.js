/* Static mode: the same board, with no Python behind it.
 *
 * The public build has no server, so this file answers the same routes the
 * backend does, from a snapshot the real engine produced (board.json, written
 * by backend/build_static.py). Nothing here invents a ranking: every score,
 * exclusion and notice is read from the snapshot.
 *
 * The single quantity recomputed is the burden term, because assigning a visit
 * changes a coordinator's committed hours and the board would otherwise keep
 * recommending someone who has just filled up. This mirrors
 * esd_scheduler/scoring.py and is pinned against the snapshot by
 * tests/test_static_board.py.
 */
"use strict";

window.StaticBoard = (function () {
  let DATA = null;
  const assignments = {};   // visit_id -> record
  const activity = [];

  const W = () => DATA.meta.weights;
  const clamp01 = (x) => Math.min(1, Math.max(0, x));

  function prospectiveBurden(committed, duration, travelMinutes) {
    return committed + duration + (DATA.meta.gammaTravel * travelMinutes) / 60;
  }
  function burdenRelief(burden, capacity) {
    return capacity <= 0 ? 0 : 1 - clamp01(burden / capacity);
  }

  function extraHours() {
    const out = {};
    for (const [visitId, a] of Object.entries(assignments)) {
      const v = DATA.visits.find((x) => x.visit.id === visitId);
      if (v) out[a.coordinator_id] = (out[a.coordinator_id] || 0) + v.visit.duration_hours;
    }
    return out;
  }

  function personById(id) {
    return DATA.roster.find((r) => r.id === id);
  }

  /** Candidates re-scored against the work this board has created. */
  function rankRows(visitId) {
    const base = DATA.visits.find((v) => v.visit.id === visitId);
    if (!base) return null;
    const extra = extraHours();

    const rows = base.candidates.map((c) => {
      const person = personById(c.id);
      const added = extra[c.id] || 0;
      const byKey = {};
      c.contributions.forEach((x) => (byKey[x.key] = x));

      let psi = byKey.psi.value;
      let util = c.utilization;
      if (added > 0) {
        // Effective capacity: the onboarding ramp is part of what the engine
        // scored against, so the recompute must use the same denominator.
        const burden = prospectiveBurden(
          person.committed_hours + added, base.visit.duration_hours, c.travel_minutes);
        psi = burdenRelief(burden, person.effective_capacity_hours);
        util = burden / person.effective_capacity_hours;
      }
      const contribution = {
        phi: W().phi * byKey.phi.value,
        omega: W().omega * byKey.omega.value,
        psi: W().psi * psi,
        p: W().p * byKey.p.value,
      };
      const score = clamp01(
        contribution.phi + contribution.omega + contribution.psi + contribution.p);
      const contributions = ["phi", "omega", "psi", "p"].map((k) => ({
        key: k, label: byKey[k].label, help: byKey[k].help,
        value: k === "psi" ? psi : byKey[k].value,
        weight: byKey[k].weight, contribution: contribution[k],
      }));
      const lead = contributions.reduce((a, b) => (b.contribution > a.contribution ? b : a));
      const overCapacity = util > 1;
      return Object.assign({}, c, {
        score, contributions, utilization: util,
        leads_on: lead.label,
        blocked_by: overCapacity
          ? ["Already at their capacity for the week"]
          : c.blocked_by.slice(),
        assignable: !overCapacity && c.blocked_by.length === 0,
      });
    });

    rows.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
    rows.forEach((r, i) => {
      r.rank = i + 1;
      r.review_band = false;
    });
    if (rows.length >= 2 && rows[0].score - rows[1].score < DATA.meta.reviewBand) {
      rows[0].review_band = true;
      rows[1].review_band = true;
    }
    return { base, rows, recommended: rows.find((r) => r.assignable) };
  }

  /** True when this visit will not resolve itself: nobody can go, or the top
   *  two are close enough that picking between them is a judgement call.
   *  Recomputed rather than read from the snapshot, because assigning work
   *  changes who is still free and the header counts these. */
  function needsAttention(visitId) {
    if (assignments[visitId]) return false;
    const summary = DATA.visits.find((v) => v.visit.id === visitId);
    if (summary && summary.visit.automated === false) return false;
    const r = rankRows(visitId);
    if (!r) return false;
    return !r.recommended || Boolean(r.rows.length >= 2 && r.rows[0].review_band);
  }

  function visitDetail(visitId) {
    const r = rankRows(visitId);
    if (!r) return null;
    return Object.assign({}, r.base, {
      visit: visitSummary(visitId),
      candidates: r.rows,
      recommended_id: r.recommended ? r.recommended.id : null,
      top_rank_blocked: Boolean(r.rows.length && !r.rows[0].assignable && r.recommended),
      review_band: DATA.meta.reviewBand,
      close_call: Boolean(r.rows.length >= 2 && r.rows[0].review_band),
      assigned: assignments[visitId] || null,
    });
  }

  function visitSummary(visitId) {
    const base = DATA.visits.find((v) => v.visit.id === visitId).visit;
    const a = assignments[visitId];
    return Object.assign({}, base, {
      // base already carries route, contact method, notes, offer window and
      // duration from the snapshot; only the status-derived keys move here.
      status: a ? "assigned" : "needs_assignment",
      needs_attention: false,   // filled in by the queue, which has the rows
      assigned_to: a ? a.coordinator_name : null,
      assigned_id: a ? a.coordinator_id : null,
      provisional: Boolean(a && a.provisional),
      was_override: Boolean(a && a.override),
    });
  }

  function fairness() {
    const extra = extraHours();
    const rows = DATA.roster.map((r) => {
      const hours = r.committed_hours + (extra[r.id] || 0);
      return {
        id: r.id, name: r.name,
        visits: Object.values(assignments).filter((a) => a.coordinator_id === r.id).length,
        hours: Math.round(hours * 10) / 10,
        capacity: r.capacity_hours,
        utilization: Math.min(1.5, hours / Math.max(1e-6, r.capacity_hours)),
      };
    });
    const loads = rows.map((r) => r.utilization);
    const mean = loads.reduce((s, x) => s + x, 0) / (loads.length || 1);
    const imbalance = mean > 0 ? Math.max.apply(null, loads) / mean : 0;
    return {
      rows, imbalance: Math.round(imbalance * 100) / 100,
      cv: 0, permutation_p: 1,
      assigned: Object.keys(assignments).length, total: DATA.visits.length,
      override_rate: 0,
      status: imbalance < 1.4 ? "even" : imbalance < 1.9 ? "uneven" : "lopsided",
    };
  }

  function log(message) {
    activity.unshift({
      at: new Date().toTimeString().slice(0, 5), message,
    });
    activity.length = Math.min(activity.length, 40);
  }

  function assign(visitId, coordinatorId, reasonCode, reasonText) {
    if (assignments[visitId]) throw new Error("This visit is already assigned.");
    const detail = visitDetail(visitId);
    const chosen = detail.candidates.find((c) => c.id === coordinatorId);
    if (!chosen) throw new Error("That coordinator is not eligible for this visit.");
    if (!chosen.assignable) throw new Error(chosen.blocked_by.join("; "));
    const isOverride = detail.recommended_id !== null && coordinatorId !== detail.recommended_id;
    if (isOverride && !reasonCode) {
      throw new Error("Choosing past the recommendation needs a reason. "
        + "An unexplained override is a lost data point.");
    }
    const code = DATA.reasonCodes.find((r) => r.code === reasonCode);
    if (reasonCode && !code) throw new Error("Unknown reason code.");

    assignments[visitId] = {
      coordinator_id: chosen.id, coordinator_name: chosen.name,
      rank: chosen.rank, score: chosen.score,
      override: isOverride,
      reason_code: isOverride ? reasonCode : null,
      reason_class: isOverride && code ? code.cls : null,
      provisional: chosen.provisional, slot: chosen.slot, notes: [],
    };
    const label = detail.visit.family_label;
    log(isOverride
      ? `${label}: chose ${chosen.name} over the recommendation (${reasonCode}).`
      : `${label}: assigned ${chosen.name}.`);
    return assignments[visitId];
  }

  function unassign(visitId) {
    if (!assignments[visitId]) return;
    const label = visitSummary(visitId).family_label;
    delete assignments[visitId];
    log(`${label}: assignment undone.`);
  }

  function reset() {
    Object.keys(assignments).forEach((k) => delete assignments[k]);
    activity.length = 0;
    log("Board reset.");
  }

  /* Same routes the Python backend serves, so app.js is unchanged. */
  function route(path, opts) {
    const [base, query] = path.split("?");
    const params = {};
    (query || "").split("&").forEach((p) => {
      const [k, v] = p.split("=");
      if (k) params[decodeURIComponent(k)] = decodeURIComponent(v || "");
    });
    const body = opts && opts.body ? JSON.parse(opts.body) : {};

    switch (base) {
      case "/api/board":
        return {
          health: DATA.meta.health, roster: DATA.roster,
          queue: DATA.visits.map((v) => Object.assign(visitSummary(v.visit.id), {
            needs_attention: needsAttention(v.visit.id),
          })),
          fairness: fairness(), reason_codes: DATA.reasonCodes,
          activity: activity.slice(0, 12),
        };
      case "/api/visit": {
        const d = visitDetail(params.id);
        if (!d) throw new Error(`No visit ${params.id}.`);
        return d;
      }
      case "/api/assign":
        return { assignment: assign(body.visit_id, body.coordinator_id,
                   body.reason_code || null, body.reason_text || null),
                 visit: visitSummary(body.visit_id) };
      case "/api/unassign":
        unassign(body.visit_id);
        return { visit: visitSummary(body.visit_id) };
      case "/api/reset":
        reset();
        return { ok: true };
      default:
        throw new Error(`No route ${base}`);
    }
  }

  return {
    async load() {
      const res = await fetch("board.json", { cache: "no-cache" });
      DATA = await res.json();
      log("Board ready. Demonstration data from the scheduling engine.");
      return DATA;
    },
    route,
  };
})();

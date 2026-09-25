"""Auditable payment safeguards around the unchanged, versioned baseline bot.

Candidate research rules are deliberately not inputs to this classification.
Archive records remain in the private source ledger, outside the denominator.
Other policy exclusions remain visible as one row per applicable respondent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

import pandas as pd

from refined_screening import RULES, COMPLETION_FIELDS, _choices, _code, _plain

EXCLUDE = "EXCLUDE"
HOLD = "HOLD FOR MANUAL REVIEW"
VERIFY = "ELIGIBLE — ADDITIONAL REVIEW NEEDED"
PAY = "PAY NOW"
UNABLE = "UNABLE TO CLASSIFY"
LABELS = [EXCLUDE, HOLD, VERIFY, PAY, UNABLE]
CURRENT_LABELS = {"Pay now": PAY, "Check by hand": HOLD,
                  "Do not pay": EXCLUDE, "Incomplete - not eligible": UNABLE}
MISSING = {"", "nan", "none", "null", "<na>", "na", "n/a", "unk", "ni", "nask", "asku", "msk"}
GATES = {
    "EX_ARCHIVE_5749": ("PID 5749 archived clone", "PID == 5749 AND numeric Record ID in [1,445]", "Outside analysis", "Exclude from applicable denominator; preserve private source ledger"),
    "EX_PRELAUNCH_5749": ("PID 5749 before launch", "PID == 5749 AND numeric Record ID in [446,473]", "Hard policy exclusion", EXCLUDE),
    "EX_TEST_WORD": ("Standalone test in free text", "Case-insensitive regex \\btest\\b in any metadata text/notes field, before language folding", "Hard policy exclusion", EXCLUDE),
    "G_SOURCE": ("Unresolved source mapping", "Source mapping issue == 1 OR source project not verified", "Manual review", HOLD),
    "G_COMPLETE": ("Incomplete required sections", "Any of the four required section completion codes != 2, including missing", "Missing critical information", UNABLE),
    "G_CONTACT": ("Payment contact needs verification", "Unusable/blank email OR temporary domain OR confirmation mismatch OR shared nonempty email across studies", "Verification", VERIFY),
    "G_INDEPENDENT": ("Multiple independent evidence families", "At least two of timing, diverse copying, impossible child count, answered attention failure without accessibility issue", "Manual review", HOLD),
    "G_REVIEW": ("Unresolved substantive or data concern", "R1 OR R2 OR R5 OR R7 OR R8 OR R9 OR R13 OR R15 OR source issue OR negative section time OR (complete AND no valid section times) OR (complete AND available attention unanswered)", "Manual review", HOLD),
    "G_ELIGIBILITY_REQUIRED": ("Missing required eligibility answer", "Any visible metadata-required eligibility field missing/invalid, or its visibility cannot be evaluated; both age_elig and children_elig must have valid metadata codes", "Manual review", HOLD),
    "G_ELIGIBILITY_RESPONSE": ("Declared eligibility needs adjudication", "Metadata-decoded age_elig == No OR children_elig == No", "Manual review", HOLD),
    "G_REVISED_POLICY": ("Approved-rule classification precedence", "Hard policy exclusion > source/declared ineligibility/substantive concern > incomplete data > required eligibility missing > contact verification > Pay Now; unapproved candidates never alter classification", "Classification gate", "Apply final precedence; multiple risk families require adjudication, not automatic fraud exclusion"),
}


def norm(value):
    if value is None or pd.isna(value):
        return ""
    text = _code(str(value).strip())
    return "" if text.casefold() in MISSING else text


def visible(logic, row, *, folded_language=False):
    """Small, fail-closed REDCap equality/inequality/AND/OR parser (no eval).

    Unknown syntax returns None. After validated bilingual folding, language
    gates on canonical English fields represent the respondent's chosen form.
    """
    if not str(logic).strip():
        return True
    pattern = r"\s*(\[[A-Za-z_][A-Za-z_0-9]*\]|'[^']*'|\"[^\"]*\"|<>|!=|>=|<=|=|>|<|\(|\)|\band\b|\bor\b|[0-9.]+)"
    tokens, offset = [], 0
    while offset < len(logic):
        match = re.match(pattern, logic[offset:], re.I)
        if not match:
            if not logic[offset:].strip():
                break
            return None
        tokens.append(match[1]); offset += match.end()
    position = 0

    def atom():
        nonlocal position
        if tokens[position] == "(":
            position += 1
            answer = disjunction()
            if tokens[position] != ")": raise ValueError("Unclosed branch")
            position += 1
            return answer
        field, operator, value = tokens[position:position+3]
        position += 3
        if not field.startswith("["): raise ValueError("Unsupported branch operand")
        field = field[1:-1]
        if folded_language and field == "english_spanish": return True
        if field not in row: return None
        left, right = norm(row[field]), norm(value.strip("'\""))
        if operator == "=": return left == right
        if operator in {"!=", "<>"}: return left != right
        if not left or not right: return None
        a, b = float(left), float(right)
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[operator]

    def conjunction():
        nonlocal position
        value = atom()
        while position < len(tokens) and tokens[position].lower() == "and":
            position += 1; other = atom()
            value = False if value is False or other is False else (None if value is None or other is None else True)
        return value

    def disjunction():
        nonlocal position
        value = conjunction()
        while position < len(tokens) and tokens[position].lower() == "or":
            position += 1; other = conjunction()
            value = True if value is True or other is True else (None if value is None or other is None else False)
        return value
    try:
        answer = disjunction()
        return answer if position == len(tokens) else None
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def eligibility_status(row, metadata, pid):
    meta = {r["field_name"]: r for r in metadata.to_dict("records")}
    missing, ineligible, unknown = [], [], []
    for field in ("age_elig", "children_elig"):
        if field not in meta:
            raise ValueError(f"PID {pid}: cannot verify mandatory eligibility field {field}")
        value = norm(row.get(field))
        label = _plain(_choices(meta[field]).get(value, "")).casefold()
        if label not in {"yes", "no", "sí", "si"}:
            missing.append(field)
        elif label == "no":
            ineligible.append(field)
    for field, definition in meta.items():
        if definition.get("form_name") != "eligibility" or str(definition.get("required_field", "")).lower() != "y":
            continue
        if definition.get("field_type") in {"calc", "descriptive"} or "@CALCTEXT" in str(definition.get("field_annotation", "")).upper():
            continue
        is_visible = visible(str(definition.get("branching_logic", "")), row, folded_language=int(pid) == 5749)
        if is_visible is False: continue
        if is_visible is None:
            unknown.append(field); continue
        choices = _choices(definition)
        if definition.get("field_type") == "checkbox":
            values = [norm(row.get(f"{field}___{code}")) for code in choices]
            answered = "1" in values and all(v in {"", "0", "1"} for v in values)
        else:
            value = norm(row.get(field))
            answered = bool(value) and (not choices or value in choices)
        if not answered: missing.append(field)
    return {"missing": sorted(set(missing)), "ineligible": sorted(set(ineligible)),
            "visibility_unknown": sorted(set(unknown))}


def revised_decision(*, hard=False, source=False, ineligible=False, review=False,
                     complete=True, eligibility_missing=False, contact=False):
    if hard: return EXCLUDE
    if source or ineligible or review: return HOLD
    if not complete: return UNABLE
    if eligibility_missing: return HOLD
    if contact: return VERIFY
    return PAY


def _link_key(project_dir):
    directory = Path(project_dir) / "Caregiver Outputs" / "restricted"
    directory.mkdir(parents=True, exist_ok=True); directory.chmod(0o700)
    path = directory / ".audit_hmac.key"
    if not path.exists():
        try:
            with path.open("xb") as handle: handle.write(secrets.token_bytes(32))
        except FileExistsError: pass
    path.chmod(0o600)
    key = path.read_bytes()
    if len(key) != 32: raise ValueError("Invalid audit linkage key")
    return key


def build_record_results(sources, baseline, *, project_dir, version, timestamp):
    key = _link_key(project_dir)
    rows, activity = [], []
    raw_by_key = {str(pid)+":"+norm(r.get("record_id")): r for pid in sources.registry
                  for r in json.loads((sources.snapshot_dir / f"{pid}_record.json").read_text())}
    def linkage(email):
        email = norm(email).lower()
        return "Not available" if not email else "hmac:" + hmac.new(key, email.encode(), hashlib.sha256).hexdigest()[:24]
    def add_activity(pid, rid, name, evaluated, triggered):
        activity.append({"Rule ID": name, "Study PID": pid, "Study name": sources.registry[pid]["study_name"],
                         "Response key": f"{pid}:{rid}", "Evaluated": bool(evaluated), "Triggered": bool(triggered)})
    for pid, records in sources.records_by_pid.items():
        lookup = records.set_index(records.record_id.map(norm))
        study = sources.registry[pid]["study_name"]
        for _, record in baseline.loc[baseline["REDCap PID"].eq(pid)].iterrows():
            rid = norm(record["Record ID"]); raw = lookup.loc[rid]
            eligibility = eligibility_status(raw, sources.metadata_by_pid[pid], pid)
            complete = record["Survey finished"] == "Yes"
            missing = eligibility["missing"] + [f"visibility:{f}" for f in eligibility["visibility_unknown"]]
            if not complete:
                missing += [f for f in COMPLETION_FIELDS if norm(record[f]) != "2"]
            flags = {r: bool(record[r]) for r in RULES}
            flags.update({"G_SOURCE": bool(record["Source mapping issue"]), "G_COMPLETE": not complete,
                          "G_CONTACT": bool(record["Payment contact hold"]),
                          "G_INDEPENDENT": record["Independent evidence families"] >= 2,
                          "G_REVIEW": bool(record["Review concern"]),
                          "G_ELIGIBILITY_REQUIRED": bool(eligibility["missing"] or eligibility["visibility_unknown"]),
                          "G_ELIGIBILITY_RESPONSE": bool(eligibility["ineligible"])})
            revised = revised_decision(source=flags["G_SOURCE"], ineligible=flags["G_ELIGIBILITY_RESPONSE"],
                review=flags["G_REVIEW"] or flags["G_INDEPENDENT"], complete=complete,
                eligibility_missing=flags["G_ELIGIBILITY_REQUIRED"], contact=flags["G_CONTACT"])
            current = CURRENT_LABELS[record["Recommended action"]]
            reasons = []
            if flags["G_SOURCE"]: reasons.append("Resolve source/language mapping before adjudication")
            if flags["G_ELIGIBILITY_RESPONSE"]: reasons.append("Declared No for " + ", ".join(eligibility["ineligible"]) + "; confirm eligibility")
            if flags["G_INDEPENDENT"]: reasons.append("Multiple integrity concerns require adjudication; risk signals do not establish bot identity")
            if flags["G_REVIEW"]:
                reasons += [RULES[r]["label"] for r in ["R1", "R2", "R5", "R7", "R8", "R9", "R13", "R15"] if flags[r]]
                if record["Timing data issue"]: reasons.append("Negative section time")
                if complete and record["Sections with a recorded time"] == 0: reasons.append("No valid section timings")
                if complete and record["Attention check available"] and not record["Attention check answered"]: reasons.append("Attention question unanswered")
            if flags["G_COMPLETE"]: reasons.append("Required survey sections incomplete")
            if flags["G_ELIGIBILITY_REQUIRED"]: reasons.append("Required eligibility missing/invalid or branching unresolved: " + ", ".join(eligibility["missing"] + eligibility["visibility_unknown"]))
            if flags["G_CONTACT"]: reasons.append("Verify payment contact or shared-email ownership before payment")
            if revised == PAY: reasons.append("Complete response; required eligibility answered; no active substantive concern; usable unique payment contact")
            flags["G_REVISED_POLICY"] = revised != PAY
            severity = {EXCLUDE:"Hard policy exclusion", HOLD:"Manual review", VERIFY:"Verification", PAY:"No payment blocker", UNABLE:"Missing critical information"}[revised]
            row = {"Study PID": pid, "Study name": study, "Record ID": rid,
                "Masked email or email hash": linkage(record["Email address"]),
                "Survey completion status": "Complete" if complete else "Incomplete",
                "Survey duration": record["Whole survey minutes"] if pd.notna(record["Whole survey minutes"]) else "Not available",
                "Current classification": current, "Revised classification": revised,
                "Classification changed": "Yes" if current != revised else "No",
                "Triggered rule IDs": "; ".join(r for r, flag in flags.items() if flag),
                "Highest severity": severity, "Plain-language reason": "; ".join(dict.fromkeys(reasons)),
                "Exclusion indicator": int(revised == EXCLUDE), "Manual-review indicator": int(revised in {HOLD, VERIFY}),
                "Payment indicator": int(revised == PAY), "Missing critical data": "; ".join(missing) or "None observed",
                "Reviewer status": "Research adjudication pending" if revised in {HOLD, VERIFY} else "Not independently adjudicated",
                "Reviewer notes": "", "Bot version": version, "Processing timestamp": timestamp,
                "Response key": f"{pid}:{rid}", "Current bot label": record["Recommended action"],
                "Current bot reason": record["Why this action"], "Survey start time": record["Started"] or "Not available",
                "Survey completion time": record["Last section handed in"] or "Not available",
                "Elapsed minutes from sign-up": record["Minutes from sign-up to last section"] if pd.notna(record["Minutes from sign-up to last section"]) else "Not available",
                "Email usable": record["Email usable"], "Shared payment contact count": record["Responses sharing payment email"],
                "Email confirmation mismatch": int(record["Email confirmation differs"]),
                "Unresolved contradiction count": int(record["Logical evidence"]) + int(record["R15"]),
                "Independent evidence families": int(record["Independent evidence families"]),
                "Required eligibility answered": "No" if flags["G_ELIGIBILITY_REQUIRED"] else "Yes",
                "Declared eligibility concern": "Yes" if flags["G_ELIGIBILITY_RESPONSE"] else "No"}
            for rule in RULES:
                row[f"{rule} evidence"] = str(record[f"{rule} evidence"])
                add_activity(pid, rid, rule, record[f"{rule} status"] in {"Flagged", "Clear"}, flags[rule])
            for gate in GATES:
                if gate not in {"EX_ARCHIVE_5749", "EX_PRELAUNCH_5749", "EX_TEST_WORD"}:
                    add_activity(pid, rid, gate, True, flags[gate])
            rows.append(row)
    # Policy exclusions are visible in final record results; archived clone is
    # explicitly outside the analysis rather than disappearing silently.
    for _, excluded in sources.exclusions.iterrows():
        pid, rid = int(excluded.project_id), norm(excluded.record_id)
        if bool(excluded.archive_clone): continue
        raw = raw_by_key[f"{pid}:{rid}"]
        flags = [rule for rule, yes in [("EX_PRELAUNCH_5749", excluded.prelaunch_protocol), ("EX_TEST_WORD", excluded.contains_test_word)] if yes]
        rows.append({"Study PID":pid, "Study name":sources.registry[pid]["study_name"], "Record ID":rid,
            "Masked email or email hash":linkage(raw.get("demo_email") or raw.get("email_elig") or raw.get("demo_email_s")),
            "Survey completion status":"Not evaluated (policy exclusion)", "Survey duration":"Not evaluated (policy exclusion)",
            "Current classification":EXCLUDE, "Revised classification":EXCLUDE, "Classification changed":"No",
            "Triggered rule IDs":"; ".join(flags + ["G_REVISED_POLICY"]), "Highest severity":"Hard policy exclusion",
            "Plain-language reason":str(excluded.exclusion_reason), "Exclusion indicator":1, "Manual-review indicator":0,
            "Payment indicator":0, "Missing critical data":"Not evaluated (policy exclusion)",
            "Reviewer status":"Policy applied; respondent validity not adjudicated", "Reviewer notes":"",
            "Bot version":version, "Processing timestamp":timestamp, "Response key":f"{pid}:{rid}",
            "Current bot label":"Excluded before scoring", "Current bot reason":str(excluded.exclusion_reason),
            "EX_TEST_WORD evidence":str(excluded.matched_text_fields), "Required eligibility answered":"Not evaluated",
            "Declared eligibility concern":"Not evaluated"})
        add_activity(pid, rid, "G_REVISED_POLICY", True, True)
    result = pd.DataFrame(rows).fillna("Not available")
    excluded_by_key = sources.exclusions.set_index("response_key")
    for row in result.to_dict("records"):
        response_key = row["Response key"]
        excluded = excluded_by_key.loc[response_key] if response_key in excluded_by_key.index else None
        for rule, flag in [("EX_ARCHIVE_5749", False),
                           ("EX_PRELAUNCH_5749", excluded is not None and bool(excluded.prelaunch_protocol)),
                           ("EX_TEST_WORD", excluded is not None and bool(excluded.contains_test_word))]:
            add_activity(row["Study PID"], row["Record ID"], rule, True, flag)
    return result, pd.DataFrame(activity)


def summary_tables(results, activity, specification):
    summary = []
    for pid, study in list(results.groupby(["Study PID", "Study name"], sort=False)) + [(("All studies", "All studies total"), results)]:
        current, revised = study["Current classification"], study["Revised classification"]
        summary.append({"Study PID":pid[0], "Study name":pid[1], "Total analyzed":len(study),
            "Current Pay Now":int(current.eq(PAY).sum()), "Revised Pay Now":int(revised.eq(PAY).sum()),
            "Current excluded":int(current.eq(EXCLUDE).sum()), "Revised excluded":int(revised.eq(EXCLUDE).sum()),
            "Current manual review":int(current.isin([HOLD, VERIFY]).sum()), "Revised manual review":int(revised.isin([HOLD, VERIFY]).sum()),
            "Classification changed":int(current.ne(revised).sum()), "Classification unchanged":int(current.eq(revised).sum()),
            "Net Pay Now change":int(revised.eq(PAY).sum()-current.eq(PAY).sum()),
            "Removed from Pay Now":int((current.eq(PAY)&revised.ne(PAY)).sum()),
            "Added to Pay Now":int((current.ne(PAY)&revised.eq(PAY)).sum()),
            "Current unable to classify":int(current.eq(UNABLE).sum()), "Revised unable to classify":int(revised.eq(UNABLE).sum())})
    transitions = []
    for (pid, name), frame in list(results.groupby(["Study PID", "Study name"], sort=False)) + [(("All studies", "All studies total"), results)]:
        matrix = pd.crosstab(frame["Current classification"], frame["Revised classification"]).reindex(index=LABELS, columns=LABELS, fill_value=0)
        matrix["Total"] = matrix.sum(axis=1); matrix.loc["Total"] = matrix.sum(axis=0)
        matrix = matrix.rename_axis("Current classification").reset_index()
        matrix.insert(0, "Study name", name); matrix.insert(0, "Study PID", pid)
        transitions.append(matrix)
    impact = []
    lookup = results.set_index("Response key")
    for spec in specification.to_dict("records"):
        rule = spec["Rule ID"]
        if spec.get("Implementation status") == "Not implemented": continue
        for (pid, name), frame in list(results.groupby(["Study PID", "Study name"], sort=False)) + [(("All studies", "All studies total"), results)]:
            hits = activity.loc[activity["Rule ID"].eq(rule)&activity["Triggered"]&activity["Response key"].isin(frame["Response key"]), "Response key"]
            hit = lookup.loc[hits]
            higher = hit["Revised classification"].eq(EXCLUDE) if not rule.startswith("EX_") else pd.Series(False, index=hit.index)
            impact.append({"Rule ID":rule, "Rule name":spec["Rule name"], "Study PID":pid, "Study name":name,
                "Trigger count":len(hits), "Unique records affected":len(hit),
                "Records removed from Pay Now":int((hit["Current classification"].eq(PAY)&hit["Revised classification"].ne(PAY)).sum()),
                "Records added to manual review":int((~hit["Current classification"].isin([HOLD,VERIFY])&hit["Revised classification"].isin([HOLD,VERIFY])).sum()),
                "Records excluded":int(hit["Revised classification"].eq(EXCLUDE).sum()),
                "Records unaffected because of higher-priority rule":int(higher.sum()),
                "Percentage of analyzed records affected":100*len(hit)/len(frame),
                "Attribution note":"Overlapping triggered-record association; not additive causal effect. Higher priority means a hard exclusion overrides this review signal."})
    return {10:pd.DataFrame(summary), 11:pd.concat(transitions,ignore_index=True), 12:pd.DataFrame(impact)}


def review_queue(results):
    queue = []
    for row in results.loc[results["Manual-review indicator"].eq(1)].to_dict("records"):
        flags = set(row["Triggered rule IDs"].split("; "))
        contradictions = int(row["Unresolved contradiction count"])
        if row["Current classification"] == PAY: priority, category = 1, "Previously Pay Now"
        elif contradictions >= 2: priority, category = 2, "Multiple contradictions"
        elif flags & {"G_ELIGIBILITY_REQUIRED", "G_ELIGIBILITY_RESPONSE"}: priority, category = 3, "Eligibility"
        elif flags & {"R5", "R7"} or int(row["Shared payment contact count"]) > 1: priority, category = 4, "Duplicate concern"
        elif flags & {"R1", "R2"} and flags & {"R4", "R13"}: priority, category = 5, "Timing and response quality"
        else: priority, category = 6, "Clarification or verification"
        fields = {"R1":"get_time_fif/get_time_val/get_time_tfa/get_time_demo", "R2":"get_time_tfa", "R5":"metadata-defined values/tfa ratings", "R7":"narrative fields", "R8":"fif_num_children/fif_num_autistic/fif_childrens_ages", "R9":"age_confirm_elig/demo_momdob/zip_demo", "R13":"elig_knee", "R15":"fif_child_needs/fif_num_autistic", "G_CONTACT":"hashed email quality/confirmation/shared count", "G_SOURCE":"source_mapping_notes", "G_ELIGIBILITY_REQUIRED":"metadata-required eligibility fields", "G_ELIGIBILITY_RESPONSE":"age_elig/children_elig"}
        queue.append({"Review priority":priority, "Study PID":row["Study PID"], "Study name":row["Study name"], "Record ID":row["Record ID"],
            "Current classification":row["Current classification"], "Proposed classification":row["Revised classification"],
            "Triggered rules":row["Triggered rule IDs"], "Number of contradictions":contradictions,
            "Reason for review":row["Plain-language reason"], "Evidence fields":"; ".join(fields[r] for r in fields if r in flags),
            "Recommended reviewer action":"Check original response and dictionary; document eligibility, integrity and payment-contact decision before release",
            "Final reviewer decision":"Pending", "Reviewer comments":"", "Priority category":category})
    return pd.DataFrame(queue).sort_values(["Review priority", "Number of contradictions", "Study PID", "Record ID"], ascending=[True,False,True,True], kind="stable").reset_index(drop=True)

"""Reproducible sixteen-table respondent-validity analysis and approval audit."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

import refined_screening as screening
from refined_sources import load_refined_sources, REFERENCE_FILE
from refined_export import formula_templates
from validity_classification import (GATES, LABELS, PAY, HOLD, VERIFY, EXCLUDE, UNABLE,
    build_record_results, summary_tables, review_queue)

PROJECT_DIR = Path(__file__).resolve().parent


def rule_specification(candidates, activity):
    conditions, _ = formula_templates()
    variables = {
        "R1":"get_time_fif, get_time_val, get_time_tfa, get_time_demo", "R2":"get_time_tfa",
        "R3":"get_time_fif, get_time_val, get_time_tfa, get_time_demo", "R4":"metadata-defined values/tfa rating blocks",
        "R5":"metadata-defined values/tfa rating vector", "R6":"eligibility_timestamp (within PID)",
        "R7":"metadata text/notes fields matching comment/explain/feedback/reason/firstsigns_what/ethic_details/whylater",
        "R8":"fif_num_autistic, fif_num_children, fif_childrens_ages; metadata-gated autism follow-ups",
        "R9":"age_confirm_elig, demo_momdob, eligibility_timestamp, demo_country, zip_demo, dob_child1",
        "R10":"demo_email or email_elig", "R11":"demo_email or email_elig; four section completion codes",
        "R12":"eligibility_timestamp", "R13":"elig_knee checkbox options from actual metadata",
        "R14":"pronouns_elig, demo_gender (metadata labels)", "R15":"fif_child_needs, fif_num_autistic (metadata labels)"}
    rows = []
    for rule, definition in screening.RULES.items():
        context = rule in {"R3", "R4", "R6", "R12", "R14"}
        action = "Context only; cannot block payment" if context else ("Payment contact verification" if rule in {"R10", "R11"} else "Manual review; never standalone proof of fraud")
        rows.append({"Rule ID":rule, "Rule name":definition["label"], "Rule category":"Existing implemented",
            "Study PID":"All applicable PIDs", "Study name":"Study-specific metadata and applicability",
            "Plain-language description":definition["description"], "Variables used":variables[rule],
            "Exact logical condition":conditions[rule], "Intended condition":definition["description"],
            "Missing-value handling":"Not evaluable or not applicable; never silently interpreted as a passed answer. Missing completion/contact/required eligibility have separate gates.",
            "Severity":"Context" if context else "Verification" if rule in {"R10", "R11"} else "Manual review",
            "Final action":action, "Approval status":"Approved for implementation",
            "Approval provenance":"Existing user-authorized screening logic; formal Jessica adjudication not documented",
            "Implementation status":"Implemented", "Potential defect":"Universal timing cutoffs need study-specific calibration" if rule in {"R1", "R2", "R3"} else "Interpretation of disability wording needs team clarification" if rule == "R15" else "Identity pairing is not evidence of invalidity" if rule == "R14" else "No independently adjudicated accuracy labels available",
            "Recommended correction":"Retain review/context action; calibrate using adjudicated data before stronger action"})
    for rule, (name, condition, severity, action) in GATES.items():
        new = rule in {"G_ELIGIBILITY_REQUIRED", "G_ELIGIBILITY_RESPONSE", "G_REVIEW", "G_REVISED_POLICY"}
        rows.append({"Rule ID":rule,"Rule name":name,"Rule category":"Requested payment safeguard" if new else "Existing implemented gate/prefilter",
            "Study PID":5749 if rule in {"EX_ARCHIVE_5749", "EX_PRELAUNCH_5749"} else "All applicable PIDs",
            "Study name":"Infant Autism Screening bilingual" if rule in {"EX_ARCHIVE_5749", "EX_PRELAUNCH_5749"} else "All configured studies",
            "Plain-language description":condition,"Variables used":condition,"Exact logical condition":condition,
            "Intended condition":condition,"Missing-value handling":"Explicit unknown; fail closed for payment. Archive and prelaunch require numeric IDs.",
            "Severity":severity,"Final action":action,"Approval status":"Approved with modifications" if rule == "G_INDEPENDENT" else "Approved for implementation",
            "Approval provenance":"User's explicit no-Pay-on-missing-eligibility and no-unapproved-risk-combination exclusion requirements" if new or rule == "G_INDEPENDENT" else "Previously authorized implementation / study-reference exclusion",
            "Implementation status":"Implemented", "Potential defect":"Baseline issues Do not pay from risk families without independent confirmation" if rule == "G_INDEPENDENT" else "Baseline lacks required eligibility validation" if rule == "G_ELIGIBILITY_REQUIRED" else "Broad literal test policy can exclude genuine discussion of testing" if rule == "EX_TEST_WORD" else "No additional defect established",
            "Recommended correction":"Keep as review until the team approves a stronger combined exclusion" if rule == "G_INDEPENDENT" else "Retain explicit safeguard and independent regression evidence"})
    for candidate in candidates.to_dict("records"):
        rows.append({"Rule ID":"CAND_" + str(candidate["Candidate rule ID"]).replace("CAND_", "").replace("Candidate ", "").replace("Rule ", ""),
            "Rule name":candidate["Rule name"],"Rule category":"New proposal / refinement",
            "Study PID":candidate.get("Study PID", "All applicable PIDs"),"Study name":candidate.get("Study name", "All configured studies"),
            "Plain-language description":candidate["Business rationale"],"Variables used":candidate["Variables used"],
            "Exact logical condition":candidate["Proposed logical condition"],"Intended condition":candidate["Proposed logical condition"],
            "Missing-value handling":candidate["Missing-data handling"],"Severity":candidate["Proposed severity"],
            "Final action":"Not active; " + candidate["Proposed action"],"Approval status":candidate["Approval status"],
            "Approval provenance":"No new research-team approval supplied; existing equivalent rules separately documented",
            "Implementation status":"Not implemented", "Potential defect":candidate["False-positive risk"],
            "Recommended correction":candidate["Requires clarification"]})
    spec = pd.DataFrame(rows)
    if spec["Rule ID"].duplicated().any():
        # Candidate tables may be stratified by study; retain a single global
        # condition and preserve study-specific observed counts in Table 5.
        if spec.loc[spec["Rule ID"].duplicated(False),"Rule category"].ne("New proposal / refinement").any():
            raise ValueError("Active rule specification has duplicate IDs")
        spec = spec.drop_duplicates("Rule ID").reset_index(drop=True)
    return spec


def inventory_and_approval(specification, activity, sources):
    existing, approval = [], []
    for row in specification.to_dict("records"):
        rule = row["Rule ID"]
        active = row["Implementation status"] == "Implemented"
        if active and rule not in {"G_ELIGIBILITY_REQUIRED", "G_ELIGIBILITY_RESPONSE", "G_REVIEW", "G_REVISED_POLICY"}:
            triggered = int(activity.loc[activity["Rule ID"].eq(rule),"Triggered"].sum())
            if rule == "EX_ARCHIVE_5749": triggered = int(sources.exclusions.archive_clone.sum())
            existing.append({"Existing rule ID":rule,"Rule name":row["Rule name"],"Study PID":row["Study PID"],"Study name":row["Study name"],
                "Plain-language description":row["Plain-language description"],"Variables used":row["Variables used"],
                "Exact implemented condition":row["Exact logical condition"],"Intended condition":row["Intended condition"],
                "Missing-value handling":row["Missing-value handling"],"Current severity":"Legacy refusal" if rule == "G_INDEPENDENT" else row["Severity"],
                "Current action":"Do not pay when complete and source verified; incomplete outranks other gates in baseline" if rule == "G_INDEPENDENT" else row["Final action"],
                "Number triggered":triggered,"Implementation matches intention":"Regression evidence in Table 7; policy interpretation requires the qualifications here",
                "Potential defect":row["Potential defect"],"Recommended correction":row["Recommended correction"],
                "Denominator":"Raw source records; 445 archive records outside final analysis" if rule == "EX_ARCHIVE_5749" else "Applicable post-archive cohort; scoring rules evaluated only after remaining policy exclusions"})
        decision = row["Approval status"]
        if not active and decision not in {"Requires clarification", "Rejected", "Deferred", "Manual review only"}:
            decision = "Requires clarification"
        approval.append({"Rule ID":rule,"Rule name":row["Rule name"],"Study PID":row["Study PID"],"Study name":row["Study name"],
            "Proposed severity":row["Severity"],"Reviewer decision":decision,
            "Requested modification":row["Recommended correction"],
            "Final approved condition":row["Exact logical condition"] if active else "Not approved for new production use",
            "Final severity":row["Severity"] if active else "Pending approval",
            "Final action":row["Final action"],"Implementation status":row["Implementation status"],
            "Reviewer comments":row["Approval provenance"]})
    return {3:pd.DataFrame(existing),6:pd.DataFrame(approval)}


def decision_matrix():
    definitions = [
        ("Hard policy exclusion","Any","Any","Any","No",EXCLUDE,"Do not pay","Existing test/prelaunch policy overrides all other outcomes; not a confirmed bot label"),
        ("Manual review","Source, explicit ineligibility, substantive contradiction or other active review rule","Any","Yes or other active concern","Yes",HOLD,"Hold payment","Adjudicate active concern before payment; independent risk families do not automatically prove invalidity"),
        ("Missing critical information","No higher-priority review or exclusion","Required survey section incomplete","No detected active concern","No",UNABLE,"Hold payment","Completion is insufficient; unknown information is not invented and incomplete record is not eligible"),
        ("Manual review","Complete survey; required eligibility missing, invalid or visibility unknown","Yes","Unknown","Yes",HOLD,"Hold payment","Resolve required eligibility before automatic payment"),
        ("Verification","Payment contact only; all eligibility and completion checks satisfied","No","No","Yes",VERIFY,"Hold payment","Verify contact ownership, format or confirmation before payment"),
        ("No payment blocker","Only clear or context-only active checks","No","No","No",PAY,"Eligible for payment","No active unresolved concern; candidate rules awaiting approval do not change this recommendation"),
    ]
    columns=["Highest rule severity","Additional rules triggered","Missing critical information","Contradiction unresolved","Manual review required","Final classification","Payment status","Explanation"]
    result = pd.DataFrame(definitions,columns=columns)
    result.insert(0,"Study name","All configured studies; apply metadata-specific rules")
    result.insert(0,"Study PID","All applicable PIDs")
    return result


def validate_analysis(tables, spec, activity, sources):
    r, summary = tables[13], tables[10]
    assert r["Response key"].is_unique and not r[["Study PID","Study name","Plain-language reason"]].isna().any().any()
    assert r["Plain-language reason"].str.strip().ne("").all()
    assert not (r["Study PID"].eq(5749)&pd.to_numeric(r["Record ID"],errors="coerce").between(1,445)).any()
    raw_count = int(sources.provenance.raw_records.sum())
    archives = int(sources.exclusions.archive_clone.sum())
    assert len(r) == raw_count-archives
    pay = r.loc[r["Revised classification"].eq(PAY)]
    forbidden = {"G_SOURCE","G_COMPLETE","G_CONTACT","G_INDEPENDENT","G_REVIEW","G_ELIGIBILITY_REQUIRED","G_ELIGIBILITY_RESPONSE","EX_TEST_WORD","EX_PRELAUNCH_5749"}
    assert all(not (set(value.split("; ")) & forbidden) for value in pay["Triggered rule IDs"])
    assert pay["Required eligibility answered"].eq("Yes").all()
    assert pay["Survey completion status"].eq("Complete").all()
    known = set(spec["Rule ID"])
    assert all(set(value.split("; "))-{""} <= known for value in r["Triggered rule IDs"])
    assert all(not rule.startswith("CAND_") for value in r["Triggered rule IDs"] for rule in value.split("; "))
    assert summary.loc[summary["Study PID"].ne("All studies"),"Total analyzed"].sum() == len(r)
    assert tables[11].loc[tables[11]["Study PID"].eq("All studies")&tables[11]["Current classification"].eq("Total"),"Total"].item() == len(r)
    queuekeys = set(tables[14]["Study PID"].astype(str)+":"+tables[14]["Record ID"].astype(str))
    assert queuekeys == set(r.loc[r["Manual-review indicator"].eq(1),"Response key"])
    assert not activity.duplicated(["Rule ID","Response key"]).any()
    return {"Applicable records":len(r),"Archived clone outside analysis":archives,"Source records reconciled":raw_count,
        "Unique record keys":True,"Study mapping verified":True,"Pay Now prerequisite violations":0,
        "Unapproved rules affecting production":0,"Transition and queue reconciled":True}


def run_validity_analysis(project_dir=PROJECT_DIR, *, refresh=True, export=True, render=True):
    from validity_audit import build_validity_audit_tables, candidate_signals
    from validity_regression import run_regression_tests, assert_regression_coverage, build_rule_validation_results
    from validity_tests import additional_cases
    from validity_deliverables import export_validity_deliverables
    project_dir = Path(project_dir).resolve()
    timestamp = datetime.now(timezone.utc).isoformat()
    codefiles = ["refined_sources.py","refined_screening.py","validity_classification.py","validity_analysis.py","validity_audit.py","validity_regression.py","validity_tests.py"]
    hashes = {name:hashlib.sha256((project_dir/name).read_bytes()).hexdigest() for name in codefiles}
    version = "validity-v1-"+hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:12]
    sources = load_refined_sources(project_dir,refresh=refresh)
    baseline = screening.score_refined_records(sources)
    results, activity = build_record_results(sources,baseline,project_dir=project_dir,version=version,timestamp=timestamp)
    tables = build_validity_audit_tables(sources,baseline,revised_results=results,project_dir=project_dir)
    signals = candidate_signals(sources,baseline)
    # Shadow proposals are inspectable but are never inputs to revised_decision.
    signals = signals.set_index("Response key")
    candidate_columns = [c for c in signals if c.startswith("Candidate ") and len(c) == 11]
    proposed = {key:"; ".join(c.replace("Candidate ","CAND_") for c in candidate_columns if bool(row[c])) for key,row in signals.iterrows()}
    results["Candidate rules pending approval"] = results["Response key"].map(proposed).fillna("")
    results["Proposed action if approved"] = results["Candidate rules pending approval"].map(lambda v:"Evaluate proposal with research team; no automatic production change" if v else "No additional candidate signal observed")
    spec = rule_specification(tables[5],activity)
    tables.update(inventory_and_approval(spec,activity,sources))
    tables[7] = run_regression_tests(extra_cases=additional_cases(sources,baseline,results))
    active = spec.loc[spec["Implementation status"].eq("Implemented"),"Rule ID"]
    assert_regression_coverage(tables[7],active)
    tables[8] = build_rule_validation_results(spec,activity,tables[7])
    tables[9] = decision_matrix()
    tables.update(summary_tables(results,activity,spec))
    tables[13], tables[14] = results, review_queue(results)
    validation = validate_analysis(tables,spec,activity,sources)
    audit = build_audit_log(project_dir,sources,hashes,version,timestamp,tables)
    report = build_report(tables,validation,sources,version)
    result = {"sources":sources,"baseline":baseline,"tables":tables,"rule_specification":spec,"activity":activity,
              "validation":validation,"audit_log":audit,"report":report,"version":version}
    if export:
        output = project_dir/"Caregiver Outputs"/"respondent_validity_review"
        notes = {n:(f"Snapshot {sources.snapshot_dir.name}. {len(results):,} applicable records; {validation['Archived clone outside analysis']:,} archived PID5749 records outside denominator. "
                    "R1–R15 use the policy-screened cohort. Unknown accuracy is not estimable.") for n in range(1,17)}
        notes[7] = "Synthetic fixtures test code, not survey prevalence. Actual replay is separately labeled. Not applicable test types have an explicit rationale."
        notes[10] += " Current labels are normalized; changed counts exclude mere label renaming."
        notes[12] += " Overlapping associations; do not sum rows as causal effects."
        forbidden_values = baseline["Email address"].dropna().loc[lambda s:s.str.contains("@")].unique().tolist()
        result["export"] = export_validity_deliverables(tables,output_dir=output,rule_specification=spec,
            report_text=report,audit_log=audit,forbidden_values=forbidden_values,render=render,table_notes=notes)
    return result


def build_audit_log(project_dir,sources,hashes,version,timestamp,tables):
    rows=[]
    def add(step,input_file,count,output,warning="None"):
        rows.append({"Processing step":step,"Timestamp":timestamp,"Input file":str(input_file),"Rule version":version,
            "Number of records affected":count,"Output file":str(output),"Warnings or errors":warning})
    add("Validate supplied PID/study reference",project_dir/REFERENCE_FILE,len(sources.registry),"Table 1")
    for name,digest in hashes.items(): add("Record source code hash",project_dir/name,"Not applicable","Analysis version",f"SHA256={digest}")
    for row in sources.provenance.to_dict("records"):
        add("Verify REDCap PID/title and export source",sources.snapshot_dir/f"{row['project_id']}_record.json",row['raw_records'],"Table 1",f"Mode={row['source_mode']}; fetched={row['fetched_at_utc']}")
    add("Apply archive, launch and test policies",sources.snapshot_dir/"exclusions.csv",len(sources.exclusions),"Tables 1 and 13","Archived PID5749 IDs1–445 are outside analysis; other excluded rows retained in results")
    add("Recompute unchanged current bot and safeguarded classification",sources.snapshot_dir,len(tables[13]),"record_level_results.csv","Candidates are shadow proposals; no Jessica adjudication supplied")
    add("Execute independent regression cases",project_dir/"validity_regression.py",len(tables[7]),"regression_test_results.csv","Synthetic tests are not observed respondents")
    add("Validate tables, counts, transition and privacy",sources.snapshot_dir,len(tables[13]),"bot_analysis_summary.xlsx","Publication is refused on failed checks")
    for name in ["record_level_results.csv","manual_review_queue.csv","rule_specification.csv","regression_test_results.csv","misclassification_analysis.csv","bot_analysis_report.md","analysis_audit_log.csv"]:
        add("Publish validated analytical artifact",sources.snapshot_dir,len(tables[13]),name)
    return pd.DataFrame(rows)


def build_report(tables,validation,sources,version):
    from validity_deliverables import TABLE_TITLES
    r=tables[13]; total=tables[10].loc[tables[10]["Study PID"].eq("All studies")].iloc[0]
    def table(n,maximum=24,columns=None):
        frame=tables[n] if columns is None else tables[n][columns]
        body=frame.head(maximum).to_markdown(index=False)
        tail=f"\n\nShowing {min(maximum,len(frame)):,} of {len(frame):,} rows. The workbook and corresponding CSV contain the complete table."
        return f"\n\n### Table {n}. {TABLE_TITLES[n]}\n\n"+body+tail
    sections=["# Caregiver respondent-validity analysis",f"Snapshot: `{sources.snapshot_dir.name}`. Bot version: `{version}`.",
        "## 1. Executive findings",
        f"The analysis covers **{len(r):,} respondents** after removing **{validation['Archived clone outside analysis']:,} archived clone records** from the denominator. Other policy exclusions remain visible. "
        f"The approved-rule result has **{total['Revised Pay Now']:,} PAY NOW**, **{total['Revised excluded']:,} EXCLUDE**, **{total['Revised manual review']:,} requiring manual review or contact verification**, and **{total['Revised unable to classify']:,} unable to classify**. "
        f"**{total['Classification changed']:,} classifications changed**, **{total['Removed from Pay Now']:,} moved out of Pay Now**, and **{total['Added to Pay Now']:,} moved into Pay Now**. Counts compare normalized decision meanings rather than renamed labels.",
        "Observed findings: the baseline does not explicitly check required eligibility answers; the new guard closes that future-data gap. Risk-family combinations now remain on hold for adjudication. Incomplete records with active substantive concerns appear in the review queue. No independently adjudicated invalid-Pay-Now or valid-excluded totals are available. Candidate age/birthdate and other refinements remain proposals, separate from active classification.",
        "Recommendations: adjudicate the proposed age/birthdate discrepancies, review literal test-word exclusions for genuine discussion of testing, and calibrate timing by study. No new candidate rule is treated as approved by Jessica.",
        "## 2. Data and field audit",table(1),table(2,12),
        "Unit: one unique PID/record key per respondent; REDCap source validation fails on duplicate keys. Raw records and full free text remain in restricted source snapshots. Email linkage uses a keyed HMAC; the secret key remains restricted. This presentation has no full emails.",
        "## 3. Current bot-rule inventory",table(3),
        "## 4. Manual response-review findings",table(4,16),
        "Desk review is an analyst assessment, not independent research-team adjudication. The deterministic sample includes current Pay Now, current refusal, contradictions, timing, duplicates, borderline records and a reproducible comparison sample. A suspected inconsistency is not established fraud.",
        "## 5. Proposed rule table",table(5),"## 6. Rule approval matrix",table(6,40),
        "Existing user-authorized rules and explicitly requested payment safeguards are active. Newly proposed refinements remain unapproved. Formal Jessica approval is never inferred from implementation or from cohort labels.",
        "## 7. Regression-test results",table(7,12),
        "Tests exercise production functions with independently specified expected outcomes. Synthetic fixtures are labeled and excluded from observed counts. Historically misclassified examples are not fabricated; unavailable historical labels are explicitly documented.",
        "## 8. Rule performance",table(8,30),table(9),
        "Accuracy denominators require independently adjudicated reference labels. Precision and recall are Not estimable. Trigger percentage uses only evaluable records; policy-excluded records are not silently counted as passing unrun rules.",
        "## 9. Current-versus-revised classification summary",table(10),
        "## 10. Classification transition matrix",table(11,30),
        "## 11. Rule-impact table",table(12,26),
        "Rule impact counts are overlapping associations among triggered records, not leave-one-rule-out causal effects; they must not be added together. Hard-exclusion precedence prevents lower-priority review rules from changing the outcome.",
        "## 12. Misclassification and root-cause analysis",table(15),
        "## 13. Prioritized manual-review queue",table(14,12),
        "## 14. Record-level results",table(13,10,["Study PID","Study name","Record ID","Current classification","Revised classification","Triggered rule IDs","Plain-language reason"]),
        "## 15. Unresolved research-team decisions",table(16),
        "## 16. Technical limitations",
        "No independent adjudication labels, device fingerprints or page-level timing logs were supplied. Section duration is the sum of four recorded section times, only when all are present and nonnegative; elapsed time from sign-up can include inactive gaps. Current time thresholds are review heuristics, not validated bot probabilities. Spanish code/label mismatches remain explicit source holds. A literal standalone test policy can catch valid narratives about diagnostic testing. The source-reference cohort designation is not used as individual ground truth. No REDCap project, verification survey, contact list or distribution workflow was created. No respondent was contacted and no payment was executed.",
        "## 17. Recommended next steps",
        "Jessica should resolve Table 16, record approvals and adjudications against the stable PID/record key, then rerun the notebook. Activate only documented approved refinements after their independent tests pass. Use adjudicated examples to estimate precision, recall and study-specific timing thresholds; retain a separate holdout for evaluation."]
    return "\n\n".join(sections)+"\n"

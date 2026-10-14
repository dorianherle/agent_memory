"""E6: source-backed preservation checks and one bounded repair."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("memory_micro_common",HERE.parent / "common.py")
common=importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(common)

HISTORY=[
    """SOURCE S1. This is a new, constructed history for a spectrum-export validator. The user's goal is to prepare a handoff for revision 4. Exact user requirements: do not overwrite raw captures. CSV exports must have the first two columns wavelength_nm and intensity_counts, in that order. A missing calibration value must remain UNKNOWN. Never replace a missing calibration value with zero. Routine work: make the report readable, rename plots, use consistent colors, align headings, and remove duplicate scratch pages.""",
    """SOURCE S2. The first note gave an acceptance limit of 12.0 counts and claimed that a trial passed. A review corrected the acceptance limit to 8.50 counts. The trial measurement is 9.10 counts. The earlier pass claim is withdrawn because 9.10 exceeds 8.50. The value 12.0 is superseded. Routine work: chart colors are complete, table headings are aligned, the report title is shorter, and figure captions were checked. None of that work changes the measurement or limit.""",
    """SOURCE S3. The general minimum exposure is 0.50 ms. Rare exception: batch K9 may use a minimum exposure of 0.20 ms only when temperature is less than or equal to 18.0 degrees C. At exactly 18.0 degrees C the exception applies. Above 18.0 degrees C batch K9 uses the general 0.50 ms minimum. Other batches always use the general minimum. Routine work: the team changed marker shapes, moved the legend, renamed drafts, and removed blank report pages. The batch exception remains required.""",
    """SOURCE S4. Revision 4 has not been tested. Release is on hold pending verification. An unexplained noise finding remains unresolved. The cause is unknown. A proposed cable explanation has not been tested and is not established. Routine work: the report opens, links work, captions are complete, and obsolete presentation files have been archived. These completed presentation tasks do not imply that revision 4 passed validation.""",
]
REFERENCE_PACKET=("Revision 4 export validator: release HOLD; revision 4 untested. Do not overwrite raw captures. "
                  "First CSV columns: wavelength_nm, intensity_counts, in order. Missing calibration stays UNKNOWN; never use zero. "
                  "Limit 8.50 counts; 12.0 superseded. Prior pass withdrawn: trial 9.10 counts exceeds 8.50. "
                  "General exposure minimum 0.50 ms. Batch K9 minimum 0.20 ms only at temperature <=18.0 degrees C; "
                  "above 18.0 use 0.50 ms. Other batches use 0.50 ms. Noise cause unknown; cable explanation untested.")
WRITER=("Prepare compact accumulated memory for the next engineer from the supplied history. Preserve exact user requirements, "
        "corrected measurements, withdrawn claims, applicable conditions, rare exceptions, current state, and unknowns. "
        "Omit completed presentation work. Do not invent facts. Use compact notation when useful. "
        "Target 140 tokens. The complete packet must fit within 160 tokens using the o200k_base estimate. "
        "Return only the packet. Use short, direct sentences.")

PUBLIC=[
    {"id":"P1","question":"State the exact user requirements for raw captures, the first two CSV columns, and missing calibration.","fields":{"overwrite_raw_allowed":"boolean or UNKNOWN","first_two_columns":"array of two strings, or UNKNOWN","missing_calibration_value":"string or UNKNOWN","zero_substitution_allowed":"boolean or UNKNOWN"}},
    {"id":"P2","question":"Give the current and superseded acceptance limits in counts.","fields":{"current_limit_counts":"number or UNKNOWN","superseded_limit_counts":"number or UNKNOWN"}},
    {"id":"P3","question":"Give the trial measurement and state whether its earlier pass claim was retained or withdrawn.","fields":{"trial_counts":"number or UNKNOWN","earlier_pass_claim":"retained, withdrawn, or UNKNOWN"}},
    {"id":"P4","question":"Give the general minimum exposure and the batch exception, including the exact temperature boundary and comparison.","fields":{"general_min_exposure_ms":"number or UNKNOWN","exception_batch":"string or UNKNOWN","exception_min_exposure_ms":"number or UNKNOWN","exception_temperature_c":"number or UNKNOWN","exception_comparison":"less_or_equal, strictly_less, or UNKNOWN"}},
    {"id":"P5","question":"Give the target revision, its test state, and release state.","fields":{"target_revision":"number or UNKNOWN","revision_test_state":"untested, passed, failed, or UNKNOWN","release_state":"hold, released, or UNKNOWN"}},
    {"id":"P6","question":"What is the established noise cause? Has the cable explanation been tested?", "fields":{"noise_cause":"unknown, cable, or UNKNOWN","cable_explanation_tested":"boolean or UNKNOWN"}},
]
PUBLIC_EXPECTED={"overwrite_raw_allowed":False,"first_two_columns":["wavelength_nm","intensity_counts"],
                 "missing_calibration_value":"UNKNOWN","zero_substitution_allowed":False,
                 "current_limit_counts":8.50,"superseded_limit_counts":12.0,"trial_counts":9.10,
                 "earlier_pass_claim":"withdrawn","general_min_exposure_ms":0.50,"exception_batch":"K9",
                 "exception_min_exposure_ms":0.20,"exception_temperature_c":18.0,"exception_comparison":"less_or_equal",
                 "target_revision":4,"revision_test_state":"untested","release_state":"hold",
                 "noise_cause":"unknown","cable_explanation_tested":False}
PUBLIC_SOURCES={"P1":["S1"],"P2":["S2"],"P3":["S2"],"P4":["S3"],"P5":["S1","S4"],"P6":["S4"]}
HIDDEN=[
    {"id":"E1","question":"Is exposure 0.20 ms allowed for batch K9 at exactly 18.0 degrees C?", "fields":{"k9_at_boundary_allowed":"boolean or UNKNOWN"}},
    {"id":"E2","question":"Is exposure 0.20 ms allowed for batch K9 at 18.01 degrees C?", "fields":{"k9_above_boundary_allowed":"boolean or UNKNOWN"}},
    {"id":"E3","question":"Is exposure 0.20 ms allowed for batch J2 at 18.0 degrees C?", "fields":{"other_batch_short_exposure_allowed":"boolean or UNKNOWN"}},
    {"id":"E4","question":"Does the recorded trial satisfy the current acceptance limit?", "fields":{"recorded_trial_passes_current_limit":"boolean or UNKNOWN"}},
    {"id":"E5","question":"May the next engineer rely on the earlier claim that the trial passed?", "fields":{"earlier_success_is_valid":"boolean or UNKNOWN"}},
    {"id":"E6","question":"Give the required first and second CSV column names separately.", "fields":{"first_column":"string or UNKNOWN","second_column":"string or UNKNOWN"}},
    {"id":"E7","question":"What must be written when calibration is missing?", "fields":{"value_for_missing_calibration":"string or UNKNOWN"}},
    {"id":"E8","question":"Is revision 4 ready for release, and has it been tested?", "fields":{"ready_for_release":"boolean or UNKNOWN","revision4_tested":"boolean or UNKNOWN"}},
    {"id":"E9","question":"May raw captures be overwritten by the export step?", "fields":{"raw_overwrite_permitted":"boolean or UNKNOWN"}},
    {"id":"E10","question":"Is the noise cause established as a cable fault?", "fields":{"noise_established_as_cable_fault":"boolean or UNKNOWN"}},
]
HIDDEN_EXPECTED={"k9_at_boundary_allowed":True,"k9_above_boundary_allowed":False,
                 "other_batch_short_exposure_allowed":False,"recorded_trial_passes_current_limit":False,
                 "earlier_success_is_valid":False,"first_column":"wavelength_nm","second_column":"intensity_counts",
                 "value_for_missing_calibration":"UNKNOWN","ready_for_release":False,"revision4_tested":False,
                 "raw_overwrite_permitted":False,"noise_established_as_cable_fault":False}
HIDDEN_SOURCES={"E1":["S3"],"E2":["S3"],"E3":["S3"],"E4":["S2"],"E5":["S2"],"E6":["S1"],"E7":["S1"],"E8":["S4"],"E9":["S1"],"E10":["S4"]}


def frozen_input():
    return {"history":HISTORY,"reference":REFERENCE_PACKET,"writer":WRITER,"public":PUBLIC,
            "public_expected":PUBLIC_EXPECTED,"hidden":HIDDEN,"hidden_expected":HIDDEN_EXPECTED}


def input_hash():
    return hashlib.sha256(json.dumps(frozen_input(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def freeze():
    required_size=common.token_size(REFERENCE_PACKET)
    assert required_size<=160,"Required facts do not fit."
    p={"experiment":"E6 checked aggressive compaction","synthetic":True,
       "hypothesis":"A source-backed preservation check and at most one repair produce an accepted packet with higher hidden-question accuracy than the same initial candidate, within the same size cap.",
       "history":HISTORY,"initial_writer_instruction":WRITER,"packet_target_estimated_tokens":140,
       "packet_cap_estimated_tokens":160,"packet_encoding":"o200k_base","required_facts_reference_packet":REFERENCE_PACKET,
       "required_facts_reference_size_estimate":required_size,"public_preservation_questions":PUBLIC,
       "public_expected_answers_hidden_from_reader":PUBLIC_EXPECTED,"public_source_map":PUBLIC_SOURCES,
       "hidden_evaluation_questions":HIDDEN,"hidden_expected_answers_hidden_from_all_model_calls":HIDDEN_EXPECTED,
       "hidden_source_map":HIDDEN_SOURCES,
       "design":"Generate one initial candidate. Check it with a fresh reader on public questions. Repair only if a public check or measured size check fails. Send the original candidate, only failed checks, and original history to the repair writer. Permit one repair. Reject a repaired packet above the measured cap. If it fits, run a fresh public check. Run fresh hidden receivers for unchecked and checked candidates, plus a full-history control. Never truncate packets. No injected errors or input tuning.",
       "acceptance_rule":"The checked candidate is accepted only when it fits the 160-token estimated cap and all public question groups pass. A repaired candidate that exceeds the cap is rejected. Evaluate a rejected candidate only for diagnosis and label it unaccepted.",
       "pass_rule":"Pass if the initial candidate fits, the full-history hidden control passes all groups, the checked candidate is accepted, and its hidden score is strictly higher than the initial candidate score. Null effect if no preservation or size check fails and the candidate remains unchanged. Fail if a valid initial candidate requires repair but repair is rejected or does not improve hidden accuracy. Inconclusive for model errors, invalid full-history control, or an initial size violation. Report acceptance, hidden scores, and budget status independently.",
       "resource_cap":{"maximum_calls":10,"maximum_wall_seconds":300,"maximum_repairs":1,"model":"gpt-5.6-sol","reasoning":"low","call_timeout_seconds":65},
       "controls":["The same initial candidate feeds both branches.","No reader sees expected answers or original history, except the full-history control.","Hidden questions are not supplied to either writer or preservation reader.","No retries and no mechanical truncation.","All calls are isolated and model-only with native usage logging."],
       "limits":["One new constructed history. No general claim.","Hidden questions test preservation and boundary decisions, not new experimental discovery.","Packet sizes are o200k_base estimates. Actual total model-call tokens use native usage events."],
       "frozen_input_sha256":input_hash()}
    common.dump(HERE / "protocol.json",p)
    return p


def grade(text,questions,expected):
    try:
        answers=json.loads(text)
        if not isinstance(answers,dict):raise ValueError("answer_is_not_object")
    except (ValueError,TypeError) as error:
        return {"valid_json":False,"error":str(error),"all_pass":False,"groups_passed":0,"groups_total":len(questions),
                "failed_checks":[{"id":"FORMAT","error":str(error),"required":"Return the requested fields."}]}
    details={}
    for key,wanted in expected.items():
        actual=answers.get(key,"MISSING")
        if type(wanted) is bool:ok=type(actual) is bool and actual is wanted
        elif type(wanted) in (int,float):ok=type(actual) in (int,float) and abs(actual-wanted)<1e-9
        elif isinstance(wanted,list):ok=actual==wanted
        else:ok=isinstance(actual,str) and actual.strip().lower()==wanted.lower()
        details[key]={"pass":ok,"actual":actual,"expected":wanted}
    groups={q["id"]:all(details[k]["pass"] for k in q["fields"]) for q in questions}
    failed=[{"id":q["id"],"question":q["question"],"failed_fields":{k:details[k] for k in q["fields"] if not details[k]["pass"]}} for q in questions if not groups[q["id"]]]
    return {"valid_json":True,"answers":answers,"field_details":details,"groups":groups,
            "groups_passed":sum(groups.values()),"groups_total":len(questions),"all_pass":all(groups.values()),"failed_checks":failed}


def reader_prompt(packet,questions):
    return ("You are a fresh reader. Answer only from the evidence below. Apply explicit limits to the stated cases. "
            "Do not invent missing facts. Use UNKNOWN if the evidence does not support an answer. "
            "Return a JSON object with every requested field directly at its top level. Use the listed types and state strings. "
            "Write this JSON object inside the requested text field.\nEVIDENCE:\n"+packet+
            "\nQUESTIONS:\n"+json.dumps(questions,ensure_ascii=False))


def run():
    protocol=json.loads((HERE / "protocol.json").read_text())
    assert protocol["frozen_input_sha256"]==input_hash(),"Frozen inputs changed."
    if (HERE / "calls").exists():raise RuntimeError("Calls already exist. Do not overwrite them.")
    start=time.monotonic(); submitted=0; errors=[]; packets={}; public_checks={}; hidden={}; repair_used=False
    checked_accepted=False; rejection=None; failed_checks=[]

    def call(prompt,label):
        nonlocal submitted
        remaining=300-(time.monotonic()-start)
        if remaining<=0:raise RuntimeError("wall_time_cap")
        submitted+=1
        return common.complete(prompt,HERE / "calls" / label,timeout=min(65,remaining))

    try:
        result=call(WRITER+"\nHISTORY:\n"+"\n\n".join(HISTORY),"initial_writer")
        packets["initial"]={"text":result["text"],"estimated_tokens":common.token_size(result["text"])}
        checked_text=result["text"]
        check=call(reader_prompt(checked_text,PUBLIC),"initial_public_check")
        public_checks["initial"]=grade(check["text"],PUBLIC,PUBLIC_EXPECTED)
        failed_checks=public_checks["initial"]["failed_checks"].copy()
        if packets["initial"]["estimated_tokens"]>160:
            failed_checks.append({"id":"SIZE","actual_estimated_tokens":packets["initial"]["estimated_tokens"],"maximum_estimated_tokens":160})
        if failed_checks:
            repair_used=True
            prompt=(WRITER+"\nRepair the candidate once. Fix the failed checks while retaining its supported facts. "
                    "Do not add hidden assumptions. You receive only failed checks, the original candidate, and original history.\n"
                    "ORIGINAL CANDIDATE:\n"+checked_text+"\nFAILED CHECKS:\n"+json.dumps(failed_checks,ensure_ascii=False)+
                    "\nORIGINAL HISTORY:\n"+"\n\n".join(HISTORY))
            repaired=call(prompt,"repair_writer")
            checked_text=repaired["text"]
            packets["checked"]={"text":checked_text,"estimated_tokens":common.token_size(checked_text)}
            if packets["checked"]["estimated_tokens"]>160:
                rejection="repaired_packet_exceeds_cap"
            else:
                check=call(reader_prompt(checked_text,PUBLIC),"repaired_public_check")
                public_checks["checked"]=grade(check["text"],PUBLIC,PUBLIC_EXPECTED)
                checked_accepted=public_checks["checked"]["all_pass"]
                if not checked_accepted:rejection="repaired_packet_failed_public_checks"
        else:
            packets["checked"]=dict(packets["initial"])
            public_checks["checked"]=public_checks["initial"]
            checked_accepted=True
        print(json.dumps({"repair_used":repair_used,"initial_estimated_tokens":packets["initial"]["estimated_tokens"],
                          "checked_estimated_tokens":packets["checked"]["estimated_tokens"],"checked_accepted":checked_accepted,"rejection":rejection}),flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            prompts={"unchecked":reader_prompt(packets["initial"]["text"],HIDDEN),
                     "checked":reader_prompt(packets["checked"]["text"],HIDDEN),
                     "full_history_control":reader_prompt("\n\n".join(HISTORY),HIDDEN)}
            futures={arm:pool.submit(call,prompt,"hidden_receiver_"+arm) for arm,prompt in prompts.items()}
            for arm,future in futures.items():
                result=future.result()
                hidden[arm]={"grade":grade(result["text"],HIDDEN,HIDDEN_EXPECTED),"accepted_packet":checked_accepted if arm=="checked" else None}
                print(json.dumps({"hidden_receiver":arm,"groups_passed":hidden[arm]["grade"]["groups_passed"],"groups_total":len(HIDDEN)}),flush=True)
    except Exception as error:errors.append(repr(error))

    logs=[]
    for path in sorted((HERE / "calls").glob("*/result.json")):
        logs.append({"label":path.parent.name,**json.loads(path.read_text())})
    usage={k:sum((r.get("usage") or {}).get(k,0) for r in logs) for k in ["input_tokens","output_tokens"]}
    overhead_logs=[r for r in logs if r["label"] in {"initial_public_check","repair_writer","repaired_public_check"}]
    overhead={"calls":len(overhead_logs),"input_tokens":sum((r.get("usage") or {}).get("input_tokens",0) for r in overhead_logs),
              "output_tokens":sum((r.get("usage") or {}).get("output_tokens",0) for r in overhead_logs),
              "elapsed_seconds_sum":sum(r["elapsed_seconds"] for r in overhead_logs)}
    valid_calls=all(r["ok"] and r["usage_complete"] and not r["native_tools"] for r in logs)
    control_ok=hidden.get("full_history_control",{}).get("grade",{}).get("all_pass",False)
    initial_budget_ok=packets.get("initial",{}).get("estimated_tokens",161)<=160
    if errors or len(hidden)!=3 or not valid_calls or not control_ok or not initial_budget_ok:
        conclusion="inconclusive"
    elif not repair_used:
        conclusion="null_effect"
    elif checked_accepted and hidden["checked"]["grade"]["groups_passed"]>hidden["unchecked"]["grade"]["groups_passed"]:
        conclusion="passed"
    else:conclusion="failed"
    result={"experiment":"E6","conclusion":conclusion,"elapsed_seconds":time.monotonic()-start,
            "submitted_calls":submitted,"calls_with_results":len(logs),"errors":errors,"valid_calls":valid_calls,
            "full_history_control_valid":control_ok,"initial_budget_valid":initial_budget_ok,
            "repair_used":repair_used,"failed_checks_sent_to_repair":failed_checks,"checked_accepted":checked_accepted,
            "rejection":rejection,"packets":packets,"public_checks":public_checks,"hidden_receivers":hidden,
            "native_usage":usage,"check_and_repair_overhead":overhead,"calls":logs,"frozen_input_sha256":input_hash()}
    common.dump(HERE / "results.json",result)
    lines=["E6: Checked aggressive compaction.",f"Conclusion: {conclusion}.","This result applies only to one constructed history.",
           f"Repair used: {repair_used}. Checked packet accepted: {checked_accepted}. Rejection: {rejection}."]
    for name,packet in packets.items():lines.append(f"{name} packet-size estimate: {packet['estimated_tokens']} tokens. Cap: 160. Encoding: o200k_base.")
    for name,value in public_checks.items():lines.append(f"Public check, {name}: {value['groups_passed']} of {value['groups_total']} groups passed.")
    for name,value in hidden.items():lines.append(f"Hidden receiver, {name}: {value['grade']['groups_passed']} of {value['grade']['groups_total']} groups passed.")
    lines += [f"Calls: {submitted}. Native input tokens: {usage['input_tokens']}. Native output tokens: {usage['output_tokens']}.",
              f"Check and repair overhead: {overhead['calls']} calls, {overhead['input_tokens']} native input tokens, {overhead['output_tokens']} native output tokens.",
              f"Elapsed time: {result['elapsed_seconds']:.1f} s.","Native usage includes fixed CLI context. Packet size is not total model-call token use.",
              "No general reliability or fundamental-truth claim is supported."]
    if not repair_used:lines.append("The initial packet passed every public check. It was retained without a repair. No error was injected.")
    if errors:lines.append("Errors: "+"; ".join(errors))
    (HERE / "report.txt").write_text("\n".join(lines)+"\n")
    print(json.dumps({"conclusion":conclusion,"calls":submitted,"usage":usage,"overhead":overhead,"errors":errors}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--freeze",action="store_true");args=parser.parse_args()
    if args.freeze:
        p=freeze();print(json.dumps({"frozen":True,"reference_estimated_tokens":p["required_facts_reference_size_estimate"],"hash":p["frozen_input_sha256"]}))
    else:run()

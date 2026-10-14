"""E1: five recursive handoffs on one frozen, constructed history."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("memory_micro_common", HERE.parent / "common.py")
common = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(common)

HISTORY = [
    """SOURCE H1. This is a constructed simulator history. Goal: qualify controller version 2.6 for the next engineer. General operating settings are speed 0.80 m/s, PWM period 2.50 ms, maximum load 4.00 kg, and ambient temperature from 18.0 to 28.0 degrees C. Rare exception: motor serial M17 has a maximum load of 2.75 kg when ambient temperature is strictly greater than 26.0 degrees C. At exactly 26.0 degrees C the general limit applies. The engineer must retain this exception. An early note says version 2.5 passed the timing test. The timing acceptance limit is a 99th percentile of 3.00 ms. Routine work: rename chart headings, align table borders, remove duplicate log lines, archive scratch plots, and clean the test directory. These routine tasks have no effect on qualification.""",
    """SOURCE H2. Correction: the early claim that version 2.5 passed timing is withdrawn. Corrected measurement: version 2.5 has a 99th percentile of 3.20 ms. This exceeds the 3.00 ms acceptance limit. Version 2.6 is the target version, but its timing has not been tested. Release is on hold. Release requires passing timing and resolving the watchdog fault. The team completed chart headings and table borders. Routine work: compare blue and green plot markers, move legend labels, shorten chart titles, standardize filenames, and remove obsolete temporary notes. Those changes do not affect measurements or operating limits.""",
    """SOURCE H3. The watchdog fault occurred in 3 of 5 simulator runs at a load of 3.60 kg. The cause is unknown. Do not report a cause as established. Do not replace an unknown quantity with zero to make a check pass. Release remains on hold. Routine work: the team selected green markers, moved legends to the right, used the same font for every chart, added a one-line caption to the overview, and removed empty scratch folders. A proposed extra test remains optional. No change was made to the operating settings or motor exception.""",
    """SOURCE H4. Timing qualification must use test B, a load of 2.00 kg, and a warmup of 120 s before measurement. These conditions apply to timing qualification. A proposed shorter warmup was rejected. There is still no timing result for version 2.6. Routine work: sort report pages, add page numbers, wrap long labels, use a lighter plot grid, check heading spacing, align decimal columns, and check that the overview opens. A colleague suggested that a watchdog reset might explain the fault, but that is an untested hypothesis. No cause is established.""",
    """SOURCE H5. Prepare the handoff to the next engineer. Version 2.6 remains the qualification target. Timing is still untested for version 2.6. The watchdog fault remains unresolved. Release remains on hold until timing passes and the watchdog fault is resolved. The overview, charts, captions, and filenames are complete. Routine work: create a clean index, link the summary page, close duplicate windows, archive the draft cover, and choose a short report title. Do not let completed presentation work imply that qualification passed. No operating limit or qualification condition was changed.""",
]

QUESTIONS = [
    {"id":"Q1", "question":"Which controller version is the qualification target?", "fields":{"goal_version":"string, or UNKNOWN"}},
    {"id":"Q2", "question":"What is the release state, and what must be completed before release?", "fields":{"release_state":"hold, released, or UNKNOWN", "release_requires_timing_pass":"boolean, or UNKNOWN", "release_requires_watchdog_resolution":"boolean, or UNKNOWN"}},
    {"id":"Q3", "question":"What are the exact general operating settings?", "fields":{"speed_m_per_s":"number, or UNKNOWN", "pwm_period_ms":"number, or UNKNOWN", "normal_max_load_kg":"number, or UNKNOWN", "ambient_min_c":"number, or UNKNOWN", "ambient_max_c":"number, or UNKNOWN"}},
    {"id":"Q4", "question":"What rare motor exception applies? Give its serial, maximum load, temperature boundary, comparison, and whether the exception applies at the boundary.", "fields":{"exception_serial":"string, or UNKNOWN", "exception_max_load_kg":"number, or UNKNOWN", "exception_temperature_c":"number, or UNKNOWN", "exception_temperature_comparison":"strictly_greater, greater_or_equal, or UNKNOWN", "exception_applies_at_boundary":"boolean, or UNKNOWN"}},
    {"id":"Q5", "question":"Was the original version 2.5 timing pass claim retained or withdrawn? Give the corrected 99th percentile and the acceptance limit.", "fields":{"v25_original_pass_claim":"withdrawn, retained, or UNKNOWN", "v25_corrected_p99_ms":"number, or UNKNOWN", "timing_limit_p99_ms":"number, or UNKNOWN"}},
    {"id":"Q6", "question":"What is the timing test state for version 2.6?", "fields":{"v26_timing_state":"untested, passed, failed, or UNKNOWN"}},
    {"id":"Q7", "question":"What are the watchdog failure count, total run count, load, and established cause?", "fields":{"watchdog_failure_count":"number, or UNKNOWN", "watchdog_total_runs":"number, or UNKNOWN", "watchdog_test_load_kg":"number, or UNKNOWN", "watchdog_cause":"unknown, reset, or UNKNOWN"}},
    {"id":"Q8", "question":"Which test, load, and warmup are required for timing qualification?", "fields":{"timing_test_id":"string, or UNKNOWN", "timing_test_load_kg":"number, or UNKNOWN", "timing_warmup_s":"number, or UNKNOWN"}},
    {"id":"Q9", "question":"May an unknown quantity be replaced by zero to make a check pass?", "fields":{"zero_substitution_allowed":"boolean, or UNKNOWN"}},
]

EXPECTED = {
    "goal_version":"2.6", "release_state":"hold", "release_requires_timing_pass":True,
    "release_requires_watchdog_resolution":True, "speed_m_per_s":0.80,
    "pwm_period_ms":2.50, "normal_max_load_kg":4.00, "ambient_min_c":18.0,
    "ambient_max_c":28.0, "exception_serial":"M17", "exception_max_load_kg":2.75,
    "exception_temperature_c":26.0, "exception_temperature_comparison":"strictly_greater",
    "exception_applies_at_boundary":False, "v25_original_pass_claim":"withdrawn",
    "v25_corrected_p99_ms":3.20, "timing_limit_p99_ms":3.00,
    "v26_timing_state":"untested", "watchdog_failure_count":3, "watchdog_total_runs":5,
    "watchdog_test_load_kg":3.60, "watchdog_cause":"unknown", "timing_test_id":"B",
    "timing_test_load_kg":2.00, "timing_warmup_s":120, "zero_substitution_allowed":False,
}

SOURCES = {
    "Q1":["H1","H5"], "Q2":["H2","H5"], "Q3":["H1"], "Q4":["H1"],
    "Q5":["H1","H2"], "Q6":["H2","H4","H5"], "Q7":["H3","H4","H5"],
    "Q8":["H4"], "Q9":["H3"],
}

SHARED = """You prepare accumulated memory for the next engineer. Merge the previous packet and the new history. Preserve the goal, current facts, corrected conclusions, exact values with units, applicable conditions, failures, and unknowns that the next engineer needs. Omit completed presentation work that has no effect on qualification. Do not add facts or turn a hypothesis into a fact. Do not replace unknowns with guesses. Use source identifiers where useful. Target 220 tokens for the complete packet. Use no more than 260 tokens. Return only the packet. Use short, direct sentences."""
FORMATS = {
    "prose":"Write one ordinary prose summary. Do not use field labels, lists, or a ledger.",
    "ledger":"Write a typed ledger. Use these six fields: goal, current_facts, superseded_facts, failures, conditions, unknowns. Keep stable facts from the previous packet unless the new history explicitly changes them. A field can be empty. Use compact text entries.",
}


def stable_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def freeze():
    protocol = {
        "experiment":"E1 recursive handoff drift", "synthetic":True,
        "hypothesis":"At five recursive handoffs, the typed ledger retains all tested task-critical facts while ordinary recursive prose loses at least one.",
        "histories":HISTORY, "variants":list(FORMATS), "compactor_shared_instruction":SHARED,
        "variant_instructions":FORMATS, "handoffs":5, "packet_token_target":220,
        "packet_token_maximum":260, "packet_count_encoding":"o200k_base",
        "questions_hidden_from_compactors":QUESTIONS, "expectations_hidden_from_all_model_calls":EXPECTED,
        "source_map":SOURCES, "controls":["Identical history chunks, model, reasoning, and packet budget.","Fresh packet-only receiver for each arm.","Fresh full-history receiver.","No receiver sees expected answers.","No repair, truncation, retries, or post-hoc input changes."],
        "pass_rule":"The full-history control and the ledger receiver must pass all nine question groups. Prose must fail at least one group. All compactor packets must be at most 260 tokens in the o200k_base estimate. All calls must have native usage and no tools. Report failed for a valid, complete run that does not meet this result pattern. Report inconclusive for invalid controls, budget violations, call errors, or incomplete runs.",
        "resource_cap":{"maximum_calls":16,"planned_calls":13,"maximum_wall_seconds":420,"model":"gpt-5.6-sol","reasoning":"low","call_timeout_seconds":75},
        "limits":["One constructed history. Two method variants. No repeated trials or second independent history within this call cap.","No causal or general performance claim.","Packet tokens and native total tokens are reported separately."],
    }
    protocol["frozen_input_sha256"] = stable_hash({"history":HISTORY,"questions":QUESTIONS,"expected":EXPECTED,"shared":SHARED,"formats":FORMATS})
    common.dump(HERE / "protocol.json", protocol)
    return protocol


def grade(text):
    try:
        answers=json.loads(text)
        if not isinstance(answers,dict):
            raise ValueError("answer_is_not_object")
    except (ValueError,TypeError) as error:
        return {"valid_json":False,"error":str(error),"groups_passed":0,"groups_total":len(QUESTIONS),"all_pass":False}
    details={}
    for key,expected in EXPECTED.items():
        value=answers.get(key,"MISSING")
        if type(expected) is bool:
            passed=type(value) is bool and value is expected
        elif type(expected) in (int,float):
            passed=type(value) in (int,float) and abs(value-expected)<1e-9
        else:
            passed=type(value) is str and value.strip().lower()==expected.lower()
        details[key]={"pass":passed,"actual":value,"expected":expected}
    groups={q["id"]:all(details[k]["pass"] for k in q["fields"]) for q in QUESTIONS}
    return {"valid_json":True,"answers":answers,"field_details":details,"groups":groups,
            "groups_passed":sum(groups.values()),"groups_total":len(groups),"all_pass":all(groups.values())}


def receiver_prompt(evidence):
    return ("You are a fresh receiver. Answer the questions from the supplied evidence only. "
            "Do not infer missing values. Use UNKNOWN when the evidence does not give an answer. "
            "Return a JSON object with every field named in the questions, directly at its top level. "
            "Use numbers in the units specified in each field name. Use booleans and the exact listed state strings. "
            "Write this JSON object inside the requested text field.\nEVIDENCE:\n"+evidence+
            "\nQUESTIONS:\n"+json.dumps(QUESTIONS,ensure_ascii=False))


def run():
    protocol=json.loads((HERE / "protocol.json").read_text())
    live_hash=stable_hash({"history":HISTORY,"questions":QUESTIONS,"expected":EXPECTED,"shared":SHARED,"formats":FORMATS})
    assert protocol["frozen_input_sha256"]==live_hash, "Frozen inputs changed."
    if (HERE / "calls").exists():
        raise RuntimeError("Calls already exist. Do not overwrite the run.")
    start=time.monotonic()
    calls=[]
    packets={arm:[] for arm in FORMATS}
    receivers={}
    errors=[]
    count=0

    def call(prompt,label):
        nonlocal count
        # Each submitted task has its own path. The main thread counts before submit.
        remaining=420-(time.monotonic()-start)
        if remaining<=0:
            raise RuntimeError("wall_time_cap")
        return common.complete(prompt,HERE / "calls" / label,timeout=min(75,remaining))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for stage,chunk in enumerate(HISTORY,1):
                futures={}
                for arm in FORMATS:
                    prior=packets[arm][-1]["text"] if packets[arm] else "NONE"
                    prompt=SHARED+"\n"+FORMATS[arm]+"\nPREVIOUS PACKET:\n"+prior+"\nNEW HISTORY:\n"+chunk
                    label=f"{arm}_handoff_{stage}"
                    count+=1
                    futures[arm]=(label,pool.submit(call,prompt,label))
                for arm,(label,future) in futures.items():
                    result=future.result()
                    calls.append({"label":label,**result})
                    packet={"handoff":stage,"text":result["text"],"tokens":common.token_size(result["text"]),"prompt_sha256":result["prompt_sha256"]}
                    packets[arm].append(packet)
                    common.dump(HERE / f"{arm}_packets.json",packets[arm])
                    print(json.dumps({"stage":stage,"arm":arm,"packet_tokens":packet["tokens"],"elapsed_seconds":round(time.monotonic()-start,1)}),flush=True)
            prompts={arm:receiver_prompt(packets[arm][-1]["text"]) for arm in FORMATS}
            prompts["full_history_control"]=receiver_prompt("\n\n".join(HISTORY))
            futures={}
            for arm,prompt in prompts.items():
                count+=1
                label=f"receiver_{arm}"
                futures[arm]=(label,pool.submit(call,prompt,label))
            for arm,(label,future) in futures.items():
                result=future.result()
                calls.append({"label":label,**result})
                receivers[arm]={"result":result,"grade":grade(result["text"])}
                print(json.dumps({"receiver":arm,"groups_passed":receivers[arm]["grade"]["groups_passed"],"groups_total":len(QUESTIONS)}),flush=True)
    except Exception as error:
        errors.append(repr(error))

    # Include failed call artifacts and their usage. Preserve every raw file.
    observed=[]
    for path in sorted((HERE / "calls").glob("*/result.json")):
        value=json.loads(path.read_text())
        observed.append({"label":path.parent.name,**value})
    budget_valid=all(p["tokens"]<=260 for arm in packets.values() for p in arm)
    complete=len(receivers)==3 and all(len(packets[a])==5 for a in FORMATS)
    tools_valid=all(r["ok"] and r["usage_complete"] and not r["native_tools"] for r in observed)
    control_valid=receivers.get("full_history_control",{}).get("grade",{}).get("all_pass",False)
    if errors or not complete or not budget_valid or not tools_valid or not control_valid:
        conclusion="inconclusive"
    elif receivers["ledger"]["grade"]["all_pass"] and not receivers["prose"]["grade"]["all_pass"]:
        conclusion="passed"
    else:
        conclusion="failed"
    native_usage={k:sum((r.get("usage") or {}).get(k,0) for r in observed) for k in ["input_tokens","output_tokens"]}
    results={"experiment":"E1","conclusion":conclusion,"elapsed_seconds":time.monotonic()-start,
             "calls_submitted":count,"calls_with_results":len(observed),"errors":errors,
             "packet_budget_valid":budget_valid,"complete":complete,"tools_and_usage_valid":tools_valid,
             "full_history_control_valid":control_valid,"packets":packets,"receivers":receivers,
             "native_usage":native_usage,"calls":observed,"frozen_input_sha256":live_hash}
    common.dump(HERE / "results.json",results)
    lines=["E1: Five recursive handoffs.",f"Hypothesis: {conclusion}.","The history is constructed. This result applies only to this run."]
    for arm in ["prose","ledger","full_history_control"]:
        if arm in receivers:
            g=receivers[arm]["grade"]
            lines.append(f"{arm}: {g['groups_passed']} of {g['groups_total']} question groups passed.")
    for arm,values in packets.items():
        lines.append(f"{arm} packet-size estimates (o200k_base): "+", ".join(str(p["tokens"]) for p in values)+".")
    lines += [f"Submitted calls: {count}. Calls with results: {len(observed)}.",
              f"Native input tokens: {native_usage['input_tokens']}. Native output tokens: {native_usage['output_tokens']}.",
              "Native usage includes a large fixed CLI context. Packet sizes are o200k_base estimates, not total model-call token use.",
              f"Elapsed time: {results['elapsed_seconds']:.1f} s.",
              "One history and two method variants were run. A second independent history would exceed the call cap.",
              "No general claim is supported by this small experiment."]
    if errors:
        lines.append("Errors: "+"; ".join(errors))
    (HERE / "report.txt").write_text("\n".join(lines)+"\n")
    print(json.dumps({"conclusion":conclusion,"calls":count,"native_usage":native_usage,"errors":errors}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--freeze",action="store_true")
    args=parser.parse_args()
    if args.freeze:
        print(json.dumps({"frozen":True,"hash":freeze()["frozen_input_sha256"]}))
    else:
        run()

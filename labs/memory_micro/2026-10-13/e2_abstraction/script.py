"""Private constructed screen: summary versus inferred semantic rules."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("memory_micro_common", HERE.parent / "common.py")
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)

HISTORIES = {
    "release": """The following chat records describe a release workflow. The tool checks are reliable. A content token identifies exact file bytes. Equal tokens mean equal bytes. Each build records which inputs it used. The word 'usable' is the tool's result, not an assistant opinion.
User: Build the atlas package. Use map (m1) and style (s1). Notes are not an input.
Tool: Atlas output exists. The build recorded map=m1, style=s1. Usability check: usable.
User: Change style to s2. Do not rebuild.
Tool: Atlas output still exists. Map=m1, style=s2. Usability check: unusable.
Assistant: Perhaps any earlier edit permanently invalidates a package.
User: Restore style to its original bytes s1. Do not rebuild.
Tool: Atlas output still exists. Map=m1, style=s1. Usability check: usable.
User: Change notes from n1 to n2. Do not rebuild.
Tool: Atlas output still exists. Map=m1, style=s1, notes=n2. Usability check: usable.
User: Delete map. Do not rebuild.
Tool: Atlas output still exists. Map is absent, style=s1. Usability check: unusable.
User: Restore map=m1. Then remove the atlas output itself.
Tool: Map=m1, style=s1. Atlas output is absent. Usability check: unusable.
User: Build beacon. Use scene=c7 and lighting=l2. The README is not an input.
Tool: Beacon output exists. The build recorded scene=c7, lighting=l2. Usability check: usable.
User: Change scene to c8. Do not rebuild.
Tool: Beacon output exists. Scene=c8, lighting=l2. Usability check: unusable.
User: Rebuild beacon with scene=c8 and lighting=l2.
Tool: Beacon output exists. The build recorded scene=c8, lighting=l2. Usability check: usable.
User: Change lighting to l3. Do not rebuild.
Tool: Beacon output exists. Scene=c8, lighting=l3. Usability check: unusable.
User: Restore lighting=l2. Change the README. Do not rebuild.
Tool: Beacon output exists. Scene=c8, lighting=l2. Usability check: usable.
User: Build comet from one input, mesh=q4.
Tool: Comet output exists. The build recorded mesh=q4. Usability check: usable.
User: Change mesh=q5, then restore q4. Do not rebuild.
Tool: Comet output exists. Mesh=q4. Usability check: usable.
""",
    "independent": """These chat records describe independent trials at a conversion desk. Each result was observed directly. No other trial results were recorded.
User: Run tool n17 on CSV with operator blue.
Tool: Result: usable.
User: Run tool n17 on JSON with operator blue.
Tool: Result: unusable.
User: Run tool m42 on CSV with operator blue.
Tool: Result: unusable.
User: Run tool m42 on JSON with operator green.
Tool: Result: usable.
User: Run tool p09 on XML with operator red.
Tool: Result: usable.
User: Run tool p09 on CSV with operator green.
Tool: Result: unusable.
Assistant: I wonder whether the operator's color controls everything.
User: We have not tested further combinations.
"""
}

QUESTIONS = {
    "release": [
        {"id":"T1", "split":"observed", "question":"Atlas was built from map=m1 and style=s1. Output exists. Current map=m1 and style=s2. Is it usable?"},
        {"id":"T2", "split":"observed", "question":"Atlas was built from map=m1 and style=s1. Output exists. Style changed to s2 and then returned to s1. Current map=m1 and style=s1. Is it usable?"},
        {"id":"T3", "split":"observed", "question":"Beacon was rebuilt from scene=c8 and lighting=l2. Output exists. Only the README changed. Scene=c8 and lighting=l2 remain. Is it usable?"},
        {"id":"U1", "split":"unseen", "question":"In the same release workflow, delta was built from graph=g3 and theme=t8. Output exists. Both inputs retain those tokens. An unrelated log changed. Is delta usable?"},
        {"id":"U2", "split":"unseen", "question":"Delta was built from graph=g3 and theme=t8. Output exists. Current graph=g4 and theme=t8. Is delta usable?"},
        {"id":"U3", "split":"unseen", "question":"Echo was built from audio=a1, captions=b2, and timing=d4. Output exists. Current audio=a1 and timing=d4, but captions are absent. Is echo usable?"},
        {"id":"U4", "split":"unseen", "question":"Echo was built from audio=a1, captions=b2, and timing=d4. Output exists. All three inputs changed and then each returned to its recorded token. No rebuild occurred. Is echo usable?"},
        {"id":"U5", "split":"unseen", "question":"Foxtrot was built from source=h6. Source still has h6. The output file is absent. Is foxtrot usable?"},
        {"id":"U6", "split":"unseen", "question":"Gamma was built from data=d1 and config=k1. Both changed. Gamma was then rebuilt from data=d2 and config=k2. Output exists. Current data=d2 and config=k2. Is gamma usable?"},
        {"id":"U7", "split":"unseen", "question":"Gamma was rebuilt from data=d2 and config=k2. Output exists. Current data=d1 and config=k1. Those are the tokens from its earlier build. Is gamma usable?"},
        {"id":"U8", "split":"unseen", "question":"Hotel was built from three inputs: alpha=x1, beta=x2, gamma=x3. Output exists. Alpha changed to x4 and then returned to x1. Beta changed to x5 and still has x5. Gamma remains x3. Is hotel usable?"}
    ],
    "independent": [
        {"id":"T1", "split":"observed", "question":"Tool n17 converts CSV with operator blue. Is the result usable?"},
        {"id":"T2", "split":"observed", "question":"Tool m42 converts CSV with operator blue. Is the result usable?"},
        {"id":"T3", "split":"observed", "question":"Tool p09 converts XML with operator red. Is the result usable?"},
        {"id":"U1", "split":"unseen", "question":"Tool n17 converts CSV with operator green. Is the result usable?"},
        {"id":"U2", "split":"unseen", "question":"Tool m42 converts JSON with operator blue. Is the result usable?"},
        {"id":"U3", "split":"unseen", "question":"Tool p09 converts JSON with operator red. Is the result usable?"},
        {"id":"U4", "split":"unseen", "question":"A new tool z88 converts CSV with operator blue. Is the result usable?"}
    ]
}

EXPECTED = {
    "release": {"T1":"unusable", "T2":"usable", "T3":"usable", "U1":"usable", "U2":"unusable", "U3":"unusable", "U4":"usable", "U5":"unusable", "U6":"usable", "U7":"unusable", "U8":"unusable"},
    "independent": {"T1":"usable", "T2":"unusable", "T3":"usable", "U1":"unknown", "U2":"unknown", "U3":"unknown", "U4":"unknown"}
}

ARMS = {
    "summary": "Write an ordinary concise handoff summary of the history. Preserve important facts, outcomes, corrections, and uncertainty. Do not invent evidence.",
    "rules": "Write a compact handoff packet that identifies a reusable semantic rule when the observations support one. State its conditions and exceptions. Distinguish inferred rules from observations. If no general rule is supported, retain specific cases and state the limits. Do not invent evidence."
}

def compressor_prompt(dataset, arm):
    return ("You prepare memory for a fresh agent. The agent must answer later questions about the same workflow. "
            "Use ASD-STE100 style. Use at most 220 tokens. Return only the memory packet. "
            + ARMS[arm] + "\nHISTORY:\n" + HISTORIES[dataset])

def receiver_prompt(dataset, packet):
    return ("You are a fresh receiver. Use only the supplied memory and each question. "
            "Answer usable, unusable, or unknown. Use unknown when the memory does not support a conclusion. "
            "New objects in the questions use the same workflow. Do not use tools. "
            "Return a JSON object whose keys are question IDs and whose values are those exact labels. "
            "Do not include explanations.\nMEMORY:\n" + packet + "\nQUESTIONS:\n" + json.dumps(QUESTIONS[dataset]))

def prepare():
    protocol = {
        "study":"E2 abstraction-focused compaction", "status":"private constructed screening study",
        "hypothesis":"A 220-token rule packet can transfer supported relationships to renamed objects and unseen combinations while retaining boundary conditions. It must avoid unsupported generalization on independent trials.",
        "model":"gpt-5.6-sol", "reasoning":"low", "histories":HISTORIES,
        "compressor_arms":ARMS, "compressor_prompts":{d:{a:compressor_prompt(d,a) for a in ARMS} for d in HISTORIES},
        "receiver_template":"Fresh receivers receive only packet plus questions. They never receive expected labels or generator code.",
        "questions":QUESTIONS, "expected":EXPECTED,
        "target_packet_tokens":220, "tokenizer":"o200k_base",
        "pass_rule":"All observed and unseen labels must be exact. The rule arm must use at most 220 packet tokens. Full-history baseline must pass. Passing this screen does not establish a general discovery capability.",
        "comparative_rule":"The rule arm has a benefit only if it meets the pass rule and either has fewer errors than summary or uses fewer tokens at equal zero error. Report ties and baseline failures.",
        "resource_cap":{"model_calls":10,"wall_seconds":420,"per_call_timeout_seconds":90,"concurrent_calls":3},
        "critical_boundaries":["Returning all inputs to captured bytes restores usability without a rebuild.","A rebuild resets the captured state.","One mismatch among several inputs still fails.","No observed trial supports labels for untested independent combinations."],
        "limitations":["Two constructed histories; one run per arm; no confidence interval.","The source workflow has a known constructed rule. Real histories can be incomplete or inconsistent.","The LLM can use pretrained knowledge; this does not identify a purely data-derived learning mechanism.","Packet tokens differ from native total input tokens because the CLI has fixed overhead."]
    }
    common.dump(HERE / "protocol.json", protocol)
    print(json.dumps({"prepared":True,"protocol_sha256":hashlib.sha256((HERE/"protocol.json").read_bytes()).hexdigest()}))

def score(dataset, result):
    try:
        answers=json.loads(result["text"])
        if not isinstance(answers,dict): raise ValueError("not_object")
    except Exception as exc:
        answers={}
        parse_error=str(exc)
    else:
        parse_error=None
    failures=[]
    counts={"observed":{"correct":0,"total":0},"unseen":{"correct":0,"total":0}}
    for q in QUESTIONS[dataset]:
        expected=EXPECTED[dataset][q["id"]]
        got=answers.get(q["id"])
        counts[q["split"]]["total"]+=1
        if got==expected: counts[q["split"]]["correct"]+=1
        else: failures.append({"id":q["id"],"split":q["split"],"expected":expected,"actual":got})
    if set(answers)!=set(EXPECTED[dataset]):
        failures.append({"schema":"unexpected_or_missing_keys"})
    return {"answers":answers,"counts":counts,"failures":failures,"parse_error":parse_error,"all_pass":not failures}

def run():
    protocol_path=HERE/"protocol.json"
    if not protocol_path.exists(): raise RuntimeError("Freeze protocol before calls.")
    protocol_sha=hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    protocol=json.loads(protocol_path.read_text())
    for d in HISTORIES:
        if protocol["histories"][d]!=HISTORIES[d]: raise RuntimeError("Protocol mismatch.")
    start=time.monotonic()
    outputs={}; errors={}; packets={}
    calls=[]

    def call(key,prompt):
        elapsed=time.monotonic()-start
        if elapsed>=420: raise RuntimeError("wall_cap")
        remaining=min(90,max(1,420-int(elapsed)))
        result=common.complete(prompt,HERE/"calls"/key,timeout=remaining)
        return key,result

    initial={}
    for d in HISTORIES:
        initial[d+"_full"]=receiver_prompt(d,HISTORIES[d])
        for a in ARMS: initial[d+"_"+a+"_write"]=compressor_prompt(d,a)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(call,k,p):k for k,p in initial.items()}
        for future in as_completed(futures):
            key=futures[future]
            try: k,result=future.result(); outputs[k]=result
            except Exception as exc: errors[key]=str(exc)
    for d in HISTORIES:
        for a in ARMS:
            key=d+"_"+a+"_write"
            if key in outputs: packets[(d,a)]=outputs[key]["text"]
    follow={d+"_"+a+"_read":receiver_prompt(d,p) for (d,a),p in packets.items()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(call,k,p):k for k,p in follow.items()}
        for future in as_completed(futures):
            key=futures[future]
            try: k,result=future.result(); outputs[k]=result
            except Exception as exc: errors[key]=str(exc)
    for result_path in sorted((HERE/"calls").glob("*/result.json")):
        result=json.loads(result_path.read_text()); result["call"]=result_path.parent.name; calls.append(result)
    evaluations={}
    sizes={d:{"history_tokens":common.token_size(h),"history_utf8_bytes":len(h.encode())} for d,h in HISTORIES.items()}
    for d in HISTORIES:
        for a in ["full","summary","rules"]:
            key=d+"_full" if a=="full" else d+"_"+a+"_read"
            if key in outputs: evaluations[d+"_"+a]=score(d,outputs[key])
        for a in ARMS:
            if (d,a) in packets:
                p=packets[(d,a)]
                sizes[d][a]={"packet_tokens":common.token_size(p),"packet_utf8_bytes":len(p.encode()),"within_target":common.token_size(p)<=220}
    usage={"input_tokens":sum((c.get("usage") or {}).get("input_tokens",0) for c in calls),"output_tokens":sum((c.get("usage") or {}).get("output_tokens",0) for c in calls),"complete":len(calls)==10 and all(c["usage_complete"] for c in calls)}
    passes={}
    for d in HISTORIES:
        full=evaluations.get(d+"_full",{}).get("all_pass",False)
        for a in ARMS:
            passes[d+"_"+a]=bool(full and evaluations.get(d+"_"+a,{}).get("all_pass",False) and sizes[d].get(a,{}).get("within_target",False))
    result={"protocol_sha256":protocol_sha,"elapsed_seconds":time.monotonic()-start,"calls_attempted":len(list((HERE/"calls").glob("*/prompt.txt"))),"calls_completed":len(calls),"errors":errors,"native_usage":usage,"call_usage":{c["call"]:{"usage":c["usage"],"elapsed_seconds":c["elapsed_seconds"],"ok":c["ok"],"errors":c["errors"]} for c in calls},"packets":{d:{a:packets.get((d,a)) for a in ARMS} for d in HISTORIES},"sizes":sizes,"evaluations":evaluations,"pass_rule":passes,"full_results":{c["call"]:c for c in calls}}
    common.dump(HERE/"results.json",result)
    lines=["E2: Private constructed abstraction screen", "This screen does not prove general law discovery.", "Protocol SHA256: "+protocol_sha, "Calls attempted: "+str(result["calls_attempted"]), "Wall time: %.2f seconds"%result["elapsed_seconds"],"Native usage: "+json.dumps(usage),"Packet sizes use o200k_base. Native CLI input has fixed overhead."]
    for d in HISTORIES:
        lines.append("\nHistory: "+d+". Size: "+str(sizes[d]["history_tokens"])+" tokens.")
        for a in ["full","summary","rules"]:
            e=evaluations.get(d+"_"+a)
            lines.append(a+": "+json.dumps({"score":e,"size":sizes[d].get(a),"pass":passes.get(d+"_"+a)}))
    lines.append("\nFailures: "+json.dumps(errors))
    lines.append("Next test: Repeat with held-out workflow families and histories where an apparent rule has a late exception. Test whether a packet changes its rule or preserves an explicit uncertainty.")
    lines.append("Limits: One run per arm. Histories are constructed. Receiver questions provide state fields. The LLM may use pretrained knowledge. A shorter packet does not establish total token savings.")
    (HERE/"report.txt").write_text("\n".join(lines)+"\n")
    print(json.dumps({k:result[k] for k in ["calls_attempted","calls_completed","elapsed_seconds","native_usage","pass_rule","errors"]},indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("mode",choices=["prepare","run"]); args=parser.parse_args()
    prepare() if args.mode=="prepare" else run()

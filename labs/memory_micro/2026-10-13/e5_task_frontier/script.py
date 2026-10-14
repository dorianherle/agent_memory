"""Private task-resumption screen. No production code is modified."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location("memory_micro_common",HERE.parent/"common.py")
common=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)

HISTORIES={
"cancel_export":"""User: Fix the export cancellation bug. A cancelled export must release its temporary URLs and retain the last valid preview. Prepare a draft review only after the focused cancellation tests and the production build pass on the final source. Do not deploy.
Assistant: I will inspect the export controller and cancellation callback.
Tool: The controller allocates a temporary URL before the worker completes. Cancellation can skip its cleanup callback.
Assistant: A cleanup function should release the URL in both success and cancellation paths.
Tool: First change saved as revision ui-16. The cleanup callback now runs on cancellation.
Tool: Focused tests on ui-16: 28 passed, 1 failed. The failure says that repeated cancellation calls revoke the same URL twice.
Assistant: I will make cleanup safe for a second call.
Tool: Change saved as ui-17. The callback clears the temporary URL after it releases it.
Tool: Focused cancellation tests on ui-17: 29 passed, 0 failed. Process exit status: 0.
Tool: Typecheck on ui-17: exit status 2. Existing parser.spec.ts has an incompatible fixture type. The controller change is not named in the errors.
User: Keep the parser typecheck failure visible. Do not mark it fixed. For this task, the acceptance checks remain the focused cancellation tests and production build.
Tool: Production build on ui-17: exit status 0. Build completed.
Assistant: The controller tests and build pass. The parser typecheck failure remains separate.
User: I noticed another case. A late worker callback arrives after cancellation and replaces the last valid preview. Fix that before preparing the draft review.
Tool: Source changed to ui-18. The worker callback now checks the cancellation flag before updating the preview. No test command has run on ui-18.
Assistant: The previous tests should cover this change. All checks passed, and the task is ready for review.
Tool: Command launched: npm run build. Source at launch: ui-18. The session was interrupted. No terminal completion, exit status, or resulting build artifact was recorded.
User: At resumption, first run the focused cancellation tests on ui-18. Then run the production build and inspect its exit status. Missing verification on the current revision blocks the draft review. The parser typecheck failure remains unresolved, but it is not an acceptance blocker for this task. Do not claim that the interrupted build passed.
""",
"backfill_cache":"""User: Implement migration from the old cache key to the tenant-scoped cache key. Keep old entries readable until backfill finishes. This local migration task is complete only after current-source tests pass, the backfill has a confirmed result, and the post-backfill consistency audit passes. Do not publish a release yet.
Assistant: I will inspect lookup and backfill logic.
Tool: Old keys omit tenant ID. The migration copies entries to a tenant-scoped key and retains the old key for fallback reads.
Tool: Source changed to cache-40. The lookup now prefers the tenant-scoped key and falls back to the old key.
Tool: Cache unit tests on cache-40: 41 passed, 1 failed. A tenant with an empty cache reads a different tenant's old entry.
Assistant: The fallback needs an owner check.
Tool: Source changed to cache-41. The fallback requires the entry's recorded owner to match the requested tenant.
Tool: Cache unit tests on cache-41: 42 passed, 0 failed. Process exit status: 0.
Tool: Migration dry-run on cache-41 completed with exit status 0. The dry-run did not change data. It found 12,400 candidate entries.
User: The database review found that one tenant can be renamed while the backfill runs. Add a stable tenant identifier to the new key before running the real backfill.
Tool: Source changed to cache-42. New keys use the stable tenant identifier. No tests have run on cache-42.
Assistant: This is a small substitution. The tests pass, and the backfill should be safe.
Tool: Real backfill started against the local fixture database. Command: python tools/backfill_cache.py --apply --job local-77. Source at launch: cache-42. The command performs writes. It must not be started twice for the same job.
Tool: Progress: local-77 wrote its first batch. The terminal connection was lost. No final process result or completion marker was recorded.
Assistant: The backfill finished successfully. The migration is complete.
User: We do not have a completion record. At resumption, first check whether job local-77 still runs and inspect its status record. Do not start another apply operation until the job state is known. After that, verify cache-42 and run the post-backfill consistency audit. Neither current-source verification nor the audit has a result. Unknown job state is the immediate blocker.
"""
}

CONTINUATION="""Continue this development task. Choose the one immediate next action. State the current source revision and which completion claims the supplied memory supports.
Return only a JSON object with these fields:
next_action: one of recheck_current_source, resolve_interrupted_operation, publish_review_or_release, change_implementation, wait_for_user, retrieve_missing_state.
current_revision: the exact recorded source revision, or unknown.
claims: an object with keys earlier_tests_passed, current_tests_passed, interrupted_operation_completed, task_complete, assistant_success_verified. Use supported or not_established for each value. A past passing test does not by itself establish a later source result. An assistant statement is not a tool completion record.
blocker: one of current_revision_unverified, unknown_backfill_state, none, unknown.
Do not use tools. Do not include explanations."""

EXPECTED={
"cancel_export":{"next_action":"recheck_current_source","current_revision":"ui-18","claims":{"earlier_tests_passed":"supported","current_tests_passed":"not_established","interrupted_operation_completed":"not_established","task_complete":"not_established","assistant_success_verified":"not_established"},"blocker":"current_revision_unverified"},
"backfill_cache":{"next_action":"resolve_interrupted_operation","current_revision":"cache-42","claims":{"earlier_tests_passed":"supported","current_tests_passed":"not_established","interrupted_operation_completed":"not_established","task_complete":"not_established","assistant_success_verified":"not_established"},"blocker":"unknown_backfill_state"}
}

ARM_PROMPTS={
"knowledge":"Write a knowledge-focused summary. Summarize learned findings, design decisions, important constraints, and uncertainty. Include the details you judge necessary for a fresh agent to continue correctly.",
"checkpoint":"Write a task checkpoint. Include the current objective, exact source revision, completed and failed verification with its revision, interrupted action and missing result, next required action, and unresolved blocker. Preserve important constraints and uncertainty."
}

def writer_prompt(dataset):
    return ("Prepare two alternative memory packets for fresh agents that must continue the task. "
            "Use ASD-STE100 style. Each packet must use at most 180 tokens. Do not invent evidence or completed actions. "
            "Return a JSON object with string fields knowledge and checkpoint.\nKNOWLEDGE ARM:\n"+ARM_PROMPTS["knowledge"]+
            "\nCHECKPOINT ARM:\n"+ARM_PROMPTS["checkpoint"]+"\nHISTORY:\n"+HISTORIES[dataset])

def receiver_prompt(memory):
    return "You are a fresh receiver. Use only this memory.\nMEMORY:\n"+memory+"\nCONTINUATION REQUEST:\n"+CONTINUATION

def prepare():
    protocol={
        "study":"E5 task frontier", "status":"private constructed task-resumption screen",
        "hypothesis":"A checkpoint preserves the current task frontier and supports correct resumption more reliably than a knowledge-focused summary under the same 180-token target.",
        "model":"gpt-5.6-sol","reasoning":"low","histories":HISTORIES,
        "arm_prompts":ARM_PROMPTS,"writer_prompts":{d:writer_prompt(d) for d in HISTORIES},
        "continuation_request":CONTINUATION,"expected":EXPECTED,
        "target_packet_tokens":180,"tokenizer":"o200k_base",
        "design":"One bundled writer call produces both packets for each history. Three fresh isolated receivers then read full history, knowledge packet, or checkpoint packet. Receivers never see expected answers or generator code.",
        "pass_rule":"Every output field must match the frozen expected answer. A compact arm must also meet its 180-token target. The full-history control must pass before a compact arm is interpreted as a successful task transfer.",
        "comparative_rule":"A checkpoint benefit requires fewer field errors than knowledge under the same target. If both pass, report a tie on resumption correctness and compare actual packet sizes. A target breach alone is a size failure.",
        "resource_cap":{"model_calls":8,"wall_seconds":300,"per_call_timeout_seconds":80,"concurrent_calls":3},
        "limitations":["Two constructed histories and one receiver per arm. No confidence interval.","The writer generates both packets in one call. This can affect both arms.","The receiver request explicitly describes the evidence standard. This cue is identical across all arms.","The task histories explicitly record the required first action. This measures preservation of task state.","Correct plans and evidence classification do not establish end-to-end coding performance.","o200k_base packet-size estimates are not native model-call usage."]
    }
    common.dump(HERE/"protocol.json",protocol)
    print(json.dumps({"prepared":True,"protocol_sha256":hashlib.sha256((HERE/"protocol.json").read_bytes()).hexdigest()}))

def score(dataset,result):
    try:
        actual=json.loads(result["text"])
        if not isinstance(actual,dict): raise ValueError("not_object")
    except Exception as exc:
        actual={}; parse_error=str(exc)
    else: parse_error=None
    expected=EXPECTED[dataset]
    fields={"next_action":expected["next_action"],"current_revision":expected["current_revision"],"blocker":expected["blocker"]}
    for k,v in expected["claims"].items(): fields["claims."+k]=v
    failures=[]; correct=0
    for path,value in fields.items():
        if path.startswith("claims."):
            claims=actual.get("claims",{})
            got=claims.get(path.split(".",1)[1]) if isinstance(claims,dict) else None
        else: got=actual.get(path)
        if got==value: correct+=1
        else: failures.append({"field":path,"expected":value,"actual":got})
    return {"answer":actual,"correct_fields":correct,"total_fields":len(fields),"failures":failures,"parse_error":parse_error,"all_pass":not failures}

def run():
    protocol_path=HERE/"protocol.json"
    if not protocol_path.exists(): raise RuntimeError("Freeze protocol before calls.")
    protocol_sha=hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    protocol=json.loads(protocol_path.read_text())
    if protocol["histories"]!=HISTORIES or protocol["expected"]!=EXPECTED: raise RuntimeError("Protocol mismatch.")
    start=time.monotonic(); outputs={}; errors={}; packets={}; calls=[]
    def call(key,prompt):
        elapsed=time.monotonic()-start
        if elapsed>=300: raise RuntimeError("wall_cap")
        result=common.complete(prompt,HERE/"calls"/key,timeout=min(80,max(1,300-int(elapsed))))
        return key,result
    initial={}
    for d in HISTORIES:
        initial[d+"_full"]=receiver_prompt(HISTORIES[d])
        initial[d+"_write"]=writer_prompt(d)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(call,k,p):k for k,p in initial.items()}
        for future in as_completed(futures):
            key=futures[future]
            try: k,r=future.result(); outputs[k]=r
            except Exception as exc: errors[key]=str(exc)
    for d in HISTORIES:
        key=d+"_write"
        if key not in outputs: continue
        try:
            p=json.loads(outputs[key]["text"])
            if set(p)!={"knowledge","checkpoint"} or any(not isinstance(v,str) for v in p.values()): raise ValueError("invalid_packet_schema")
            for a,v in p.items(): packets[(d,a)]=v
        except Exception as exc: errors[key+"_parse"]=str(exc)
    follow={d+"_"+a+"_read":receiver_prompt(p) for (d,a),p in packets.items()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(call,k,p):k for k,p in follow.items()}
        for future in as_completed(futures):
            key=futures[future]
            try: k,r=future.result(); outputs[k]=r
            except Exception as exc: errors[key]=str(exc)
    for path in sorted((HERE/"calls").glob("*/result.json")):
        r=json.loads(path.read_text()); r["call"]=path.parent.name; calls.append(r)
    sizes={d:{"history_tokens_estimate":common.token_size(h),"history_utf8_bytes":len(h.encode())} for d,h in HISTORIES.items()}
    evaluations={}; passes={}
    for d in HISTORIES:
        for a in ["full","knowledge","checkpoint"]:
            key=d+"_full" if a=="full" else d+"_"+a+"_read"
            if key in outputs: evaluations[d+"_"+a]=score(d,outputs[key])
        for a in ARM_PROMPTS:
            if (d,a) in packets:
                p=packets[(d,a)]; n=common.token_size(p)
                sizes[d][a]={"packet_tokens_estimate":n,"packet_utf8_bytes":len(p.encode()),"within_target":n<=180}
        baseline=evaluations.get(d+"_full",{}).get("all_pass",False)
        for a in ARM_PROMPTS:
            passes[d+"_"+a]=bool(baseline and evaluations.get(d+"_"+a,{}).get("all_pass",False) and sizes[d].get(a,{}).get("within_target",False))
    usage={"input_tokens":sum((c.get("usage") or {}).get("input_tokens",0) for c in calls),"output_tokens":sum((c.get("usage") or {}).get("output_tokens",0) for c in calls),"complete":len(calls)==8 and all(c["usage_complete"] for c in calls)}
    interpretation=[]
    for d in HISTORIES:
        k=evaluations.get(d+"_knowledge",{}); c=evaluations.get(d+"_checkpoint",{})
        ke=len(k.get("failures",[])); ce=len(c.get("failures",[]))
        interpretation.append({"history":d,"knowledge_field_errors":ke,"checkpoint_field_errors":ce,"checkpoint_fewer_errors":ce<ke,"correctness_tie":bool(k and c and ke==ce)})
    result={"protocol_sha256":protocol_sha,"elapsed_seconds":time.monotonic()-start,"calls_attempted":len(list((HERE/"calls").glob("*/prompt.txt"))),"calls_completed":len(calls),"errors":errors,"native_usage":usage,"call_usage":{c["call"]:{"usage":c["usage"],"elapsed_seconds":c["elapsed_seconds"],"ok":c["ok"],"errors":c["errors"]} for c in calls},"packets":{d:{a:packets.get((d,a)) for a in ARM_PROMPTS} for d in HISTORIES},"sizes":sizes,"evaluations":evaluations,"pass_rule":passes,"interpretation":interpretation,"full_results":{c["call"]:c for c in calls}}
    common.dump(HERE/"results.json",result)
    lines=["E5: Private task-frontier screen","Correct state preservation is not end-to-end coding performance.","Protocol SHA256: "+protocol_sha,"Calls attempted: "+str(result["calls_attempted"]),"Wall time: %.2f seconds"%result["elapsed_seconds"],"Native usage: "+json.dumps(usage),"Packet sizes are o200k_base estimates. They are separate from native call tokens."]
    for d in HISTORIES:
        lines.append("\nHistory: "+d+". Raw size estimate: "+str(sizes[d]["history_tokens_estimate"])+" tokens.")
        for a in ["full","knowledge","checkpoint"]:
            lines.append(a+": "+json.dumps({"score":evaluations.get(d+"_"+a),"size":sizes[d].get(a),"pass":passes.get(d+"_"+a)}))
    lines.append("\nComparison: "+json.dumps(interpretation))
    lines.append("Failures: "+json.dumps(errors))
    lines.append("Next test: Use actual interrupted development tasks. Have fresh agents execute their plans in disposable checkouts. Compare correct completion, repeated operations, and false completion claims.")
    lines.append("Limits: One run per arm. Two constructed histories. Both packets share a writer call. The evidence standard is stated in each continuation request. The first required action is explicit in the original histories. This tests preservation, not autonomous discovery or coding performance.")
    (HERE/"report.txt").write_text("\n".join(lines)+"\n")
    print(json.dumps({k:result[k] for k in ["calls_attempted","calls_completed","elapsed_seconds","native_usage","pass_rule","interpretation","errors"]},indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("mode",choices=["prepare","run"]); args=parser.parse_args()
    prepare() if args.mode=="prepare" else run()

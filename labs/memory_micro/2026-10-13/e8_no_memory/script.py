"""E8: adaptive no-memory control on the frozen E2 fixture."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
E2=HERE.parent / "e2_abstraction"
spec=importlib.util.spec_from_file_location("memory_micro_common",HERE.parent / "common.py")
common=importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(common)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    source=json.loads((E2 / "protocol.json").read_text())
    old_results=json.loads((E2 / "results.json").read_text())
    prompts={}; fingerprints={}; comparisons={}
    for dataset in ["release","independent"]:
        original=E2 / "calls" / (dataset+"_rules_read") / "prompt.txt"
        prefix,rest=original.read_text().split("\nMEMORY:\n",1)
        packet,questions=rest.split("\nQUESTIONS:\n",1)
        assert json.loads(questions)==source["questions"][dataset],"Question mismatch."
        # Only this supplied memory field changes. Instruction and questions remain exact.
        prompts[dataset]=prefix+"\nMEMORY:\n"+""+"\nQUESTIONS:\n"+questions
        fingerprints[str(original)]=digest(original)
        comparisons[dataset]={arm:old_results["evaluations"][dataset+"_"+arm]["counts"] for arm in ["full","summary","rules"]}
    fingerprints[str(E2 / "script.py")]=digest(E2 / "script.py")
    fingerprints[str(E2 / "protocol.json")]=digest(E2 / "protocol.json")
    fingerprints[str(E2 / "results.json")]=digest(E2 / "results.json")
    protocol={"experiment":"E8 no-memory diagnostic","adaptive_control":True,"independent_test":False,
              "hypothesis":"The release receiver reproduces every frozen E2 expected label with no supplied memory. A pass detects that this E2 fixture does not require the transferred packet for this receiver.",
              "design":"Reuse E2's exact receiver instruction and question text. Replace only the MEMORY field with an empty string. Make one isolated call for each of its two datasets. No history, packet, expected answer, generator, or new guessing instruction reaches the model.",
              "frozen_prompts":prompts,"questions":source["questions"],"expected_hidden_from_calls":source["expected"],
              "existing_E2_scores":comparisons,"source_file_sha256":fingerprints,
              "pass_rule":"Pass the stated hypothesis if release observed and unseen labels all match the frozen expected labels. Fail it if any release label differs. Inconclusive if a call fails or output cannot be scored. Report the independent dataset and each split separately. Correct independent unseen unknowns alone do not show knowledge transfer.",
              "resource_cap":{"maximum_model_calls":2,"maximum_wall_seconds":120,"per_call_timeout_seconds":65,"model":"gpt-5.6-sol","reasoning":"low"},
              "limits":["Adaptive control on a known constructed fixture.","One call per dataset. No confidence interval.","Low no-memory scores do not prove that E2 learned solely from the source history.","Questions can supply state information. Model prior knowledge can still contribute."],}
    protocol["frozen_prompt_sha256"]={d:hashlib.sha256(p.encode()).hexdigest() for d,p in prompts.items()}
    common.dump(HERE / "protocol.json",protocol)
    return protocol


def score(dataset,text,protocol):
    try:
        answers=json.loads(text)
        if not isinstance(answers,dict):raise ValueError("not_object")
        error=None
    except Exception as exc:answers={};error=str(exc)
    counts={"observed":{"correct":0,"total":0},"unseen":{"correct":0,"total":0}};failures=[]
    for q in protocol["questions"][dataset]:
        wanted=protocol["expected_hidden_from_calls"][dataset][q["id"]]
        got=answers.get(q["id"])
        counts[q["split"]]["total"]+=1
        if got==wanted:counts[q["split"]]["correct"]+=1
        else:failures.append({"id":q["id"],"split":q["split"],"expected":wanted,"actual":got})
    if set(answers)!=set(protocol["expected_hidden_from_calls"][dataset]):failures.append({"schema":"unexpected_or_missing_keys"})
    return {"answers":answers,"counts":counts,"failures":failures,"parse_error":error,"all_pass":not failures}


def run():
    p=json.loads((HERE / "protocol.json").read_text())
    for name,sha in p["source_file_sha256"].items():assert digest(Path(name))==sha,"Source changed after freeze."
    if (HERE / "calls").exists():raise RuntimeError("Calls already exist. Do not overwrite.")
    start=time.monotonic();errors={};outputs={};scores={}
    def call(dataset):
        remaining=120-(time.monotonic()-start)
        if remaining<=0:raise RuntimeError("wall_time_cap")
        return common.complete(p["frozen_prompts"][dataset],HERE / "calls" / (dataset+"_no_memory"),timeout=min(65,remaining))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(call,d):d for d in ["release","independent"]}
        for future in as_completed(futures):
            dataset=futures[future]
            try:outputs[dataset]=future.result();scores[dataset]=score(dataset,outputs[dataset]["text"],p)
            except Exception as error:errors[dataset]=repr(error)
    logs=[{"label":path.parent.name,**json.loads(path.read_text())} for path in sorted((HERE / "calls").glob("*/result.json"))]
    usage={k:sum((r.get("usage") or {}).get(k,0) for r in logs) for k in ["input_tokens","output_tokens"]}
    valid_calls=len(logs)==2 and all(r["ok"] and r["usage_complete"] and not r["native_tools"] for r in logs)
    if errors or not valid_calls or len(scores)!=2 or any(s["parse_error"] for s in scores.values()):conclusion="inconclusive"
    elif scores["release"]["all_pass"]:conclusion="passed"
    else:conclusion="failed"
    comparison={d:{"no_memory":scores.get(d,{}).get("counts"),"E2":p["existing_E2_scores"][d]} for d in ["release","independent"]}
    result={"experiment":"E8","adaptive_control":True,"conclusion":conclusion,"elapsed_seconds":time.monotonic()-start,
            "scores":scores,"comparison":comparison,"errors":errors,"valid_calls":valid_calls,"native_usage":usage,
            "calls":logs,"frozen_prompt_sha256":p["frozen_prompt_sha256"]}
    common.dump(HERE / "results.json",result)
    lines=["E8: No-memory control on the known E2 fixture.",f"Hypothesis: {conclusion}.","This is an adaptive control. It is not a new independent test."]
    for dataset in ["release","independent"]:
        if dataset in scores:
            for split in ["observed","unseen"]:
                c=scores[dataset]["counts"][split]
                lines.append(f"No memory, {dataset}, {split}: {c['correct']} of {c['total']} correct.")
        lines.append("Existing E2 scores, "+dataset+": "+json.dumps(p["existing_E2_scores"][dataset]))
    lines += ["The instruction and questions were unchanged. The supplied MEMORY field was empty.",
              "Correct unknown answers on independent unseen cases do not show transferred case knowledge.",
              f"Calls with results: {len(logs)}. Native input tokens: {usage['input_tokens']}. Native output tokens: {usage['output_tokens']}.",
              f"Elapsed time: {result['elapsed_seconds']:.1f} s.",
              "A low no-memory score cannot prove a purely source-derived learning mechanism."]
    if errors:lines.append("Errors: "+json.dumps(errors))
    (HERE / "report.txt").write_text("\n".join(lines)+"\n")
    print(json.dumps({"conclusion":conclusion,"comparison":comparison,"native_usage":usage,"errors":errors}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--freeze",action="store_true");args=parser.parse_args()
    if args.freeze:
        p=freeze();print(json.dumps({"frozen":True,"prompt_hashes":p["frozen_prompt_sha256"]}))
    else:run()

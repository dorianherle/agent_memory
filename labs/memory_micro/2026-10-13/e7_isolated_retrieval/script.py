"""Run fresh, isolated receivers on a known E4 fixture."""
from pathlib import Path
import sys,json,time,hashlib,re,importlib.util,concurrent.futures,traceback
ROOT=Path(__file__).resolve().parent
E4=ROOT.parent/"e4_query_memory"
sys.path.insert(0,str(E4/"deps")); sys.path.insert(0,str(ROOT.parent))
from common import complete,dump,token_size
spec=importlib.util.spec_from_file_location("e4_retriever",E4/"script.py")
e4=importlib.util.module_from_spec(spec); spec.loader.exec_module(e4)
START=time.monotonic()
IDS=["quarry_correction","quarry_rare_lateral","quarry_tail","quarry_absent"]
NORMALIZATION={"135 °c":"135 c","135°c":"135 c","135 degrees c":"135 c","135 degrees celsius":"135 c","135 celsius":"135 c","11 min":"11 minutes","11 mins":"11 minutes","11 minute":"11 minutes","660 s":"11 minutes","660 seconds":"11 minutes","no; unknown.":"no; unknown","unknown.":"unknown","not specified":"unknown","not set":"unknown","not known":"unknown"}
def normalize(value):
    s=re.sub(r"\s+"," ",str(value).strip().lower())
    return NORMALIZATION.get(s,s)
def main():
    archive=json.loads((E4/"archive.json").read_text())
    frozen_e4=json.loads((E4/"protocol.json").read_text())
    tests=[next(x for x in frozen_e4["hidden_tests"] if x["id"]==name) for name in IDS]
    packet=(E4/"global_packet_capped.txt").read_text().strip()
    raw="\n".join(e4.line(row) for row in archive)
    retrieval=[e4.retrieve(test["query"],archive) for test in tests]
    inputs=[]
    for arm in ["global","retrieval","full_history"]:
        for index,test in enumerate(tests):
            memory=packet if arm=="global" else retrieval[index]["context"] if arm=="retrieval" else raw
            inputs.append({"arm":arm,"test":test,"memory":memory,"memory_tokens":token_size(memory),"retrieved_records":retrieval[index]["record_count"] if arm=="retrieval" else None,"assigned_sources":retrieval[index]["selected_sources"] if arm=="retrieval" else None})
    original_hashes={str(path.relative_to(ROOT.parent)):hashlib.sha256(path.read_bytes()).hexdigest() for path in [E4/"script.py",E4/"archive.json",E4/"global_packet_capped.txt",E4/"protocol.json"]}
    protocol={"experiment":"E7 isolated retrieval diagnostic","frozen_before_calls":True,"classification":"Adaptive diagnostic on the known E4 fixture. Not an independent confirmation or abstraction test.","model":"gpt-5.6-sol","reasoning":"low","fixed_query_ids":IDS,"tests":tests,"source_hashes":original_hashes,"global_packet":packet,"global_tokens":token_size(packet),"packet_limit_tokens":180,"retrieval_algorithm":"Exact reuse of E4 retrieve(query, archive)","retrieval":retrieval,"calls":12,"concurrency":4,"maximum_seconds":180,"receiver_isolation":"One query and its assigned memory in each separate fresh model-only helper call. No other query or context is supplied.","semantic_normalization_map":NORMALIZATION,"other_normalization":"Lowercase, strip outer whitespace, and combine repeated whitespace. No other answer equivalences are accepted.","source_scoring":"Exact original source ID; case-insensitive NONE accepted.","expected_values":"Original E4 values reused without changes.","tokenizer":"o200k_base estimate; native usage recorded separately","blind_spots":["Known fixture selected after E4 outcomes","Four questions","One model","One repetition","Fixed project-name lexical retrieval","Global packet produced by an earlier adaptive writer","No new writer or training","No native task continuation","No fundamental-rule discovery","No statistical confirmation"]}
    dump(ROOT/"protocol.json",protocol)
    protocol_hash=hashlib.sha256((ROOT/"protocol.json").read_bytes()).hexdigest()
    dump(ROOT/"inputs.json",inputs)
    results={"experiment":protocol["experiment"],"protocol_sha256":protocol_hash,"classification":protocol["classification"],"status":"running","calls":[],"normalization_map":NORMALIZATION,"blind_spots":protocol["blind_spots"]}
    dump(ROOT/"results.json",results)
    def worker(item):
        test=item["test"]
        prompt="Answer this one question using only the supplied project evidence. Treat different project names as different projects. Use the latest correction. If the answer is absent, answer UNKNOWN and source NONE. Return only JSON: {\"answer\":\"short value with unit, or Yes/No\",\"source\":\"source ID\"}. For an unknown lateral rate, answer exactly No; unknown.\nQuestion: "+test["query"]+"\nEvidence:\n"+item["memory"]
        row={"arm":item["arm"],"id":test["id"],"query":test["query"],"expected_answer":test["answer"],"expected_source":test["source"],"memory_tokens":item["memory_tokens"],"memory_target_breach":item["memory_tokens"]>180 if item["arm"]!="full_history" else None,"retrieved_records":item["retrieved_records"],"assigned_sources":item["assigned_sources"]}
        try:
            remaining=180-(time.monotonic()-START)
            if remaining<10: raise RuntimeError("The three-minute cap was reached.")
            response=complete(prompt,ROOT/(item["arm"]+"_"+test["id"]),timeout=min(50,max(10,int(remaining))),model=protocol["model"])
            text=response["text"].strip()
            if text.startswith("```"): text=re.sub(r"^```(?:json)?\s*|\s*```$","",text)
            parsed=json.loads(text)
            row.update({"ok":True,"response":parsed,"exact_text":response["text"],"semantic_answer_pass":normalize(parsed.get("answer"))==normalize(test["answer"]),"source_pass":str(parsed.get("source","")).upper()==test["source"],"native_usage":response["usage"],"elapsed_seconds":response["elapsed_seconds"],"native_tools":response["native_tools"]})
            row["joint_pass"]=row["semantic_answer_pass"] and row["source_pass"]
        except Exception:
            row.update({"ok":False,"error":traceback.format_exc(),"semantic_answer_pass":False,"source_pass":False,"joint_pass":False,"native_usage":None})
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(worker,item):item for item in inputs}
        for future in concurrent.futures.as_completed(futures):
            row=future.result(); results["calls"].append(row); dump(ROOT/"results.json",results)
            print(row["arm"],row["id"],row["semantic_answer_pass"],row["source_pass"],flush=True)
    results["arms"]={}
    for arm in ["global","retrieval","full_history"]:
        rows=[row for row in results["calls"] if row["arm"]==arm]
        results["arms"][arm]={"semantic_answers":sum(row["semantic_answer_pass"] for row in rows),"exact_sources":sum(row["source_pass"] for row in rows),"joint":sum(row["joint_pass"] for row in rows),"total":len(rows),"total_memory_tokens":sum(row["memory_tokens"] for row in rows),"largest_memory_tokens":max(row["memory_tokens"] for row in rows),"native_usage":{key:sum((row["native_usage"] or {}).get(key,0) for row in rows) for key in ["input_tokens","output_tokens"]}}
    results["native_usage_total"]={key:sum((row["native_usage"] or {}).get(key,0) for row in results["calls"]) for key in ["input_tokens","output_tokens"]}
    results["native_usage_complete"]=len(results["calls"])==12 and all(row["ok"] for row in results["calls"])
    results["protocol_unchanged"]=hashlib.sha256((ROOT/"protocol.json").read_bytes()).hexdigest()==protocol_hash
    results["e4_sources_unchanged"]=all(hashlib.sha256((ROOT.parent/path).read_bytes()).hexdigest()==value for path,value in original_hashes.items())
    results["elapsed_seconds"]=time.monotonic()-START
    results["status"]="completed" if results["native_usage_complete"] else "inconclusive"
    dump(ROOT/"results.json",results)
    report=["E7 isolated retrieval diagnostic","Status: "+results["status"],protocol["classification"],"Each call received one query and one memory packet. No cross-query batch context was supplied."]
    for arm,value in results["arms"].items(): report.append(f"{arm}: semantic answers {value['semantic_answers']}/4; exact sources {value['exact_sources']}/4; joint {value['joint']}/4; total memory {value['total_memory_tokens']} estimated tokens; largest packet {value['largest_memory_tokens']} tokens; native usage {json.dumps(value['native_usage'])}.")
    report.append("Native usage total: "+json.dumps(results["native_usage_total"])+".")
    report.append(f"Elapsed seconds: {results['elapsed_seconds']:.2f}. Original E4 sources unchanged: {results['e4_sources_unchanged']}. E7 protocol unchanged: {results['protocol_unchanged']}.")
    report.extend("Limit: "+s for s in protocol["blind_spots"])
    (ROOT/"report.txt").write_text("\n".join(report)+"\n")

if __name__=="__main__": main()

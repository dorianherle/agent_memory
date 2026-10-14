"""Compare one global packet with query-conditioned evidence packets."""
from pathlib import Path
import sys, json, time, hashlib, re, math, collections
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"deps"))
sys.path.insert(0,str(ROOT.parent))
from common import complete, token_size, dump
START=time.monotonic()

def records():
    rows=[]
    histories={
      "Sparrow beam": [
        "Goal: make a low-power optical alignment beam module.",
        "Normal-mode power limit: 12 mW.",
        "The beam aperture is 42 um wide.",
        "Use the 1550 nm laser for alignment.",
        "The current package revision is R6.",
        "Use port P4 for the main output.",
        "Preserve all failed test results in the report.",
        "Correction: normal-mode power limit is now 17 mW. This replaces the previous 12 mW limit.",
        "Rare constraint: in ice mode, power must not exceed 3 mW.",
        "Rare constraint: do not use blue binder on a vacuum-exposed surface.",
        "The planned qualification temperature is 65 C.",
        "The reference photodiode serial number is PD-728.",
        "The test report owner is Mira Chen.",
        "The lens holder uses drawing LH-19.",
        "The sample tray has 24 slots.",
        "The nominal test current is 8 mA.",
        "The serial console rate is 115200 baud.",
        "The test coupon uses die C7.",
        "Store the package dry at 22 C.",
        "There are four remaining qualification tests.",
        "The device mount uses three M2 screws.",
        "The optical connector type is FC/APC.",
        "The stage travel is 14 mm.",
        "The final test repetition count is 37."
      ],
      "Quarry etch": [
        "Goal: qualify a lithium-niobate etch recipe.",
        "The chuck temperature setpoint is 120 C.",
        "The process pressure is 7 mTorr.",
        "The substrate material is sapphire.",
        "The mask material is chromium.",
        "The run uses chamber E2.",
        "The recipe label is Q-11.",
        "Correction: chuck temperature setpoint is now 135 C. This replaces the previous 120 C setpoint.",
        "Rare constraint: lateral etch rate is unknown. Never replace it with an assumed zero.",
        "Rare constraint: acetone cleaning is forbidden for this mask stack.",
        "The etch duration is 93 seconds.",
        "The argon flow is 21 sccm.",
        "The chlorine flow is 8 sccm.",
        "The RF bias power is 44 W.",
        "The ICP source power is 320 W.",
        "The wafer diameter is 100 mm.",
        "The mask thickness is 180 nm.",
        "The target vertical depth is 610 nm.",
        "The metrology owner is Tomas Reed.",
        "The sample carrier label is CR-82.",
        "The cooling hold is 11 minutes.",
        "The inspection microscope objective is 20x.",
        "The process file name is quarry_Q11.yaml.",
        "The backup gas line is G3."
      ],
      "Sparrow heater": ["Normal-mode power limit: 24 mW.","In ice mode, power limit: 11 mW.","Blue binder is permitted on its external support.","The beam aperture is 70 um wide.","The test repetition count is 9.","The release date is 2026-11-20."],
      "Quarry demo": ["Chuck temperature setpoint: 90 C.","The lateral etch rate is assumed zero for the illustration.","Acetone cleaning is allowed.","The etch duration is 30 seconds.","The mask thickness is 60 nm.","The release date is 2026-12-01."]
    }
    for project, facts in histories.items():
        prefix={"Sparrow beam":"S","Quarry etch":"Q","Sparrow heater":"H","Quarry demo":"D"}[project]
        for i,text in enumerate(facts,1): rows.append({"source":f"{prefix}{i:02}","project":project,"order":i,"text":text})
    return rows

TESTS=[
 {"id":"sparrow_common","query":"For Sparrow beam, what aperture width must I use?","answer":"42 um","source":"S03"},
 {"id":"sparrow_correction","query":"For Sparrow beam, give the latest normal-mode power limit.","answer":"17 mW","source":"S08"},
 {"id":"sparrow_rare_ice","query":"For Sparrow beam running in ice mode, what power ceiling applies?","answer":"3 mW","source":"S09"},
 {"id":"sparrow_rare_binder","query":"Can Sparrow beam use blue binder on a surface exposed to vacuum?","answer":"No","source":"S10"},
 {"id":"sparrow_tail","query":"For Sparrow beam, how many final test repetitions are required?","answer":"37","source":"S24"},
 {"id":"sparrow_absent","query":"What is the release date of Sparrow beam?","answer":"UNKNOWN","source":"NONE"},
 {"id":"quarry_common","query":"For Quarry etch, what is the process pressure?","answer":"7 mTorr","source":"Q03"},
 {"id":"quarry_correction","query":"For Quarry etch, what is the current chuck temperature setpoint?","answer":"135 C","source":"Q08"},
 {"id":"quarry_rare_lateral","query":"For Quarry etch, can I assume that the lateral etch rate is zero?","answer":"No; unknown","source":"Q09"},
 {"id":"quarry_rare_acetone","query":"For Quarry etch, is acetone cleaning permitted?","answer":"No","source":"Q10"},
 {"id":"quarry_tail","query":"For Quarry etch, what is the cooling hold duration?","answer":"11 minutes","source":"Q21"},
 {"id":"quarry_absent","query":"What is the release date of Quarry etch?","answer":"UNKNOWN","source":"NONE"}
]

STOP=set("a an the of for is are what which how can i must use give me to in on at it its with do does now latest current".split())
ALIASES={"ceiling":"limit","running":"mode","repetitions":"repetition","vacuum":"vacuum","exposed":"exposed","permitted":"allowed","duration":"duration"}
def words(s):
    return [ALIASES.get(w,w) for w in re.findall(r"[a-z0-9]+",s.lower()) if w not in STOP]

def line(row): return f"[{row['source']}] {row['project']}: {row['text']}"

def retrieve(query,archive,limit=180):
    # Only query and archive enter ranking. Labels and expected answers do not.
    q=collections.Counter(words(query))
    project_matches=[p for p in sorted(set(r["project"] for r in archive)) if p.lower() in query.lower()]
    docs=[set(words(line(row))) for row in archive]
    df=collections.Counter(w for doc in docs for w in doc)
    ranked=[]
    for row,doc in zip(archive,docs):
        score=sum((math.log((len(archive)+1)/(df[w]+1))+1)*min(c,1) for w,c in q.items() if w in doc)
        if project_matches and row["project"] not in project_matches: continue
        ranked.append({"row":row,"score":score})
    ranked.sort(key=lambda x:(-x["score"],-x["row"]["order"],x["row"]["source"]))
    selected=[]
    for item in ranked:
        candidate="\n".join(line(x["row"]) for x in selected+[item])
        if token_size(candidate)<=limit: selected.append(item)
    context="\n".join(line(x["row"]) for x in selected)
    return {"query":query,"query_terms":dict(q),"project_filter":project_matches,"ranked":[{"source":x["row"]["source"],"score":x["score"]} for x in ranked],"selected_sources":[x["row"]["source"] for x in selected],"context":context,"tokens":token_size(context),"record_count":len(selected)}

def decode(text):
    s=text.strip()
    if s.startswith("```"): s=re.sub(r"^```(?:json)?\s*|\s*```$","",s)
    return json.loads(s)

def norm(s): return re.sub(r"\s+"," ",str(s).strip().lower().replace("μ","u").replace("µ","u"))

def run():
    archive=records()
    protocol={"experiment":"E4 query-conditioned memory", "frozen_before_calls":True,"model":"gpt-5.6-sol","reasoning":"low","seed":None,"estimated_tokenizer":"o200k_base","packet_target_tokens":180,"maximum_model_calls":8,"maximum_seconds":300,"archive":archive,"hidden_tests":TESTS,"retriever":{"algorithm":"IDF-weighted query-term overlap; exact project-name filter; newest record wins score ties; greedily add complete ranked records within token limit","stop_words":sorted(STOP),"aliases":ALIASES,"uses_expected_answers":False},"calls":["global summary","global packet reader","query-conditioned reader","full-history reader"],"receiver":{"tools":False,"batch":True,"context_per_question":True},"grading":"Exact normalized answer and exact source ID. Equivalent wording can fail. All outputs remain for review.","blind_spots":["Two constructed histories","One writer and one reader model","One run per arm","Questions are batched and visible together","The reader can read contexts assigned to other questions","Global packet is repeated per question and total token count is reported","Lexical matching uses exact project names","No large-agent continuation","No fundamental-rule discovery","Estimated token counts differ from native usage"]}
    dump(ROOT/"protocol.json",protocol)
    protocol_hash=hashlib.sha256((ROOT/"protocol.json").read_bytes()).hexdigest()
    raw="\n".join(line(row) for row in archive)
    dump(ROOT/"archive.json",archive)
    result={"experiment":protocol["experiment"],"protocol_sha256":protocol_hash,"status":"running","arms":{},"calls":[],"archive_tokens":token_size(raw),"archive_records":len(archive),"blind_spots":protocol["blind_spots"]}
    dump(ROOT/"results.json",result)
    def call(prompt,name):
        remaining=300-(time.monotonic()-START)
        if remaining<15: raise RuntimeError("Five-minute experiment cap reached.")
        value=complete(prompt,ROOT/name,timeout=min(55,max(15,int(remaining))),model=protocol["model"])
        result["calls"].append({"name":name,"usage":value["usage"],"elapsed_seconds":value["elapsed_seconds"],"native_tools":value["native_tools"],"ok":value["ok"]})
        dump(ROOT/"results.json",result)
        return value
    try:
        summary=call("Write one global handoff packet for both project histories below. Use at most 180 tokens. Keep current facts, corrections, rare constraints, and source IDs as useful. You do not know the later questions. Return only the packet.\n\n"+raw,"writer")
        packet=summary["text"]; (ROOT/"global_packet.txt").write_text(packet+"\n")
        global_size=token_size(packet)
        logs=[retrieve(test["query"],archive) for test in TESTS]
        dump(ROOT/"retrieval.json",logs)
        result["global_packet"]={"text":packet,"tokens":global_size,"target":180,"target_breach":global_size>180}
        for arm in ["global","retrieval","full_history"]:
            contexts=[packet if arm=="global" else logs[i]["context"] if arm=="retrieval" else raw for i in range(len(TESTS))]
            items=[{"id":test["id"],"query":test["query"],"evidence":contexts[i]} for i,test in enumerate(TESTS)]
            prompt="Answer each question using its supplied evidence. Treat the project names as different projects. Use the latest correction. If its answer is absent, return UNKNOWN and source NONE. Return JSON as {\"answers\":[{\"id\":\"...\",\"answer\":\"...\",\"source\":\"...\"}]}. Give an exact short value, unit, or Yes/No answer. For an unknown lateral rate, answer exactly No; unknown. Do not explain.\n\n"+json.dumps(items,ensure_ascii=False)
            receiver=call(prompt,arm+"_reader")
            parsed=decode(receiver["text"])
            by_id={row["id"]:row for row in parsed["answers"]}
            grades=[]
            for i,test in enumerate(TESTS):
                answer=by_id.get(test["id"],{})
                answer_pass=norm(answer.get("answer"))==norm(test["answer"])
                source_pass=answer.get("source")==test["source"]
                evidence_has_source=test["source"] in contexts[i] if test["source"]!="NONE" else None
                grades.append({"id":test["id"],"query":test["query"],"expected_answer":test["answer"],"expected_source":test["source"],"response":answer,"answer_pass":answer_pass,"source_pass":source_pass,"joint_pass":answer_pass and source_pass,"assigned_context_has_expected_source":evidence_has_source,"assigned_context_tokens":token_size(contexts[i]),"retrieved_records":logs[i]["record_count"] if arm=="retrieval" else None})
            result["arms"][arm]={"grades":grades,"answer_passes":sum(x["answer_pass"] for x in grades),"source_passes":sum(x["source_pass"] for x in grades),"joint_passes":sum(x["joint_pass"] for x in grades),"total":len(grades),"total_memory_tokens":sum(token_size(c) for c in contexts),"largest_question_context_tokens":max(token_size(c) for c in contexts),"question_context_target_breaches":sum(token_size(c)>180 for c in contexts),"reader_native_usage":receiver["usage"],"raw_reader_text":receiver["text"]}
            dump(ROOT/"results.json",result)
            print(arm,result["arms"][arm]["joint_passes"],"/",len(TESTS),flush=True)
        result["status"]="completed"
    except Exception as exc:
        import traceback
        result["status"]="inconclusive"; result["error"]=traceback.format_exc()
        print(result["error"],flush=True)
    result["elapsed_seconds"]=time.monotonic()-START
    result["native_usage_total"]={k:sum(c["usage"].get(k,0) for c in result["calls"]) for k in ["input_tokens","output_tokens"]}
    result["native_usage_complete"]=all(c["ok"] for c in result["calls"]) and len(result["calls"])==4
    result["protocol_unchanged"]=hashlib.sha256((ROOT/"protocol.json").read_bytes()).hexdigest()==protocol_hash
    dump(ROOT/"results.json",result)
    report=["E4 query-conditioned memory", "Status: "+result["status"],f"Archive: {len(archive)} records; {token_size(raw)} estimated tokens."]
    if "global_packet" in result: report.append(f"Global packet: {result['global_packet']['tokens']} estimated tokens; 180-token target breach: {result['global_packet']['target_breach']}.")
    for name,value in result["arms"].items(): report.append(f"{name}: exact answers {value['answer_passes']}/12; exact sources {value['source_passes']}/12; joint {value['joint_passes']}/12; total supplied memory {value['total_memory_tokens']} estimated tokens; largest question packet {value['largest_question_context_tokens']} tokens; target breaches {value['question_context_target_breaches']}.")
    report.append("Native usage: "+json.dumps(result["native_usage_total"])+". All call usage is measured by the native helper.")
    report.append("This tests a query-conditioned memory interface. It does not test fundamental-rule discovery or native-agent continuation. The receiver batch can read the union of all supplied question contexts.")
    report.extend("Limit: "+s for s in protocol["blind_spots"])
    if result.get("error"): report.append(result["error"])
    (ROOT/"report.txt").write_text("\n".join(report)+"\n")

def repair():
    # Preserve the original protocol and over-budget outcome. Freeze this amendment before calls.
    import tiktoken
    result=json.loads((ROOT/"results.json").read_text())
    original_calls=len(result["calls"])
    amendment={"reason":"The first writer packet exceeded the declared 180-token target.","frozen_before_repair_calls":True,"original_protocol_sha256":result["protocol_sha256"],"action":"Ask a fresh writer for at most 85 words and 180 tokens. If it still exceeds 180 tokens, apply a deterministic token-prefix cap. Re-run only the global reader. Preserve all first-pass outcomes.","writer_sees_queries":False,"writer_sees_expected_answers":False,"new_model_calls":2,"maximum_total_model_calls":8,"packet_target_tokens":180,"question_and_grade_changes":False}
    dump(ROOT/"protocol_repair.json",amendment)
    amendment_hash=hashlib.sha256((ROOT/"protocol_repair.json").read_bytes()).hexdigest()
    archive=json.loads((ROOT/"archive.json").read_text()); raw="\n".join(line(row) for row in archive)
    writer=complete("Write one global handoff packet for these two project histories. Use at most 85 words and at most 180 tokens. Keep current facts, corrections, rare constraints, and source IDs as useful. You do not know the later questions. Return only the packet.\n\n"+raw,ROOT/"writer_capped",timeout=45,model="gpt-5.6-sol")
    encoder=tiktoken.get_encoding("o200k_base")
    original=writer["text"]; ids=encoder.encode(original); packet=encoder.decode(ids[:180])
    (ROOT/"global_packet_capped.txt").write_text(packet+"\n")
    items=[{"id":test["id"],"query":test["query"],"evidence":packet} for test in TESTS]
    prompt="Answer each question using its supplied evidence. Treat the project names as different projects. Use the latest correction. If its answer is absent, return UNKNOWN and source NONE. Return JSON as {\"answers\":[{\"id\":\"...\",\"answer\":\"...\",\"source\":\"...\"}]}. Give an exact short value, unit, or Yes/No answer. For an unknown lateral rate, answer exactly No; unknown. Do not explain.\n\n"+json.dumps(items,ensure_ascii=False)
    receiver=complete(prompt,ROOT/"global_capped_reader",timeout=45,model="gpt-5.6-sol")
    parsed=decode(receiver["text"]); by_id={row["id"]:row for row in parsed["answers"]}; grades=[]
    for test in TESTS:
        answer=by_id.get(test["id"],{}); ap=norm(answer.get("answer"))==norm(test["answer"]); sp=answer.get("source")==test["source"]
        grades.append({"id":test["id"],"query":test["query"],"expected_answer":test["answer"],"expected_source":test["source"],"response":answer,"answer_pass":ap,"source_pass":sp,"joint_pass":ap and sp,"assigned_context_has_expected_source":test["source"] in packet if test["source"]!="NONE" else None,"assigned_context_tokens":token_size(packet),"retrieved_records":None})
    result["arms"]["global_capped"]={"grades":grades,"answer_passes":sum(x["answer_pass"] for x in grades),"source_passes":sum(x["source_pass"] for x in grades),"joint_passes":sum(x["joint_pass"] for x in grades),"total":len(grades),"total_memory_tokens":len(grades)*token_size(packet),"largest_question_context_tokens":token_size(packet),"question_context_target_breaches":int(token_size(packet)>180)*len(grades),"reader_native_usage":receiver["usage"],"raw_reader_text":receiver["text"]}
    result["global_packet_capped"]={"text":packet,"tokens":token_size(packet),"writer_raw_tokens":len(ids),"deterministic_prefix_cap_used":len(ids)>180,"writer_raw_text":original,"target":180,"target_breach":token_size(packet)>180}
    result["repair_protocol_sha256"]=amendment_hash
    result["repair_protocol_unchanged"]=hashlib.sha256((ROOT/"protocol_repair.json").read_bytes()).hexdigest()==amendment_hash
    for name,value in [("writer_capped",writer),("global_capped_reader",receiver)]: result["calls"].append({"name":name,"usage":value["usage"],"elapsed_seconds":value["elapsed_seconds"],"native_tools":value["native_tools"],"ok":value["ok"]})
    result["native_usage_total"]={k:sum(c["usage"].get(k,0) for c in result["calls"]) for k in ["input_tokens","output_tokens"]}
    result["native_usage_complete"]=all(c["ok"] for c in result["calls"]) and len(result["calls"])==original_calls+2
    result["repair_elapsed_seconds"]=time.monotonic()-START
    dump(ROOT/"results.json",result)
    arm=result["arms"]["global_capped"]
    with (ROOT/"report.txt").open("a") as out:
        out.write(f"\nBudget repair: a separate protocol was frozen before two additional calls. The original packet and outcomes remain above.\nCapped global packet: {token_size(packet)} estimated tokens; writer raw {len(ids)} tokens; prefix cap used: {len(ids)>180}.\nCapped global: exact answers {arm['answer_passes']}/12; exact sources {arm['source_passes']}/12; joint {arm['joint_passes']}/12; total supplied memory {arm['total_memory_tokens']} estimated tokens.\nAll six calls native usage: {json.dumps(result['native_usage_total'])}.\n")
    print("global_capped",arm["joint_passes"],"/ 12; packet",token_size(packet),flush=True)

if __name__=="__main__":
    if "--repair" in sys.argv: repair()
    else: run()

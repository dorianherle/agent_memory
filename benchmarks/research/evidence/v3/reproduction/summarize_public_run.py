"""Summarize the final eligible retrieval set without changing raw results."""
import hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent; RUN=R/'longmemeval-final'
raw=json.loads((RUN/'results.json').read_text()); check=json.loads((RUN/'verification.json').read_text())
assert check['passed']; rows=raw['rows']; assert len(rows)==470
by_type={}
for kind in sorted({x['question_type'] for x in rows}):
 selected=[x for x in rows if x['question_type']==kind]
 by_type[kind]={'questions':len(selected), **{c+'_all_evidence_at_10':int(sum(x['results'][c]['metrics']['recall_all@10'] for x in selected)) for c in ('memory','notebook')}}
result={'assessment':'All eligible LongMemEval-S cleaned session retrieval questions','raw_result_sha256':hashlib.sha256((RUN/'results.json').read_bytes()).hexdigest(),'questions':len(rows),'excluded_abstentions':30,'all_evidence_at_5':{c:int(sum(x['results'][c]['metrics']['recall_all@5'] for x in rows)) for c in ('memory','notebook')},'all_evidence_at_10':{c:int(sum(x['results'][c]['metrics']['recall_all@10'] for x in rows)) for c in ('memory','notebook')},'exact_rank_disagreements':sum(x['results']['memory']['retrieved_session_ids']!=x['results']['notebook']['retrieved_session_ids'] for x in rows),'by_question_type':by_type,'source_version':'Final v6; frozen before replay','input_integrity_passed_before_all_queries':all(x['input_integrity_passed_before_retrieval'] for x in rows),'histories_restored_from_input_in_final_replay':sum(x['reingested_from_authoritative_input'] for x in rows),'no_model_calls':True,'answer_quality_tested':False,'native_tokens_or_cost_tested':False,'decoded_note_text_integrity_verified':check['decoded_memory_text_and_notebook_bytes_equal'],'sessions_verified':check['sessions_audited'],'ingestion_time_measured':False,'timing_limit':raw['latency_limit']}
(RUN/'run-summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

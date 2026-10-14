"""Replay all queries from preserved Git and notebook stores with the fixed reader.

Copy the stores before rebuilding the derived cache. Preserve the original run.
This measures query results only; it does not measure ingestion performance.
"""
import argparse,collections,hashlib,json,os,shutil,sqlite3,statistics,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent_memory import Memory
from scripts.benchmark_public_retrieval import metrics
p=argparse.ArgumentParser(description=__doc__);p.add_argument('dataset',type=Path);p.add_argument('source_run',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
for key in ('GEMINI_API_KEY','AGENT_MEMORY_PROVIDER'):os.environ.pop(key,None)
rows={r['question_id']:r for r in json.loads(a.dataset.read_text())};prior=json.loads((a.source_run/'results.json').read_text());ids=[r['question_id'] for r in prior['rows']];assert len(ids)==470
assert set(ids)=={r['question_id'] for r in rows.values() if not str(r['question_id']).endswith('_abs') and r['answer_session_ids']}
a.output.mkdir(parents=True,exist_ok=False)
freeze={'selected_question_ids':ids,'dataset_sha256':hashlib.sha256(a.dataset.read_bytes()).hexdigest(),'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('scripts/replay_public_retrieval.py','scripts/benchmark_public_retrieval.py','agent_memory/core.py','agent_memory/catalog.py')},'reused_git_run':str(a.source_run.resolve()),'reused_raw_result_sha256':hashlib.sha256((a.source_run/'results.json').read_bytes()).hexdigest(),'method':'Copy preserved stores and compare every decoded note with the authoritative input multiset. Reingest any mismatched history from role/content/date/session ID only before retrieval. Verify both corpora, then replay every query. No ingestion latency claim.'}
(a.output/'protocol-freeze.json').write_text(json.dumps(freeze,indent=2)+'\n')
start=time.perf_counter()
def assess(qid):
 row=rows[qid];name=hashlib.sha256(str(qid).encode()).hexdigest()[:16];source=a.source_run/'workspaces'/name;case=a.output/'workspaces'/name;case.mkdir(parents=True)
 for kind in ('memory','notebook'):shutil.copytree(source/kind,case/kind)
 inputs=[]
 for sid,date,turns in zip(row['haystack_session_ids'],row['haystack_dates'],row['haystack_sessions']):
  text=f'Date: {date}\n'+'\n\n'.join(f"{turn['role']}: {turn['content']}" for turn in turns)
  inputs.append((text,{'session_id':sid,'date':date,'agent':'public-corpus'}))
 expected=collections.Counter((text,json.dumps(payload,sort_keys=True)) for text,payload in inputs)
 m=Memory(case/'memory');notes=m.state()
 actual=collections.Counter((x.message,json.dumps(x.payload,sort_keys=True)) for x in notes)
 affected=actual!=expected
 mismatched_notes=sum((expected-actual).values())
 if affected:
  m._catalog.close();shutil.rmtree(case/'memory');(case/'memory').mkdir();m=Memory(case/'memory')
  for text,payload in inputs:m.save(text,payload,agent='public-corpus')
  notes=m.state()
 assert collections.Counter((x.message,json.dumps(x.payload,sort_keys=True)) for x in notes)==expected,qid
 for i,(text,_payload) in enumerate(inputs):assert (case/'notebook'/f'{i:04}.md').read_bytes().decode('utf-8')==text
 mapping={x.id:x.payload['session_id'] for x in notes}
 conn=sqlite3.connect(case/'notebook/index.sqlite3');query=row['question'];gold=set(row['answer_session_ids']);times={};found={}
 for kind in (('memory','notebook') if ids.index(qid)%2==0 else ('notebook','memory')):
  tick=time.perf_counter()
  if kind=='memory':found[kind]=[mapping[x.id] for x in m.find(query,k=10,dedupe=False)]
  else:
   expr=' OR '.join(f'"{word}"' for word in query.replace('"',' ').split() if word)
   found[kind]=[x[0] for x in conn.execute('SELECT session_id FROM notes WHERE notes MATCH ? ORDER BY bm25(notes),CAST(ordinal AS INTEGER) DESC LIMIT 10',(expr,)).fetchall()]
  times[kind]=(time.perf_counter()-tick)*1000
 m._catalog.close();conn.close()
 result={'question_id':qid,'question_type':row['question_type'],'sessions':len(notes),'reingested_from_authoritative_input':affected,'previous_mismatched_notes':mismatched_notes,'input_integrity_passed_before_retrieval':True,'content_sha256':[hashlib.sha256((case/'notebook'/f'{i:04}.md').read_bytes()).hexdigest() for i in range(len(row['haystack_session_ids']))],'gold_session_ids':sorted(gold),'results':{kind:{'retrieved_session_ids':found[kind],'metrics':{**metrics(found[kind],gold,5,row['haystack_session_ids']),**metrics(found[kind],gold,10,row['haystack_session_ids'])},'query_ms':times[kind]} for kind in ('memory','notebook')}}
 (case/'assessment.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'question_id':qid,'sessions':len(notes)}),flush=True);return result
with ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(assess,ids))
summary={kind:{name:statistics.mean(x['results'][kind]['metrics'][name] for x in results) for name in results[0]['results'][kind]['metrics']} for kind in ('memory','notebook')}
for kind in summary:summary[kind]['median_query_ms']=statistics.median(x['results'][kind]['query_ms'] for x in results)
report={'benchmark':'LongMemEval-S cleaned','assessment':'All 470 eligible session-retrieval questions, corrected-reader replay','questions':470,'elapsed_seconds':time.perf_counter()-start,'no_model_calls':True,'not_end_to_end_qa':True,'ingestion_time_measured':False,'comparison':'Same byte-exact input sessions and SQLite BM25; query order counterbalanced by question.','latency_limit':'One query per case on one macOS host with concurrent copy and catalog rebuild. Not a production latency distribution.','summary':summary,'rows':results}
(a.output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'questions':470,'summary':summary},indent=2))

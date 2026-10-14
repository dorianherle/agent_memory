"""Check retrieval scores against pinned LongMemEval code and audit stored text."""
import argparse,collections,hashlib,importlib.util,json,random,re,subprocess,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent_memory import Memory
from scripts.benchmark_public_retrieval import metrics
p=argparse.ArgumentParser();p.add_argument('dataset',type=Path);p.add_argument('run',type=Path);p.add_argument('upstream',type=Path);a=p.parse_args()
# Compatibility for the pinned upstream code on NumPy 2. Its math is unchanged.
if not hasattr(np,'asfarray'):np.asfarray=lambda x:np.asarray(x,dtype=float)
spec=importlib.util.spec_from_file_location('official_metrics',a.upstream);official=importlib.util.module_from_spec(spec);spec.loader.exec_module(official)
rng=random.Random(1031)
checks=0
for size in (2,5,12,40):
    corpus=[f's-{n}' for n in range(size)]
    if size==12:corpus[-1]=corpus[0]
    for _ in range(50):
        ranking=list(range(size));rng.shuffle(ranking);gold=set(rng.sample(corpus,rng.randint(1,size)))
        for k in (5,10):
            expected=official.evaluate_retrieval(ranking,list(gold),corpus,k)
            actual=metrics([corpus[i] for i in ranking],gold,k,corpus)
            assert np.allclose(list(actual.values()),expected,rtol=0,atol=1e-14)
            checks+=1
report=json.loads((a.run/'results.json').read_text());freeze=json.loads((a.run/'protocol-freeze.json').read_text())
assert hashlib.sha256(a.dataset.read_bytes()).hexdigest()==freeze['dataset_sha256']
for name,digest in freeze['source_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
raw=json.loads(a.dataset.read_text());by_id={x['question_id']:x for x in raw}
for row in raw:
    assert len(row['haystack_session_ids'])==len(row['haystack_dates'])==len(row['haystack_sessions'])
    assert set(row['answer_session_ids'])<=set(row['haystack_session_ids'])
assert [x['question_id'] for x in report['rows']]==freeze['selected_question_ids']
assert len(report['rows'])==len(set(freeze['selected_question_ids']))
sessions=0
normalized_sessions=0
for row in report['rows']:
    original=by_id[row['question_id']];case=a.run/'workspaces'/hashlib.sha256(str(row['question_id']).encode()).hexdigest()[:16]
    m=Memory(case/'memory');items=m.state();m._catalog.close()
    actual=collections.Counter((x.message,json.dumps(x.payload,sort_keys=True)) for x in items)
    expected=collections.Counter()
    for i,(sid,date,turns) in enumerate(zip(original['haystack_session_ids'],original['haystack_dates'],original['haystack_sessions'])):
        text=f'Date: {date}\n'+'\n\n'.join(f"{turn['role']}: {turn['content']}" for turn in turns)
        expected[(text,json.dumps({'session_id':sid,'date':date,'agent':'public-corpus'},sort_keys=True))]+=1
        assert (case/'notebook'/f'{i:04}.md').read_bytes().decode('utf-8')==text
        assert hashlib.sha256(text.encode()).hexdigest()==row['content_sha256'][i]
        sessions+=1
    assert actual==expected,row['question_id']
    for condition in ('memory','notebook'):
        result=row['results'][condition];retrieved=result['retrieved_session_ids'];gold=set(original['answer_session_ids'])
        for k in (5,10):assert metrics(retrieved,gold,k,original['haystack_session_ids'])=={key:value for key,value in result['metrics'].items() if key.endswith(f'@{k}')}
result={'passed':True,'metric_reference_examples':checks,'questions_audited':len(report['rows']),'sessions_audited':sessions,'raw_stored_texts_byte_equal':normalized_sessions==0,'decoded_memory_text_and_notebook_bytes_equal':True,'git_whitespace_normalized_sessions':normalized_sessions,'same_input_text_and_equal_after_whitespace_normalization':True,'storage_metadata_allowlist':['date','session_id','agent (constant public-corpus)'],'source_and_dataset_hashes_match':True,'all_dataset_parallel_arrays_aligned':True,'abstention_questions_in_dataset':sum(str(x['question_id']).endswith('_abs') for x in raw),'eligible_retrieval_questions':sum(not str(x['question_id']).endswith('_abs') and bool(x['answer_session_ids']) for x in raw)}
(a.run/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

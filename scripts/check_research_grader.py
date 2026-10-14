"""Validate the frozen grader with correct controls and unfinished stubs."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile

from grade_research_handoff import grade_case
from research_handoff import CASES, data_files

SOURCES = {
    'convergence': '''import math,json,subprocess,sys
from pathlib import Path
def assess(records,config):
    if len(records)<3:raise ValueError('insufficient records')
    records=sorted(records,key=lambda r:r['intervals'])[-3:]
    length=config['length']
    if not math.isfinite(length) or length<=0:raise ValueError('length')
    for r in records:
        if any(not math.isfinite(r[k]) or r[k]<=0 for k in ('intervals','length','eigenvalue')):raise ValueError('invalid sample')
        if r['length']!=length:raise ValueError('stale geometry')
    a,b,c=records
    if b['intervals']!=2*a['intervals'] or c['intervals']!=2*b['intervals']:raise ValueError('grid ratios')
    d1=abs(a['eigenvalue']-b['eigenvalue']);d2=abs(b['eigenvalue']-c['eigenvalue'])
    if not d1 or not d2:raise ValueError('degenerate')
    p=math.log2(d1/d2)
    if not math.isfinite(p) or p<=0:raise ValueError('observed order')
    value=c['eigenvalue']+(c['eigenvalue']-b['eigenvalue'])/(2**p-1)
    error=abs(value-(math.pi/length)**2)/(math.pi/length)**2
    return dict(observed_order=p,extrapolated=value,relative_error=error,qualified=bool(config['order_min']<=p<=config['order_max'] and error<=config['tolerance']))
def main():
    cfg=json.loads(Path('config.json').read_text());records=json.loads(Path('samples.json').read_text())
    if any(r['length']!=cfg['length'] for r in records):
        subprocess.run([sys.executable,'measure.py'],check=True)
        records=json.loads(Path('samples.json').read_text())
    Path('report.json').write_text(json.dumps(assess(records,cfg),indent=2)+'\\n')
if __name__=='__main__':main()
''',
    'vector_error': '''import json,numpy as np
from pathlib import Path
def field_error(predicted,reference):
    p=np.asarray(predicted,dtype=complex);r=np.asarray(reference,dtype=complex)
    if p.ndim!=1 or r.ndim!=1 or not len(p) or p.shape!=r.shape or not np.isfinite(p).all() or not np.isfinite(r).all():raise ValueError('invalid vectors')
    pn=np.linalg.norm(p);rn=np.linalg.norm(r)
    if pn==0 or rn==0:raise ValueError('zero vector')
    p=p/pn;r=r/rn;phase=np.angle(np.vdot(r,p))
    return float(np.linalg.norm(p*np.exp(-1j*phase)-r))
def main():
    d=json.loads(Path('fields.json').read_text());policy=json.loads(Path('policy.json').read_text())
    convert=lambda rows:[complex(*pair) for pair in rows]
    error=field_error(convert(d['predicted']),convert(d['reference']))
    Path('report.json').write_text(json.dumps(dict(field_error=error,qualified=error<=policy['field_error_limit']),indent=2)+'\\n')
if __name__=='__main__':main()
''',
    'runtime_audit': '''import json,math,statistics
from pathlib import Path
def summarize(rows,policy):
    if not rows:raise ValueError('no evidence')
    limit=policy['residual_limit']
    if not math.isfinite(limit) or limit<=0:raise ValueError('invalid limit')
    phases=('load_ms','predict_ms','correct_ms','verify_ms','output_ms')
    costs=[];speed=[];inference=[]
    for row in rows:
        try:vals=[row[k] for k in (*phases,'baseline_ms','residual')]
        except KeyError as e:raise ValueError('missing field') from e
        if any(not math.isfinite(x) or x<0 for x in vals):raise ValueError('invalid measurement')
        total=sum(row[k] for k in phases)
        if total<=0 or row['predict_ms']<=0 or row['baseline_ms']<=0:raise ValueError('invalid denominator')
        costs.append(total);inference.append(row['baseline_ms']/row['predict_ms'])
        if row['residual']<=limit:speed.append(row['baseline_ms']/total)
    median=statistics.median(speed) if speed else None
    claim='no_qualified_speedup' if not speed else 'accuracy_limited' if len(speed)<len(rows) else 'faster' if median>1 else 'slower_or_equal'
    return dict(attempted=len(rows),qualified=len(speed),median_attempt_ms=statistics.median(costs),median_qualified_speedup=median,median_inference_speedup=statistics.median(inference),baseline_contract='construction_only',claim=claim)
def main():
    rows=json.loads(Path('timings.json').read_text());policy=json.loads(Path('policy.json').read_text())
    Path('report.json').write_text(json.dumps(summarize(rows,policy),indent=2)+'\\n')
if __name__=='__main__':main()
''',
}


def check(output):
    rows=[]
    with tempfile.TemporaryDirectory(prefix='research-grader-control-') as folder:
        for case in CASES:
            path=Path(folder)/case;path.mkdir();data_files(path,case,1)
            (path/'study.py').write_text(SOURCES[case])
            positive=grade_case(path,case)
            assert positive['full_task_pass'],(case,positive)
            (path/'study.py').write_text(CASES[case]['source'])
            negative=grade_case(path,case)
            assert negative['passed']==0 and not negative['full_task_pass'],(case,negative)
            rows.append(dict(case=case,positive_passed=positive['passed'],total=positive['total'],negative_passed=negative['passed']))
    result=dict(controls=rows,passed=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    check(p.parse_args().output)

"""Independent executable grader. Run after each fresh agent has finished.

The development suite checks numerical behavior on inputs absent from agent
fixtures. Freeze this file before model trials. This is not a held-out domain.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import numpy as np


def close(actual, expected, tol=1e-8):
    assert math.isfinite(float(actual)) and math.isclose(float(actual), float(expected), rel_tol=tol, abs_tol=tol), (actual, expected)


def reject(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('Invalid evidence must raise ValueError.')


def convergence_checks(module):
    cfg=dict(length=1.3,tolerance=1e-6,order_min=1.8,order_max=2.2)
    exact=(math.pi/cfg['length'])**2
    def records(order=2, sign=1):
        return [dict(intervals=n,length=cfg['length'],eigenvalue=exact+sign*0.7/n**order) for n in (8,16,32,64)]
    def second_order():
        a=module.assess(records(),cfg)
        close(a['observed_order'],2,1e-7);close(a['extrapolated'],exact)
        close(a['relative_error'],0);assert a['qualified'] is True or a['qualified']==True
    def reversed_error_sign():
        a=module.assess(records(sign=-1),cfg)
        close(a['observed_order'],2,1e-7);close(a['extrapolated'],exact)
    def first_order_rejected():
        a=module.assess(records(order=1),cfg)
        close(a['observed_order'],1);assert not a['qualified']
    def extrapolation_not_reference_substitution():
        r=records()
        for row in r: row['eigenvalue']+=0.04
        a=module.assess(r,cfg)
        close(a['extrapolated'],exact+0.04);assert not a['qualified']
    def order_independent_of_input_sort():
        a=module.assess(list(reversed(records())),cfg);close(a['extrapolated'],exact)
    def stale_geometry_rejected():
        r=records();r[-1]['length']=2.1;reject(lambda:module.assess(r,cfg))
    def nondoubling_grid_rejected():
        r=records();r[-1]['intervals']=60;reject(lambda:module.assess(r,cfg))
    def insufficient_data_rejected(): reject(lambda:module.assess(records()[:2],cfg))
    def degenerate_data_rejected():
        r=records()
        for row in r:row['eigenvalue']=exact
        reject(lambda:module.assess(r,cfg))
    def nonfinite_data_rejected():
        r=records();r[-1]['eigenvalue']=float('nan');reject(lambda:module.assess(r,cfg))
    return [second_order,reversed_error_sign,first_order_rejected,extrapolation_not_reference_substitution,
        order_independent_of_input_sort,stale_geometry_rejected,nondoubling_grid_rejected,
        insufficient_data_rejected,degenerate_data_rejected,nonfinite_data_rejected]


def vector_checks(module):
    rng=np.random.default_rng(74019)
    r=rng.normal(size=11)+1j*rng.normal(size=11)
    def identical():close(module.field_error(r,r),0)
    def phase_invariance():close(module.field_error(r*np.exp(1j*1.37),r),0)
    def scale_invariance():close(module.field_error(-9.4j*r,0.021*r),0)
    def relative_phase_detected():close(module.field_error([1,1],[1,-1]),math.sqrt(2))
    def missing_component_detected():close(module.field_error([1,0],[1,1]),math.sqrt(2-math.sqrt(2)))
    def arbitrary_vector_matches_independent_formula():
        p=rng.normal(size=11)+1j*rng.normal(size=11)
        expected=math.sqrt(max(0,2-2*abs(np.vdot(r/np.linalg.norm(r),p/np.linalg.norm(p)))))
        close(module.field_error(p,r),expected)
    def zero_norm_rejected():reject(lambda:module.field_error([0,0],[1,0]))
    def unequal_lengths_rejected():reject(lambda:module.field_error([1,0],[1]))
    def multidimensional_rejected():reject(lambda:module.field_error([[1,2]],[[1,2]]))
    def nonfinite_rejected():reject(lambda:module.field_error([1,float('nan')],[1,2]))
    return [identical,phase_invariance,scale_invariance,relative_phase_detected,
        missing_component_detected,arbitrary_vector_matches_independent_formula,zero_norm_rejected,
        unequal_lengths_rejected,multidimensional_rejected,nonfinite_rejected]


def runtime_checks(module):
    policy={'residual_limit':1e-6}
    base=[dict(load_ms=2,predict_ms=1,correct_ms=8,verify_ms=4,output_ms=3,
               training_ms=200000,baseline_ms=9,residual=1e-8),
          dict(load_ms=4,predict_ms=2,correct_ms=10,verify_ms=5,output_ms=4,
               training_ms=100000,baseline_ms=20,residual=1e-8),
          dict(load_ms=3,predict_ms=3,correct_ms=2,verify_ms=1,output_ms=1,
               training_ms=100000,baseline_ms=100,residual=1e-3)]
    def full_pipeline_cost():
        a=module.summarize(base,policy);close(a['median_attempt_ms'],18)
    def accuracy_coverage():
        a=module.summarize(base,policy);assert a['attempted']==3 and a['qualified']==2
    def failed_cases_not_speed_wins():
        a=module.summarize(base,policy);close(a['median_qualified_speedup'],(9/18+20/25)/2)
    def accuracy_limited_claim():assert module.summarize(base,policy)['claim']=='accuracy_limited'
    def pure_inference_separate():close(module.summarize(base,policy)['median_inference_speedup'],10)
    def offline_training_not_charged():
        a=module.summarize(base,policy);other=copy.deepcopy(base)
        for row in other:row['training_ms']*=100
        close(a['median_attempt_ms'],module.summarize(other,policy)['median_attempt_ms'])
    def baseline_contract_explicit():assert module.summarize(base,policy)['baseline_contract']=='construction_only'
    def no_accepted_result():
        rows=copy.deepcopy(base)
        for row in rows:row['residual']=0.01
        a=module.summarize(rows,policy);assert a['median_qualified_speedup'] is None and a['claim']=='no_qualified_speedup'
    def full_coverage_slow_and_fast():
        rows=copy.deepcopy(base[:2]);assert module.summarize(rows,policy)['claim']=='slower_or_equal'
        for row in rows:row['baseline_ms']=100
        assert module.summarize(rows,policy)['claim']=='faster'
    def invalid_evidence_rejected():
        reject(lambda:module.summarize([],policy))
        rows=copy.deepcopy(base);rows[0]['verify_ms']=-1
        reject(lambda:module.summarize(rows,policy))
        rows=copy.deepcopy(base);rows[0]['predict_ms']=0
        reject(lambda:module.summarize(rows,policy))
        rows=copy.deepcopy(base);rows[0]['residual']=float('inf')
        reject(lambda:module.summarize(rows,policy))
    return [full_pipeline_cost,accuracy_coverage,failed_cases_not_speed_wins,accuracy_limited_claim,
        pure_inference_separate,offline_training_not_charged,baseline_contract_explicit,
        no_accepted_result,full_coverage_slow_and_fast,invalid_evidence_rejected]


def grade_case(path, case):
    checks={'convergence':convergence_checks,'vector_error':vector_checks,'runtime_audit':runtime_checks}
    results=[]
    try:
        spec=importlib.util.spec_from_file_location('candidate_'+hashlib.sha256(str(path).encode()).hexdigest()[:10],path/'study.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        tests=checks[case](m)
        for test in tests:
            try:test();results.append({'check':test.__name__,'passed':True})
            except Exception as error:results.append({'check':test.__name__,'passed':False,'error':f'{type(error).__name__}: {error}'})
    except Exception as error:
        results=[{'check':'module_import','passed':False,'error':f'{type(error).__name__}: {error}'}]
    main_result=subprocess.run([sys.executable,'study.py'],cwd=path,text=True,capture_output=True,timeout=30)
    main_ok=False;main_error=None
    try:
        assert main_result.returncode==0, main_result.stderr[-2000:]
        report=json.loads((path/'report.json').read_text())
        if case=='convergence':
            cfg=json.loads((path/'config.json').read_text());records=json.loads((path/'samples.json').read_text())
            assert all(math.isclose(r['length'],cfg['length']) for r in records)
            assert report['qualified'];close(report['extrapolated'],(math.pi/cfg['length'])**2,1e-6)
        elif case=='vector_error':
            assert not report['qualified'];assert report['field_error']>0.7
        else:
            assert report['attempted']==4 and report['qualified']==3 and report['claim']=='accuracy_limited'
            close(report['median_attempt_ms'],21)
        main_ok=True
    except Exception as error:main_error=f'{type(error).__name__}: {error}'
    results.append({'check':'current_data_report','passed':main_ok,'error':main_error})
    return dict(passed=sum(x['passed'] for x in results),total=11,checks=results,
                full_task_pass=len(results)==11 and all(x['passed'] for x in results),
                main_stdout=main_result.stdout,main_stderr=main_result.stderr)


def grade(root):
    manifest=json.loads((root/'manifest.json').read_text())
    import tiktoken
    enc=tiktoken.get_encoding('cl100k_base')
    rows=[]
    for row in manifest['rows']:
        path=root/row['workspace'];result=grade_case(path,row['case'])
        reads=[]
        if (path/'reads.jsonl').exists():reads=[json.loads(x) for x in (path/'reads.jsonl').read_text().splitlines()]
        read_tokens=sum(len(enc.encode(r['text'])) for r in reads)
        rows.append(dict(row,**result,memory_reads=len(reads),read_proxy_tokens=read_tokens,
            visible_evidence_proxy_tokens=row['context_proxy_tokens']+read_tokens,
            candidate_sha256=hashlib.sha256((path/'study.py').read_bytes()).hexdigest()))
    summary={}
    for arm in manifest['conditions']:
        selected=[r for r in rows if r['condition']==arm]
        summary[arm]=dict(task_passes=sum(r['full_task_pass'] for r in selected),tasks=len(selected),
            checks_passed=sum(r['passed'] for r in selected),checks_total=sum(r['total'] for r in selected),
            mean_initial_context_proxy_tokens=statistics.mean(r['context_proxy_tokens'] for r in selected),
            mean_visible_evidence_proxy_tokens=statistics.mean(r['visible_evidence_proxy_tokens'] for r in selected),
            memory_reads=sum(r['memory_reads'] for r in selected))
    result=dict(protocol_version=1,summary=summary,rows=rows,
        tokenizer='cl100k_base; proxy only, not native provider usage',
        native_provider_usage=None,fixture_scope='synthetic development tasks; two fresh-agent trials per context arm; tasks batched within each agent',
        grader_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (root/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('study',type=Path)
    grade(p.parse_args().study)

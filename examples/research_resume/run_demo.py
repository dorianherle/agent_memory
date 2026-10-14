"""Resume an analytically verifiable cavity study in a fresh Python process.

This is a deterministic example. It makes no model calls and establishes no
AI capability improvement. Measurements are numerical, not hardware data.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from agent_memory import Memory

os.environ.setdefault('AGENT_MEMORY_NO_REVEAL','1')
# This example is local. Disable inherited embedding configuration before
# either process creates a memory store.
os.environ.pop('AGENT_MEMORY_PROVIDER', None)
os.environ.pop('GEMINI_API_KEY', None)


def measure(length):
    return [{'intervals':n,'length':length,
             'eigenvalue':4*n*n*math.sin(math.pi/(2*n))**2/length**2}
            for n in (16,32,64,128)]


def analyze(records,length):
    a,b,c=records[-3:]
    if any(r['length']!=length for r in records):raise ValueError('stale geometry')
    order=math.log2(abs(a['eigenvalue']-b['eigenvalue'])/abs(b['eigenvalue']-c['eigenvalue']))
    extrapolated=c['eigenvalue']+(c['eigenvalue']-b['eigenvalue'])/(2**order-1)
    reference=(math.pi/length)**2
    error=abs(extrapolated-reference)/reference
    return dict(length=length,observed_order=order,extrapolated=extrapolated,
                analytical_reference=reference,relative_error=error,
                qualified=1.8<=order<=2.2 and error<=1e-6,
                evidence_type='numerical finite-difference study with analytical check')


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def resume(path):
    memory=Memory(path)
    packet=memory.render_packet('Continue the cavity convergence study.')
    (path/'resume-packet.txt').write_text(packet)
    if '[stale]' not in packet:raise RuntimeError('Stale evidence was not marked.')
    cfg=json.loads((path/'config.json').read_text())
    old=json.loads((path/'report.json').read_text())
    if old['length']==cfg['length']:raise RuntimeError('The fixture did not change geometry.')
    records=measure(cfg['length'])
    report=analyze(records,cfg['length'])
    write(path/'samples.json',records);write(path/'report.json',report)
    memory.save('Current geometry passes the numerical convergence check.\n'
                f"Evidence: length={cfg['length']}; relative error={report['relative_error']:.6g}.\n"
                'Next: run a separate higher-resolution study before broader claims.',
                {'handoff':True,'files':['config.json','samples.json','report.json']})
    memory._catalog.close()
    return report


def demo(output):
    output.mkdir(parents=True,exist_ok=False)
    write(output/'config.json',dict(length=1.0))
    records=measure(1.0);report=analyze(records,1.0)
    write(output/'samples.json',records);write(output/'report.json',report)
    memory=Memory(output)
    memory.save_user('Check cavity convergence. Keep numerical measurements and the analytical reference separate.')
    note=memory.save('The length-1 cavity passes this numerical check.\n'
        'Decision: use measured convergence order for extrapolation.\n'
        'Rejected: reuse the result after a geometry change.\n'
        'Next: verify the current geometry before accepting this result.',
        {'handoff':True,'files':['config.json','samples.json','report.json']})
    memory._catalog.close()
    write(output/'config.json',dict(length=1.7))
    env=os.environ.copy();env.pop('AGENT_MEMORY_PROVIDER',None);env.pop('GEMINI_API_KEY',None)
    worker=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--resume',str(output)],
        capture_output=True,text=True,check=True,env=env,timeout=60)
    new=json.loads((output/'report.json').read_text())
    assert new['length']==1.7 and new['qualified']
    result=dict(fresh_process=True,stale_claim_marked=True,old_length=1.0,current_length=1.7,
                old_note=note,new_report=new,model_calls=0,claim='deterministic_workflow_example')
    write(output/'demo-result.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--output',type=Path);group.add_argument('--resume',type=Path)
    args=p.parse_args()
    if args.resume:print(json.dumps(resume(args.resume.resolve())))
    else:demo(args.output.resolve())

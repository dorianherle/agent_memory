"""Build blinded fresh-agent recovery fixtures. No model calls or credentials.

Generate before and after packets from identical histories, then evaluate model
JSON responses with --score. Token counts use cl100k_base as a consistent proxy,
not a claim about a provider's hidden prompt, reasoning, or billing.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory

CASES = [
    dict(id='constraints', goal='Prepare the Vega export rollout.\n\nKeep processing in eu-west-3. Never enable public access.', question='Resume the Vega rollout. Return the required region and access mode.', expected={'region':'eu-west-3','access':'private'}, notes=[('save','Vega export serialization verified.\nEvidence: test_export passed.',{})]),
    dict(id='handoff', goal='Repair the Larch queue consumer.', question='Continue the Larch task. What exact command should run next?', expected={'command':'pytest tests/test_larch.py -k redelivery'}, notes=[('save','Larch queue patch implemented.',{}),('auto','Larch work is ready for the next agent.\nNext: pytest tests/test_larch.py -k redelivery\nDo not deploy before this passes.',{})]),
    dict(id='decision', goal='Fix Orion retry behavior without duplicate writes.', question='Resume Orion. Return the chosen retry mechanism and the rejected one.', expected={'chosen':'idempotency-key','rejected':'blind-retry'}, notes=[('save','Orion retry investigation completed.\nDecision: use idempotency-key.\nRejected: blind-retry duplicates writes.\nEvidence: integration test duplicate_write reproduced twice.',{})]),
    dict(id='stale', goal='Tune Nimbus worker concurrency.', question='Can the saved Nimbus worker count be used without rechecking? Return use_saved or recheck.', expected={'action':'recheck'}, notes=[('file','settings.txt','workers=6'),('save','Nimbus workers=6 is validated.',{'files':['settings.txt']}),('file','settings.txt','workers=18'),('save','Nimbus deployment settings changed; measurement pending.',{})]),
    dict(id='checkpoint', goal='Repair Cedar parser escape handling.', question='Continue the interrupted Cedar work. Return the next test command.', expected={'command':'pytest tests/test_cedar.py -k escaped_quote'}, notes=[('save','Cedar parser patch is ready for validation.\nNext: pytest tests/test_cedar.py -k escaped_quote\nEvidence: patch in parser.py; validation not run.',{'handoff':True}),('save','Cedar README typo fixed.',{})]),
    dict(id='unknown', goal='Measure Quartz search latency.', question='Return the measured Quartz p99 latency in ms, or unknown if not measured.', expected={'p99':'unknown'}, notes=[('save','Quartz search performance has not been measured.\nNext: run the production-shaped load fixture.',{})]),
    dict(id='simple', goal='Update Maple client timeout.', question='What timeout in seconds was selected for Maple?', expected={'seconds':45}, notes=[('save','Maple timeout selected: 45 seconds.',{})]),
    dict(id='buried', goal='Finish Alder migration safely.', question='Resume Alder. Return the rollback command.', expected={'command':'alder migrate --restore checkpoint-17'}, notes=[('save','Alder migration rehearsal findings.\n'+('The rehearsal log includes ordinary progress with no decision change.\n'*18)+'Rollback: alder migrate --restore checkpoint-17',{})]),
]


def populate(memory, case):
    memory.save_user(case['goal'])
    # Fixed distractor history: plausible earlier work, unrelated to current topic.
    for i in range(24):
        memory.save(f'Archive task {i}: formatter fixture checked.\nEvidence: formatting unchanged; no active work remains.', agent=f'worker-{i%3}')
    for kind, text, data in case['notes']:
        if kind == 'file':
            (memory._path/text).write_text(data)
        elif kind == 'auto':
            memory.auto_save(text, agent='worker-a')
        else:
            memory.save(text, data, agent='worker-a')


def build(output):
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    output.mkdir(parents=True, exist_ok=True)
    cases=[]
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix='memory-recovery-') as folder:
            m=Memory(folder); populate(m, case)
            t=time.perf_counter(); packet=m.render_packet(case['question']); ms=(time.perf_counter()-t)*1000
            # Captured tool responses: runner reveals only ids explicitly requested.
            notes={item.id[:8]:m.call('memory',{'action':'find','ids':[item.id]}) for item in m.history()}
            cases.append(dict(id=case['id'], question=case['question'], packet=packet, reads=notes, packet_tokens=len(enc.encode(packet)), packet_ms=round(ms,2)))
            m._catalog.close()
    (output/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
    (output/'gold.json').write_text(json.dumps({c['id']:c['expected'] for c in CASES},indent=2)+'\n')
    print(json.dumps({'cases':len(cases),'packet_tokens':sum(c['packet_tokens'] for c in cases),'output':str(output)}))


def score(path):
    answers=json.loads(path.read_text())
    if isinstance(answers,dict) and 'answers' in answers: answers=answers['answers']
    rows=[]
    for c in CASES:
        got=answers.get(c['id'],{})
        passed=all(str(got.get(k,'')).lower()==str(v).lower() for k,v in c['expected'].items())
        rows.append({'id':c['id'],'pass':passed,'got':got,'expected':c['expected']})
    print(json.dumps({'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows},indent=2))

def report(root):
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    rows = []
    for mode in ('before', 'after'):
        cases = json.loads((root / mode / 'cases.json').read_text())
        for trial in (2, 3):
            filename = f'trial-{trial}.json'
            if mode == 'after' and trial == 2:
                filename = 'trial-2-initial.json'
            record = json.loads((root / mode / filename).read_text())
            result_text = record['result']['content'][0]['text']
            answers = json.loads(result_text)['answers']
            output_tokens = len(enc.encode(result_text))
            reads = {}
            if mode == 'before':
                initial = json.loads((root / mode / f'trial-{trial}-initial.json').read_text())
                text = initial['result']['content'][0]['text']
                output_tokens += len(enc.encode(text))
                reads = json.loads(text)['reads']
            expected = json.loads((root / mode / 'gold.json').read_text())
            correct = sum(all(str(answers.get(key, {}).get(k, '')).lower() == str(v).lower()
                              for k, v in gold.items()) for key, gold in expected.items())
            input_tokens = sum(len(enc.encode(p)) for p in record['prompts'])
            evidence_tokens = sum(c['packet_tokens'] for c in cases)
            evidence_tokens += sum(len(enc.encode(c['reads'][note]))
                                   for c in cases for note in reads.get(c['id'], []))
            rows.append(dict(mode=mode, trial=trial, correct=correct, total=len(cases),
                             input_tokens=input_tokens, output_tokens=output_tokens,
                             visible_total_tokens=input_tokens+output_tokens,
                             evidence_tokens=evidence_tokens,
                             body_reads=sum(map(len, reads.values()))))
    data = dict(tokenizer='cl100k_base', model='grok-4.6',
                scope='visible benchmark text only; provider hidden context/reasoning/billing unavailable',
                rows=rows)
    (root / 'report.json').write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(data, indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--score',type=Path)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.report: report(args.report)
    elif args.score: score(args.score)
    elif args.output: build(args.output)
    else: parser.error('use --output, --score or --report')

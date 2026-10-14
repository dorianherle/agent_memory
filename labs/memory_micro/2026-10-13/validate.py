"""Inventory evidence and native usage without pooling different experiments."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def main():
    manifest=json.loads((ROOT/'suite.json').read_text())
    inventory=[]
    for item in manifest['experiments']:
        directory=ROOT/item['path']
        files={name:(directory/name).exists() for name in ['protocol.json','script.py','results.json','report.txt']}
        calls=[]
        for p in sorted(directory.rglob('result.json')):
            try: r=json.loads(p.read_text())
            except ValueError: continue
            if 'prompt_sha256' not in r: continue
            usage=r.get('usage')
            calls.append({'path':str(p.relative_to(ROOT)), 'ok':r.get('ok'),
                          'usage_complete':r.get('usage_complete'), 'usage':usage,
                          'errors':r.get('errors'), 'elapsed_seconds':r.get('elapsed_seconds'),
                          'native_tools':r.get('native_tools')})
        known=[c for c in calls if c['usage'] is not None]
        unknown=sum(not c['usage_complete'] for c in calls)
        inventory.append({'id':item['id'],'path':item['path'],'files':files,
                          'model_calls':len(calls),'failed_calls':sum(not c['ok'] for c in calls),
                          'unknown_or_incomplete_usage_calls':unknown,
                          'known_native_usage':{key:sum(c['usage'][key] for c in known)
                                                for key in ['input_tokens','output_tokens']},
                          'native_usage_applicable':bool(calls),
                          'total_native_usage_verified':unknown==0 if calls else None,
                          'artifact_sha256':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                             for name in files if files[name]},
                          'calls':calls})
    output={'date':manifest['date'],'purpose':manifest['purpose'],'experiments':inventory,
            'limits':manifest['shared_limits'],
            'accounting_note':'Known usage excludes parent and subagent orchestration. Training costs are separate. Missing usage is unknown, not zero.'}
    (ROOT/'evidence-inventory.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps([{k:r[k] for k in ['id','files','model_calls','failed_calls','unknown_or_incomplete_usage_calls','known_native_usage']} for r in inventory],indent=2))

if __name__=='__main__': main()

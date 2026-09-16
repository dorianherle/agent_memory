import json, shutil, subprocess, sys
from pathlib import Path
import argparse, tempfile
parser=argparse.ArgumentParser(description='Prepare isolated coding recovery arms; evaluate only after agents finish.')
parser.add_argument('--baseline-ref', default='42d0da098d6b485c670c17ef9b535b4e53677940')
args=parser.parse_args()
root=Path(tempfile.mkdtemp(prefix='agent-memory-coding-recovery-'))
source=Path(__file__).resolve().parents[1]
engine=root/'baseline_engine';engine.mkdir(exist_ok=True)
shutil.copytree(source/'agent_memory',engine/'agent_memory',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','tests'))
for name in ('core.py','prompt.py','tools.py'):
 (engine/'agent_memory'/name).write_bytes(subprocess.check_output(['git','show',f'{args.baseline_ref}:agent_memory/{name}'],cwd=source))
code='''class Deduplicator:
    def __init__(self, ttl):
        self.ttl = ttl
        self.seen = {}

    def accept(self, tenant, event_id, now):
        # Earlier worker built the interface; finish the policy from the handoff.
        key = event_id
        if key in self.seen:
            return False
        self.seen[key] = now
        return True
'''
goal='''Finish Aspen event deduplication.
Constraints: keys are (tenant, event_id); duplicates never refresh expiry.
Expiry: age >= ttl accepts again. Empty event_id bypasses tracking.
Clock: retain a global monotonic watermark; use max(now, watermark) on every call.
Memory: remove all expired entries on every call, including empty event_id calls.
Public API: Deduplicator(ttl).accept(tenant, event_id, now) returns bool; keep .seen as a dictionary.
'''
for mode in ('before','after'):
 path=root/mode;path.mkdir(exist_ok=True);(path/'dedup.py').write_text(code)
 generator='''import json,sys
from agent_memory import Memory
from pathlib import Path
root=Path(sys.argv[1]);m=Memory(root)
m.save_user(sys.argv[2]);m.save('Aspen interface implemented; policy still pending.\\nNext: finish accept using the user constraints, then test tenant isolation, expiry and clock rollback.',agent='worker-a')
print(json.dumps({'packet':m.render_packet('Continue Aspen deduplication.'),'notes':{x.id[:8]:m.call('memory',{'action':'find','ids':[x.id]}) for x in m.history()}}))
'''
 py=root/f'generate-{mode}.py';py.write_text(generator)
 env=dict(__import__('os').environ);env['PYTHONPATH']=str(engine if mode=='before' else source)
 data=subprocess.check_output([str(source/'.venv/bin/python'),str(py),str(path),goal],env=env,text=True)
 (root/f'{mode}-context.json').write_text(data)
 (path/'memory-notes.json').write_text(json.dumps(json.loads(data)['notes'], indent=2))
 # Do not expose notebook or hidden source corpus to the receiving agent.
 shutil.move(path/'.agent_memory', root/f'{mode}-store')
print(root)

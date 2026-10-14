"""Check the installed private wheel; reuse existing dependency packages."""
import hashlib,json,sys,zipfile
from pathlib import Path
APP=Path(__file__).resolve().parent
sys.path.append('/Users/dorianherle/Documents/Personal/agent_memory/.venv/lib/python3.14/site-packages')
import agent_memory
from agent_memory import Memory
loaded=Path(agent_memory.__file__).resolve();assert 'installed-wheel-check' in loaded.parts,loaded
assert hashlib.sha256((loaded.parent/'core.py').read_bytes()).hexdigest()==json.loads((APP/'source-integration-final.json').read_text())['changed']['agent_memory/core.py']
wheel=APP/'private-wheel-final'/'agent_memory-0.1.0-py3-none-any.whl'
with zipfile.ZipFile(wheel) as z:
 assert 'agent_memory/studio.html' in z.namelist()
 assert 'agent_memory/studio_bridge.mjs' in z.namelist()
root=APP/'installed-wheel-workspace-final';root.mkdir(exist_ok=False)
(root/'config.json').write_text('{"length":1}')
m=Memory(root)
text='  Exact code\n\n\nvalue = """x  \ny"""\n\x02\x00\r\nDecomposed: e\u0301; noncharacter: \ufffe\n'
note=m.save(text,{'handoff':True,'files':['config.json']})
assert m.find(ids=note)[0].message==text
assert m.find(query='Exact',k=1)[0].id==note
(root/'config.json').write_text('{"length":2}')
assert '[stale]' in m.render_packet('Exact code')
m._catalog.close()
m=Memory(root);assert m.find(ids=note)[0].message==text;m._catalog.close()
result={'passed':True,'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'loaded_module':str(loaded),'exact_note_round_trip':True,'local_search':True,'stale_declared_config_detected':True,'reopen':True,'studio_assets_present':True,'dependency_limit':'Reuses existing dependency packages. This is not a fully clean dependency installation.','python':sys.version.split()[0]}
(APP/'wheel-smoke-final.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

"""Collect compact private proof records. Keep the large stores local."""
import hashlib,json,shutil,zipfile
from pathlib import Path
R=Path(__file__).resolve().parent; APP=R.parent; PKG=APP/'agent-memory-v6'; OUT=PKG/'benchmarks/research/evidence/v3'; OUT.mkdir(parents=True,exist_ok=True)
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def copy(src,name):
 dst=OUT/name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
archive=R/'source-snapshots-final.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for version in ('v3','v4','v5','v6'):
  base=APP/('agent-memory-'+version)
  for name in ('agent_memory/core.py','agent_memory/catalog.py','agent_memory/tests/test_reliability_audit.py','scripts/benchmark_public_retrieval.py','scripts/verify_public_retrieval.py','scripts/replay_public_retrieval.py','scripts/fetch_longmemeval.py','scripts/check_text_roundtrip.py'):
   p=base/name
   if p.exists(): z.write(p,version+'/'+name)
 z.write(R/'replay-before-input-guard.py','earlier-replay-without-input-guard.py')
(R/'source-snapshots-final-manifest.json').write_text(json.dumps({'archive':archive.name,'sha256':digest(archive),'bytes':archive.stat().st_size},indent=2)+'\n')
for p in R.iterdir():
 if p.is_file() and p.suffix in ('.json','.log','.xml','.zip'): copy(p,p.name)
for name in ('stage-0-results.json','stage-1-results.json','stage-2-results.json','verification.json','boundary-diagnostic.json','protocol.json','run-freeze.json','stage-2-freeze.json'):
 copy(R/'handoffs'/name,'handoffs/'+name)
for run in ('longmemeval-pilot','longmemeval-full-retrieval','longmemeval-full-retrieval-v4','longmemeval-full-retrieval-v5','longmemeval-full-retrieval-v6','longmemeval-final'):
 for name in ('results.json','protocol-freeze.json','verification.json','failure.json','integrity-check-notes.json','run-summary.json'):
  p=R/run/name
  if p.exists(): copy(p,run+'/'+name)
for name in ('provenance.json','download-provenance.json','eval_utils.py','UPSTREAM-LICENSE'):
 copy(R/'public-benchmarks'/name,'public-benchmarks/'+name)
for directory,name in [('research-resume-transfer-check','demo-result.json'),('text-roundtrip-check','results.json')]: copy(R/directory/name,directory+'/'+name)
copy(R/'private-wheel-final/agent_memory-0.1.0-py3-none-any.whl','private-wheel-final/agent_memory-0.1.0-py3-none-any.whl')
for name in ('summarize_public_run.py','prepare_evidence_delivery.py','smoke_installed_wheel_final.py'): copy(R/name,'reproduction/'+name)
raw={run:str(R/run) for run in ('longmemeval-pilot','longmemeval-full-retrieval','longmemeval-full-retrieval-v4','longmemeval-full-retrieval-v5','longmemeval-full-retrieval-v6','longmemeval-final')}
raw.update(dataset=str(R/'public-benchmarks/longmemeval_s_cleaned.json'),note='Large public corpus and raw stores remain private and local. Use the pinned downloader and fresh-ingestion scripts to reproduce retrieval. Earlier results retain their original failures and limits.')
(OUT/'raw-local-evidence.json').write_text(json.dumps(raw,indent=2)+'\n')
files={str(p.relative_to(OUT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='sha256.json'}
(OUT/'sha256.json').write_text(json.dumps(files,indent=2)+'\n'); print(json.dumps({'files':len(files),'bytes':sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()),'output':str(OUT)},indent=2))

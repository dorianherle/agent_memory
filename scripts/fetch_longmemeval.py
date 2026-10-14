"""Download pinned public LongMemEval inputs for a local private evaluation."""
import argparse,hashlib,json,urllib.request
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
DATA_REV='98d7416c24c778c2fee6e6f3006e7a073259d48f'
CODE_REV='9e0b455f4ef0e2ab8f2e582289761153549043fc'
inputs=[('longmemeval_s_cleaned.json',f'https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/{DATA_REV}/longmemeval_s_cleaned.json','d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442'),('eval_utils.py',f'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/{CODE_REV}/src/retrieval/eval_utils.py','c98b8d1096877a15aa755c9de44fe33c195298466a2eb6f3c0f9f6bde8c72349'),('UPSTREAM-LICENSE',f'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/{CODE_REV}/LICENSE','d3c4b9aa54759df6ded337978a6f3b55b75615e5e4525c3b82d7e2627d4b9732')]
rows=[]
for name,url,expected in inputs:
 target=a.output/name
 if not target.exists():
  part=target.with_suffix(target.suffix+'.part')
  with urllib.request.urlopen(url,timeout=120) as response,part.open('wb') as dest:
   while chunk:=response.read(1024*1024):dest.write(chunk)
  actual=hashlib.sha256(part.read_bytes()).hexdigest()
  if expected and actual!=expected:raise ValueError(f'Hash mismatch: {name}')
  part.replace(target)
 actual=hashlib.sha256(target.read_bytes()).hexdigest()
 if expected and actual!=expected:raise ValueError(f'Hash mismatch: {name}')
 rows.append({'file':name,'url':url,'sha256':actual,'bytes':target.stat().st_size});print(json.dumps(rows[-1]),flush=True)
(a.output/'download-provenance.json').write_text(json.dumps({'data_revision':DATA_REV,'code_revision':CODE_REV,'inputs':rows},indent=2)+'\n')

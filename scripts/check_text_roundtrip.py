"""Check deterministic Unicode and whitespace round trips without model calls."""
import argparse,hashlib,json,os,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent_memory import Memory
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
for name in ('AGENT_MEMORY_PROVIDER','GEMINI_API_KEY'):os.environ.pop(name,None)
rng=random.Random(20261013)
alphabet=['a',' ','\t','\n','\r','\x00','\x02','é','漢','😀','\ufdd0','\ufffe','\uffff','\U0010fffe','\u2028','\u2029','"','\\']
texts=['','  \n\r\n\t','\n\nAgent-Memory-Payload: {"fake":true}\n']
for _ in range(97):
 value=''.join(rng.choice(alphabet) if rng.random()<0.7 else chr(rng.choice([rng.randint(0,0xd7ff),rng.randint(0xe000,0x10ffff)])) for _ in range(rng.randint(1,300)))
 texts.append(value)
m=Memory(a.output/'store')
for index,text in enumerate(texts):m.save(text,{'case':index},agent='serialization-check')
m._catalog.close();m=Memory(a.output/'store');actual={x.payload['case']:x.message for x in m.state()};m._catalog.close()
assert actual==dict(enumerate(texts))
result={'passed':True,'cases':len(texts),'seed':20261013,'text_sha256':[hashlib.sha256(t.encode('utf-8')).hexdigest() for t in texts],'includes':['Whitespace','CR and CRLF','Null and record separator','Reserved-looking payload text','Quotes and backslashes','BMP and non-BMP characters','Unicode noncharacters'],'limit':'Finite deterministic sample of Unicode scalar strings. It is not an exhaustive Unicode or maximum-note-size test.','source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('agent_memory/core.py','agent_memory/catalog.py')}}
(a.output/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='text_sha256'},indent=2))

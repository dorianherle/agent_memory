"""Check the corrected source rule. This is not an agent performance study."""
from pathlib import Path
import hashlib
import json
import sys

root=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(root))
from agent_memory.models import prediction, reduce_structure, validate_model
from evaluation.model_transfer.tasks import build_task

model_path=root/'examples/source_dependency_model.json'
model=validate_model(json.loads(model_path.read_text()))
previous=json.loads((root/'evaluation/tests/fixtures/source_model_failures.json').read_text())['cases']
rows=[]
for i,case in enumerate(previous):
 result=prediction(model['rules'],case['inputs'],'needs_review')
 rows.append({'set':'previous_failure','case':i,'inputs':case['inputs'],'expected':case['expected'],
              'prediction':result,'pass':result.get('status')=='predicted' and result.get('value') is case['expected']})
for variant in (7,19,83):
 for stage in build_task('source_dependencies',variant)['stages']:
  for i,case in enumerate(stage['cases']):
   inputs={'request':case['request'],'policy':stage['policy']}
   result=prediction(model['rules'],inputs,'needs_review')
   expected=case['expected']['needs_review']
   rows.append({'set':'other_work_sample','variant':variant,'stage':stage['stage'],'case':i,
                'inputs':inputs,'expected':expected,'prediction':result,
                'pass':result.get('status')=='predicted' and result.get('value') is expected})
report={'study_type':'Engineering regression checks; no LLM workers or token-saving estimate',
        'scope':'needs_review Boolean, raw JSON inputs, string paths, at most 256 declared paths',
        'passed':sum(r['pass'] for r in rows),'total':len(rows),'checks':rows,
        'source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (model_path,root/'agent_memory/models.py',Path(__file__))}}
(Path(__file__).parent/'result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('study_type','passed','total')}))
if not all(r['pass'] for r in rows):raise SystemExit(1)

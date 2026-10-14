"""Replay source rules from tested stage snapshots with corrected input types.

This is an engineering audit. It does not change original worker results or
establish a token-saving estimate. Private inputs never enter worker context.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from agent_memory.models import check_inputs, prediction, reduce_structure
from evaluation.model_transfer.study import probe_inputs

folder = Path(sys.argv[1]).resolve()
manifest = json.loads((folder / 'manifest.json').read_text())
archived = {name: hashlib.sha256((folder / 'private/source' / name).read_bytes()).hexdigest()
            for name in manifest['source_hashes']}
assert archived == manifest['source_hashes'], 'Archived source changed'
tasks = {t['id']: t for t in json.loads((folder / 'private/task-pack.json').read_text())}
records = []
for path in sorted(folder.glob('runs/source_dependencies-*/repeat-0/models/stage-*/result.json')):
    result = json.loads(path.read_text())
    assert result['memory']['result']['checks']['status'] == 'tested'
    record = result['memory']['result']['model']
    model = record['model']
    assert not model['alternatives'], 'Replay needs alternative resolution'
    task = tasks[path.parts[-5]]
    stage = task['stages'][result['stage'] - 1]
    reduced = reduce_structure(model)
    for case in stage['cases'][stage['visible_count']:]:
        inputs = probe_inputs(task, stage, case, set(model['inputs']))
        check_inputs(model, inputs)
        expected = case['expected']['needs_review']
        before = prediction(model['rules'], inputs, 'needs_review')
        after = prediction(reduced['rules'], inputs, 'needs_review')
        records.append({'stage': result['stage'], 'model_id': record['id'], 'inputs': inputs,
                        'expected': expected, 'prediction': before, 'reduced_prediction': after,
                        'passed': before.get('status') == after.get('status') == 'predicted'
                                  and before.get('value') is expected and after.get('value') is expected,
                        'rules_before': len(model['rules']), 'rules_after': len(reduced['rules'])})
report = {'scored': False, 'purpose': 'Correct code evidence input from null to an empty array; replay recorded tested rule snapshots',
          'limits': ['Rule-interpreter replay of stage snapshots; not a new agent run',
                     'Original source-version checks were tested at each recorded stage',
                     'No token-saving inference from model size or these repeated checks'],
          'source_archive_verified': True, 'checks': len(records), 'passed': sum(r['passed'] for r in records),
          'interpreter_sha256': hashlib.sha256((ROOT / 'agent_memory/models.py').read_bytes()).hexdigest(),
          'records': records}
(folder / 'prediction-replay.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'records'}))
assert all(r['passed'] for r in records)

"""Read complete study logs without printing large model payloads."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
for log in sorted(root.glob('runs/**/run.jsonl')):
    events = [json.loads(line) for line in log.read_text().splitlines()]
    completions = [e for e in events if e['type'] == 'completion']
    operations = [e for e in events if e['type'] == 'tool']
    row = {'stage': str(log.parent.relative_to(root)), 'completions': len(completions),
           'operations': sum(e.get('operation_count', 1) for e in operations),
           'known_tokens': sum(e['usage']['input_tokens'] + e['usage']['output_tokens'] for e in completions),
           'decision_errors': sum(e['type'] == 'decision_error' for e in events)}
    stage = log.parent / 'result.json'
    if stage.exists():
        result = json.loads(stage.read_text())
        row.update(stop=result['stop'], passed=result['grade']['full_pass'], seconds=result['elapsed_seconds'])
        probes = result.get('model_probes', {})
        predictions = probes.get('predictions', {}).get('result', [])
        expected = probes.get('expected', [])
        row['probes'] = {'correct': sum(p.get('status') == 'predicted' and p.get('value') == e for p, e in zip(predictions, expected)),
                         'wrong': sum(p.get('status') == 'predicted' and p.get('value') != e for p, e in zip(predictions, expected)),
                         'unknown': sum(p.get('status') == 'unknown' for p in predictions), 'count': len(predictions)}
    elif operations:
        event = operations[-1]
        row['last_action'] = event['action']
        row['last_memory_action'] = event['arguments'].get('action')
    print(json.dumps(row, sort_keys=True))

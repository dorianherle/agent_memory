"""Record setup probes and abandoned development runs, without zero-cost gaps."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
rows = []
scored_folders = {'pilot-v3', 'pilot-v7'}
for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in scored_folders):
    completions = []
    for path in sorted(folder.rglob('events.jsonl')):
        events = []
        for line in path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        usages = [e.get('usage', {}) for e in events if e.get('type') == 'turn.completed']
        valid = [u for u in usages if all(type(u.get(k)) is int and u[k] >= 0 for k in ('input_tokens', 'output_tokens'))]
        result_path = path.with_name('completion.json')
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        complete = bool(valid) and result.get('usage_complete', False)
        completions.append({'events': str(path.relative_to(root)), 'usage_complete': complete,
                            'requested_model': result.get('requested_model'),
                            'known_tokens': sum(u['input_tokens'] + u['output_tokens'] for u in valid),
                            'cached_input_tokens': sum(u['cached_input_tokens'] for u in valid) if valid and all(type(u.get('cached_input_tokens')) is int for u in valid) else None,
                            'errors': result.get('errors', [])})
    if completions:
        rows.append({'run': folder.name, 'scored': False, 'completions': completions,
                     'known_tokens_lower_bound': sum(c['known_tokens'] for c in completions),
                     'unmetered_completions': sum(not c['usage_complete'] for c in completions)})
report = {'purpose': 'Engineering setup costs, separate from the frozen scored pilot',
          'scored_folders_excluded': sorted(scored_folders),
          'known_tokens_lower_bound': sum(r['known_tokens_lower_bound'] for r in rows),
          'unmetered_completions': sum(r['unmetered_completions'] for r in rows),
          'runs': rows,
          'limits': ['Interrupted or denied model calls can have missing usage. The total is a lower bound.',
                     'Do not combine setup runs with independent matched task results.',
                     'No billing data or money-saving estimate.']}
(root / 'setup-resources.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ('known_tokens_lower_bound', 'unmetered_completions')}))

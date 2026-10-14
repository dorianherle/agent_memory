"""Check frozen source and immutable trial inputs without changing candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(study: Path, source: Path) -> dict:
    manifest = json.loads((study / 'manifest.json').read_text())
    frozen = json.loads((study / 'protocol-freeze.json').read_text())
    violations = []
    groups = {}
    for row in manifest['rows']:
        workspace = study / row['workspace']
        common = {}
        for name, expected in row['input_sha256'].items():
            path = workspace / name
            if name == 'samples.json' and row['case'] == 'convergence':
                continue  # Explicitly permitted regeneration from current config.
            if not path.is_file() or digest(path) != expected:
                violations.append(f"{row['workspace']}/{name}")
            if name not in ('context.txt', 'memory_read.py'):
                common[name] = expected
        groups.setdefault((row['trial'], row['case']), []).append(common)
    mismatches = [f'trial-{trial}/{case}' for (trial, case), values in groups.items()
                  if any(value != values[0] for value in values[1:])]
    changed = [name for name, expected in frozen['files'].items()
               if not (source / name).is_file() or digest(source / name) != expected]
    return {
        'immutable_input_violations': violations,
        'frozen_source_changes': changed,
        'unequal_common_input_groups': mismatches,
        'passed': not (violations or changed or mismatches),
        'scope': 'Hash checks; this does not prove operating-system isolation.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = verify(args.study, args.source)
    (args.study / 'integrity-assessment.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)

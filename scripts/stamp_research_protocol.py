"""Correct the frozen v1 scorer's version label; keep its raw result unchanged.

This operation changes metadata only. It does not change candidates, checks,
scores, or token counts.
"""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('study', type=Path)
args = parser.parse_args()
result_path = args.study / 'results.json'
raw = result_path.read_bytes()
manifest = json.loads((args.study / 'manifest.json').read_text())
result = json.loads(raw)
if result['protocol_version'] != manifest['protocol_version']:
    digest = hashlib.sha256(raw).hexdigest()
    archived = args.study / f'raw-grader-result-{digest[:12]}.json'
    archived.write_bytes(raw)
    result['score_engine_protocol_version'] = result['protocol_version']
    result['protocol_version'] = manifest['protocol_version']
    result['metadata_correction'] = {
        'reason': 'The frozen scorer uses the version-1 label for both studies.',
        'raw_result': archived.name,
        'raw_sha256': digest,
        'check_results_changed': False,
    }
    result_path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'protocol_version': result['protocol_version'], 'scores_changed': False}))

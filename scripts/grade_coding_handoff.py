"""Grade an isolated dedup.py against the recorded Aspen user contract.

Run only after the receiving agent has finished; never expose these tests to it.
"""
import argparse
import importlib.util
import json
from pathlib import Path


def grade(path):
    spec = importlib.util.spec_from_file_location('handoff_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    make = lambda: module.Deduplicator(10)

    def initial_duplicate():
        d = make()
        assert d.accept('a', 'x', 0) is True
        assert d.accept('a', 'x', 1) is False

    def tenant_isolation():
        d = make()
        assert d.accept('a', 'x', 0) is True
        assert d.accept('b', 'x', 1) is True

    def expiry_boundary():
        d = make()
        d.accept('a', 'x', 0)
        assert d.accept('a', 'x', 10) is True

    def duplicate_does_not_refresh():
        d = make()
        d.accept('a', 'x', 0)
        assert d.accept('a', 'x', 9) is False
        assert d.accept('a', 'x', 10) is True

    def empty_bypasses_storage():
        d = make()
        assert d.accept('a', '', 0) is True
        assert d.accept('a', '', 1) is True
        assert d.seen == {}

    def rollback_uses_global_watermark():
        d = make()
        d.accept('a', 'x', 100)
        d.accept('b', 'y', 90)
        assert d.accept('b', 'y', 105) is False
        assert d.accept('b', 'y', 110) is True

    def removes_other_expired_keys():
        d = make()
        d.accept('a', 'x', 0)
        d.accept('b', 'y', 11)
        assert set(d.seen) == {('b', 'y')}

    def empty_calls_advance_clock_and_cleanup():
        d = make()
        d.accept('a', 'x', 0)
        assert d.accept('b', '', 20) is True
        assert d.seen == {}
        d.accept('a', 'x', 5)
        assert d.accept('a', 'x', 25) is False

    rows = []
    for test in (initial_duplicate, tenant_isolation, expiry_boundary,
                 duplicate_does_not_refresh, empty_bypasses_storage,
                 rollback_uses_global_watermark, removes_other_expired_keys,
                 empty_calls_advance_clock_and_cleanup):
        try:
            test()
            rows.append({'test': test.__name__, 'pass': True})
        except Exception as error:
            rows.append({'test': test.__name__, 'pass': False,
                         'error': f'{type(error).__name__}: {error}'})
    return {'passed': sum(r['pass'] for r in rows), 'total': len(rows), 'tests': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = grade(args.candidate)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

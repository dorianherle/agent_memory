"""Prepare a private, executable research handoff study. No model calls.

Three context conditions use the same source files and study history. Agents
must only inspect their assigned workspace. The grader remains outside it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import textwrap
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory

os.environ.setdefault("AGENT_MEMORY_NO_REVEAL", "1")

CASES = {
    "convergence": {
        "goal": "Finish the cavity convergence study in study.py. Implement assess(records, config) and main(). main writes report.json. Keep the numerical measurements separate from the analytical reference.",
        "requirements": """Decision: records contain intervals, length, and eigenvalue. Use the three finest records. The intervals must double at each refinement. Estimate observed_order = log2(abs(coarse-middle)/abs(middle-fine)). Use observed-order Richardson extrapolation: extrapolated = fine + (fine-middle)/(2**observed_order-1). relative_error compares extrapolated with (pi/config['length'])**2. qualified requires config['order_min'] <= observed_order <= config['order_max'] AND relative_error <= config['tolerance']. Return observed_order, extrapolated, relative_error, qualified. Raise ValueError for fewer than three records, nonfinite/nonpositive inputs, unequal geometry lengths, non-doubling grids, zero differences, or nonpositive observed order. Do not replace measured values with the analytical answer.""",
        "steering": "Constraint: use the CURRENT config length and tolerance. If stored sample lengths differ, main must run measure.py to regenerate samples before writing report.json. Never qualify the old geometry.",
        "handoff": "Cavity analysis is unfinished.\nDecision: use observed-order extrapolation, not an assumed second-order fit.\nRejected: fine-grid agreement alone can hide first-order convergence.\nNext: finish study.py, regenerate stale samples, and run main().",
        "source": """import json\nfrom pathlib import Path\n\ndef assess(records, config):\n    raise NotImplementedError('Continue the interrupted research study.')\n\ndef main():\n    raise NotImplementedError('Write report.json from current evidence.')\n\nif __name__ == '__main__':\n    main()\n""",
    },
    "vector_error": {
        "goal": "Finish the electromagnetic field assessment in study.py. Implement field_error(predicted, reference) and main(). Inputs to field_error are one-dimensional complex arrays. main reads fields.json and policy.json and writes report.json with field_error and qualified.",
        "requirements": """Decision: field_error is the L2 norm of the difference after separate unit-L2 normalization of the COMPLETE predicted and reference vectors, and ONE global complex phase alignment. The same phase applies to every component. It must be invariant to nonzero global scale and phase, but sensitive to relative component amplitude and phase. Raise ValueError for unequal lengths, non-one-dimensional inputs, empty arrays, nonfinite values, or zero vector norm. The JSON field arrays store [real, imag] pairs. qualified is field_error <= policy['field_error_limit']. Preserve the full vector and never normalize components independently.""",
        "steering": "Constraint: the current policy uses global complex-vector error. A similar magnitude plot does not qualify a field. Rejected: magnitude-only error and one phase per component.",
        "handoff": "Field comparison needs a scientific metric.\nDecision: one normalization per whole vector and one global phase.\nRejected: per-component normalization hides missing components.\nNext: implement the metric and assess the supplied field under current policy.",
        "source": """import json\nfrom pathlib import Path\n\ndef field_error(predicted, reference):\n    raise NotImplementedError('Continue the interrupted field assessment.')\n\ndef main():\n    raise NotImplementedError('Write report.json from fields.json and policy.json.')\n\nif __name__ == '__main__':\n    main()\n""",
    },
    "runtime_audit": {
        "goal": "Finish the learned-solver runtime audit in study.py. Implement summarize(rows, policy) and main(). main reads timings.json and policy.json and writes report.json. Keep accuracy coverage and speed separate.",
        "requirements": """Decision: every full candidate attempt includes load_ms + predict_ms + correct_ms + verify_ms + output_ms. training_ms is offline and excluded. baseline_ms is reference inverse CONSTRUCTION time; state baseline_contract='construction_only'. A qualified row has residual <= policy['residual_limit']. Return attempted, qualified, median_attempt_ms over ALL rows, median_qualified_speedup as median(baseline_ms/full_ms) over ONLY qualified rows or None if none, and median_inference_speedup as median(baseline_ms/predict_ms) over all rows. Return claim='no_qualified_speedup' if none pass, 'accuracy_limited' if only some pass, otherwise 'faster' if qualified median speedup > 1 or 'slower_or_equal'. Raise ValueError for empty rows, missing fields, nonfinite or negative values, zero full_ms/predict_ms/baseline_ms, or nonpositive residual_limit. Do not include failed outputs as speed wins.""",
        "steering": "Constraint: include correction, verification, loading, and output in fresh construction time. Pure inference can be reported separately. Preserve failed cases. Do not silently change the workload to reusable solves.",
        "handoff": "Runtime conclusion is unfinished.\nDecision: full fresh construction is the primary workload.\nRejected: an inference-only speed ratio is not the full result.\nNext: implement summarize and report coverage and both timing contracts.",
        "source": """import json\nfrom pathlib import Path\n\ndef summarize(rows, policy):\n    raise NotImplementedError('Continue the interrupted runtime audit.')\n\ndef main():\n    raise NotImplementedError('Write report.json from timings.json and policy.json.')\n\nif __name__ == '__main__':\n    main()\n""",
    },
}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def measurement_code():
    return textwrap.dedent('''\
        import json, math
        from pathlib import Path
        cfg = json.loads(Path('config.json').read_text())
        length = cfg['length']
        records = [{'intervals': n, 'length': length,
                    'eigenvalue': 4*n*n*math.sin(math.pi/(2*n))**2/length**2}
                   for n in (16, 32, 64, 128)]
        Path('samples.json').write_text(json.dumps(records, indent=2)+'\\n')
    ''')


def data_files(path, case, trial):
    if case == "convergence":
        length = 1.2 if trial == 1 else 1.7
        write_json(path / "config.json", dict(length=length, tolerance=1e-6,
                                               order_min=1.8, order_max=2.2))
        write_json(path / "samples.json", [dict(intervals=n, length=1.0,
            eigenvalue=4*n*n*math.sin(math.pi/(2*n))**2) for n in (16,32,64,128)])
        (path / "measure.py").write_text(measurement_code())
    elif case == "vector_error":
        # Equal magnitudes and different relative phases: magnitude error is zero.
        angle = math.pi if trial == 1 else math.pi / 2
        write_json(path / "fields.json", dict(reference=[[1,0],[1,0]],
            predicted=[[1,0],[math.cos(angle),math.sin(angle)]]))
        write_json(path / "policy.json", dict(field_error_limit=0.15))
    else:
        rows = [dict(matrix_id=str(i), load_ms=2, predict_ms=1+i,
            correct_ms=8+i, verify_ms=4, output_ms=3, training_ms=100000,
            baseline_ms=10+i, residual=1e-8 if i != trial else 1e-3)
            for i in range(4)]
        write_json(path / "timings.json", rows)
        write_json(path / "policy.json", dict(residual_limit=1e-6))


def prepare(output):
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"protocol_version": 1, "conditions": ["memory", "recent", "full"],
        "trials": 2, "cases": list(CASES), "fixture_type": "synthetic_development",
        "isolation": "instruction-scoped workspaces; no OS read-denial claim",
        "native_model_token_usage": None, "rows": []}
    store_root = output / "stores"
    for trial in (1, 2):
        for case, spec in CASES.items():
            store = store_root / f"trial-{trial}" / case
            store.mkdir(parents=True)
            data_files(store, case, trial)
            if case == "convergence":
                # Capture an earlier geometry, then modify its config without a save.
                original = json.loads((store / "config.json").read_text())
                write_json(store / "config.json", dict(original, length=1.0))
            memory = Memory(store)
            history = []
            def save(text, payload=None, user=False):
                history.append(text)
                return memory.save_user(text) if user else memory.save(text, payload or {}, agent="researcher")
            save(spec["goal"], user=True)
            save(spec["requirements"])
            save(spec["steering"], user=True)
            for i in range(30):
                save(f"Archive task {i}: log formatting checked.\nEvidence: text format is unchanged. This task is complete.")
            dependencies = ["config.json", "samples.json"] if case == "convergence" else ["policy.json"]
            save(spec["handoff"], {"handoff": True, "files": dependencies})
            if case == "convergence":
                write_json(store / "config.json", original)
            for i in range(12):
                save(f"Incidental update {i}: display spacing checked. No research decision changed.")
            t0 = time.perf_counter()
            packet = memory.render_packet("Continue the interrupted research study.", max_chars=1200)
            packet_ms = (time.perf_counter()-t0)*1000
            transcript = "\n\n".join(history)
            # Same character budget, chronological tail. This is a truncation stress baseline.
            contexts = {"memory": packet, "recent": transcript[-len(packet):], "full": transcript}
            for condition, context in contexts.items():
                work = output / "workspaces" / f"trial-{trial}" / condition / case
                work.mkdir(parents=True)
                data_files(work, case, trial)
                (work / "study.py").write_text(spec["source"])
                (work / "context.txt").write_text(context)
                # Common resume request includes interfaces, not lost historical decisions.
                (work / "TASK.md").write_text(spec["goal"] + "\n\nContinue from context.txt. Check the current data. Write a short HANDOFF.md with the result, tests, limits, and next action.\n")
                if condition == "memory":
                    shutil.copytree(store / '.agent_memory', work / '.agent_memory')
                    (work / "memory_read.py").write_text(textwrap.dedent('''\
                        import argparse,json,os
                        from pathlib import Path
                        from agent_memory import Memory
                        os.environ['AGENT_MEMORY_NO_REVEAL']='1'
                        p=argparse.ArgumentParser()
                        p.add_argument('--id');p.add_argument('--text');p.add_argument('--query')
                        p.add_argument('--full', action='store_true')
                        a=p.parse_args()
                        if not any((a.id,a.text,a.query)): p.error('Use --id, --text, or --query.')
                        m=Memory(Path(__file__).parent)
                        args={'action':'find','full':a.full}
                        if a.id: args['ids']=[a.id]
                        if a.text: args['text']=a.text
                        if a.query: args['query']=a.query
                        text=m.call('memory',args)
                        with Path(__file__).with_name('reads.jsonl').open('a') as f:
                            f.write(json.dumps({'arguments':args,'characters':len(text),'text':text})+'\\n')
                        m._catalog.close()
                        print(text)
                    '''))
                fingerprint = hashlib.sha256((spec["source"]+spec["goal"]+str(trial)).encode()).hexdigest()
                inputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in work.iterdir()
                        if p.is_file() and p.name!='study.py'}
                manifest["rows"].append(dict(trial=trial, condition=condition, case=case,
                    workspace=str(work.relative_to(output)), fixture_sha256=fingerprint,
                    input_sha256=inputs,
                    context_characters=len(context), context_proxy_tokens=len(enc.encode(context)),
                    packet_ms=round(packet_ms,3) if condition=="memory" else None))
            memory._catalog.close()
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"output":str(output),"task_attempts":len(manifest['rows'])}))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)

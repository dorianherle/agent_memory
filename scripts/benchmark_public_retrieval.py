"""Run a preselected LongMemEval-S retrieval pilot with a Markdown/BM25 control.

No answers or answer-location labels are stored. This is retrieval assessment,
not the official end-to-end question-answering score.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory

SELECTION_SEED = 'agent-memory-public-pilot-v1'


def metrics(retrieved, gold, k, corpus_ids=None):
    # Match the pinned upstream eval_utils.py, including its DCG convention.
    relevant = [int(sid in gold) for sid in retrieved[:k]]
    def dcg(values):
        return (values[0] if values else 0) + sum(value / math.log2(index + 1)
                                                       for index, value in enumerate(values[1:], 1))
    # Upstream counts repeated relevant corpus IDs in its ideal ranking.
    relevant_count = sum(sid in gold for sid in corpus_ids) if corpus_ids is not None else len(gold)
    ideal = dcg([1] * min(relevant_count, k))
    return {f'recall_any@{k}': float(bool(set(retrieved[:k]) & gold)),
            f'recall_all@{k}': float(gold <= set(retrieved[:k])),
            f'ndcg_any@{k}': dcg(relevant) / ideal if ideal else 0.0}


def select(data, per_type):
    groups = {}
    for row in data:
        if str(row['question_id']).endswith('_abs') or not row['answer_session_ids']:
            continue
        groups.setdefault(row['question_type'], []).append(row)
    selected = []
    for kind in sorted(groups):
        ordered = sorted(groups[kind], key=lambda row: hashlib.sha256(
            (SELECTION_SEED + str(row['question_id'])).encode()).hexdigest())
        selected.extend(ordered[:per_type])
    return selected


def assess(row, output):
    started = time.perf_counter()
    name = hashlib.sha256(str(row['question_id']).encode()).hexdigest()[:16]
    case = output / 'workspaces' / name
    (case / 'memory').mkdir(parents=True)
    notebook = case / 'notebook'
    notebook.mkdir()
    memory = Memory(case / 'memory')
    connection = sqlite3.connect(notebook / 'index.sqlite3')
    connection.execute('CREATE VIRTUAL TABLE notes USING fts5(session_id UNINDEXED, text, ordinal UNINDEXED)')
    mapping = {}
    save_ms = {'memory': [], 'notebook': []}
    content_hashes = []
    for index, (sid, date, turns) in enumerate(zip(row['haystack_session_ids'], row['haystack_dates'], row['haystack_sessions'])):
        # Only the role and content fields enter storage. Strip has_answer.
        text = f'Date: {date}\n' + '\n\n'.join(f"{turn['role']}: {turn['content']}" for turn in turns)
        content_hashes.append(hashlib.sha256(text.encode()).hexdigest())
        tick = time.perf_counter()
        note = memory.save(text, {'session_id': sid, 'date': date}, agent='public-corpus')
        mapping[note] = sid
        save_ms['memory'].append((time.perf_counter() - tick) * 1000)
        tick = time.perf_counter()
        (notebook / f'{index:04}.md').write_text(text)
        connection.execute('INSERT INTO notes VALUES (?,?,?)', (sid, text, index))
        connection.commit()
        save_ms['notebook'].append((time.perf_counter() - tick) * 1000)
    query = row['question']
    tick = time.perf_counter()
    found = memory.find(query=query, k=10, dedupe=False)
    memory_ms = (time.perf_counter() - tick) * 1000
    memory_ids = [mapping[item.id] for item in found]
    expression = ' OR '.join(f'"{word}"' for word in query.replace('"', ' ').split() if word)
    tick = time.perf_counter()
    notebook_ids = [hit[0] for hit in connection.execute(
        'SELECT session_id FROM notes WHERE notes MATCH ? ORDER BY bm25(notes), CAST(ordinal AS INTEGER) DESC LIMIT 10',
        (expression,)).fetchall()]
    notebook_ms = (time.perf_counter() - tick) * 1000
    gold = set(row['answer_session_ids'])
    # Gold labels enter only the external assessment after retrieval finishes.
    result = {'question_id': row['question_id'], 'question_type': row['question_type'],
              'sessions': len(mapping), 'content_sha256': content_hashes,
              'gold_session_ids': sorted(gold), 'stored_fields': ['date', 'role', 'content', 'session_id'],
              'results': {}}
    for condition, retrieved, latency in [('memory', memory_ids, memory_ms), ('notebook', notebook_ids, notebook_ms)]:
        result['results'][condition] = {'retrieved_session_ids': retrieved,
            'metrics': {**metrics(retrieved, gold, 5, row['haystack_session_ids']), **metrics(retrieved, gold, 10, row['haystack_session_ids'])},
            'warm_query_ms': latency, 'median_durable_save_ms': statistics.median(save_ms[condition])}
    memory._catalog.close()
    connection.close()
    result['elapsed_seconds'] = time.perf_counter() - started
    (case / 'assessment.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'question_id': row['question_id'], 'sessions': len(mapping),
                      'elapsed_seconds': round(result['elapsed_seconds'], 2)}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-type', type=int, default=2)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.per_type < 1 or args.workers < 1:
        parser.error('Counts must be positive.')
    for key in ('GEMINI_API_KEY', 'AGENT_MEMORY_PROVIDER'):
        os.environ.pop(key, None)
    os.environ['AGENT_MEMORY_NO_REVEAL'] = '1'
    args.output.mkdir(parents=True, exist_ok=False)
    selected = select(json.loads(args.dataset.read_text()), args.per_type)
    root = Path(__file__).resolve().parents[1]
    frozen = {'selection_seed': SELECTION_SEED, 'per_type': args.per_type,
              'selected_question_ids': [row['question_id'] for row in selected],
              'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                for name in ('scripts/benchmark_public_retrieval.py', 'agent_memory/core.py', 'agent_memory/catalog.py')}}
    (args.output / 'protocol-freeze.json').write_text(json.dumps(frozen, indent=2) + '\n')
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda row: assess(row, args.output), selected))
    summary = {}
    for condition in ('memory', 'notebook'):
        names = rows[0]['results'][condition]['metrics']
        summary[condition] = {name: statistics.mean(row['results'][condition]['metrics'][name] for row in rows) for name in names}
        summary[condition]['median_warm_query_ms'] = statistics.median(row['results'][condition]['warm_query_ms'] for row in rows)
        summary[condition]['median_case_save_ms'] = statistics.median(row['results'][condition]['median_durable_save_ms'] for row in rows)
    report = {'benchmark': 'LongMemEval-S cleaned', 'assessment': 'session retrieval pilot',
              'questions': len(rows), 'elapsed_seconds': time.perf_counter() - started,
              'no_model_calls': True, 'not_end_to_end_qa': True,
              'latency_limit': 'Warm queries and saves on one macOS host with concurrent corpus construction; one query per case. Not a latency distribution or production workload.',
              'comparison': 'Same exact session text and SQLite BM25 algorithm. Memory adds Git persistence and scope handling.',
              'summary': summary, 'rows': rows}
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'questions': len(rows), 'summary': summary}, indent=2))


if __name__ == '__main__':
    main()

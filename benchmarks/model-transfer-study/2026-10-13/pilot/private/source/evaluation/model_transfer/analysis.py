"""Task-level paired analysis. Missing usage never becomes zero cost."""
from __future__ import annotations

import math
import random

BASELINES = ("notebook", "notes")


def binomial_upper(failures, count, alpha=0.025):
    """Exact one-sided Clopper-Pearson bound; no scipy dependency."""
    if not 0 <= failures <= count or count < 1:
        raise ValueError("Invalid failure counts")
    if failures == count:
        return 1.0
    def cdf(p):
        if p == 1:
            return 0.0
        if p == 0:
            return 1.0
        logs = [math.lgamma(count + 1) - math.lgamma(k + 1) - math.lgamma(count - k + 1)
                + k * math.log(p) + (count - k) * math.log1p(-p) for k in range(failures + 1)]
        largest = max(logs)
        return math.exp(largest) * sum(math.exp(v - largest) for v in logs)
    low, high = 0.0, 1.0
    for _ in range(70):
        mid = (low + high) / 2
        if cdf(mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def ratio(rows, baseline):
    new = sum(r["models"]["tokens"] for r in rows)
    old = sum(r[baseline]["tokens"] for r in rows)
    new_pass = sum(r["models"]["complete"] for r in rows)
    old_pass = sum(r[baseline]["complete"] for r in rows)
    if not new_pass or not old_pass or not old:
        return None
    return (new / new_pass) / (old / old_pass)


def summarize(records, *, phase, expected_task_ids, repeats, bootstrap=10000, seed=1327):
    main = [r for r in records if r.get("comparison", "main") == "main"]
    groups = {}
    for r in main:
        key = (r["task"], r["repeat"], r["arm"])
        if key in groups:
            raise ValueError("Duplicate trajectory; do not count a retry as a new task")
        groups[key] = r
    expected = {(task, repeat, arm) for task in expected_task_ids for repeat in range(repeats)
                for arm in (*BASELINES, "models")}
    problems = []
    if set(groups) != expected:
        problems.append("Incomplete or unexpected task/method matrix")
    if any(r.get("tokens") is None or not r.get("usage_complete", False) for r in main):
        problems.append("Native token counts are missing")
    if any(not r.get("isolated", False) for r in main):
        problems.append("Worker isolation is unverified")
    if problems:
        return {"conclusion": "inconclusive", "phase": phase, "reasons": problems, "trajectories": len(main)}
    rows = []
    for task in expected_task_ids:
        row = {"task": task, "domain": groups[(task, 0, "models")]["domain"]}
        for arm in (*BASELINES, "models"):
            runs = [groups[(task, repeat, arm)] for repeat in range(repeats)]
            row[arm] = {"tokens": sum(r["tokens"] for r in runs) / repeats,
                        "complete": sum(bool(r["complete"]) for r in runs) / repeats}
        rows.append(row)
    comparisons = {}
    for baseline in BASELINES:
        point = ratio(rows, baseline)
        rng = random.Random(seed)
        samples = []
        for _ in range(bootstrap):
            # Repeat runs stay inside the task unit. Domain counts stay fixed.
            draw = []
            for domain in ("code", "research"):
                subset = [r for r in rows if r["domain"] == domain]
                draw.extend(rng.choices(subset, k=len(subset)))
            value = ratio(draw, baseline)
            if value is not None:
                samples.append(value)
        samples.sort()
        # One-sided Bonferroni bound over two baseline comparisons.
        upper = samples[min(len(samples) - 1, math.ceil(0.975 * len(samples)) - 1)] if samples else None
        lower = samples[max(0, math.floor(0.025 * len(samples)))] if samples else None
        extra_failures = sum(any(not groups[(t, r, "models")]["complete"] and
                                 groups[(t, r, baseline)]["complete"] for r in range(repeats))
                             for t in expected_task_ids)
        quality_upper = binomial_upper(extra_failures, len(rows))
        comparisons[baseline] = {"token_ratio": point, "ratio_interval": [lower, upper],
                                 "additional_failure_tasks": extra_failures,
                                 "additional_failure_upper": quality_upper,
                                 "cost_gain_supported": point is not None and upper is not None and upper < 1,
                                 "undefined_bootstrap_samples": bootstrap - len(samples)}
    by_domain = {}
    for domain in ("code", "research"):
        subset = [r for r in rows if r["domain"] == domain]
        by_domain[domain] = {arm: {"tokens": sum(r[arm]["tokens"] for r in subset),
                                  "completed_tasks": sum(r[arm]["complete"] for r in subset),
                                  "tasks": len(subset)} for arm in (*BASELINES, "models")}
    pilot_continue = all(c["token_ratio"] is not None and c["token_ratio"] <= 0.85 and
                         c["additional_failure_tasks"] == 0 for c in comparisons.values())
    def meets(threshold):
        return all(c["token_ratio"] is not None and c["token_ratio"] <= threshold and
                   c["cost_gain_supported"] and c["additional_failure_upper"] <= 0.05 and
                   c["undefined_bootstrap_samples"] == 0 for c in comparisons.values())
    conclusion = "exceptional" if meets(0.5) else "useful" if meets(0.75) else "no demonstrated benefit"
    if phase == "pilot":
        conclusion = "inconclusive"
    return {"conclusion": conclusion, "phase": phase, "pilot_continue": pilot_continue,
            "tasks": len(rows), "repeats": repeats, "comparisons": comparisons, "by_domain": by_domain,
            "limits": ["Constructed tasks from eight families; not independent real-world research evidence",
                       "Repeated runs and case checks are not independent tasks",
                       "Token counts are not monetary charges", "Intervals do not establish performance outside this task population"]}

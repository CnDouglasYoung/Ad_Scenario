#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export one full seven-day agent conversation per Harbor trial.

    python3 tools/export_trajectories.py work/harbor-jobs/script0 [...]

export/sft.jsonl: messages + tools + per-message loss_mask + exact provenance.
export/rl.jsonl: the same full interaction with the episode reward.
export/manifest.json: accepted/review/rejected trials and reasons.
Conditions remain in meta/run.json; they are never added to model messages.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

import evidence
from agent_sft import ConversionError, build_episode, read_jsonl, trial_sources


def write_jsonl(path, rows):
    """Never leave a partially written training file after an export error."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_run(run_dir):
    run_dir = Path(run_dir)
    archived = read_jsonl(run_dir / "export" / "events.jsonl")
    samples, report = [], []
    for trial_id, trial_dir, result in trial_sources(run_dir):
        try:
            sample = build_episode(trial_id, trial_dir, result, archived)
        except (ConversionError, evidence.EvidenceError, OSError, ValueError, KeyError) as exc:
            report.append({"trial_id": trial_id, "step_id": "day7", "status": "rejected",
                           "reason": str(exc),
                           "legacy_unverified": "缺少 manifest" in str(exc)})
            continue
        samples.append(sample)
        report.append({"trial_id": trial_id, "step_id": "day7",
                       "status": "candidate" if sample["meta"]["training_ready"] else "review",
                       "missing_context": sample["meta"]["missing_context"],
                       "messages": len(sample["messages"])})
    if not report:
        raise ConversionError(f"{run_dir}: 没有 Harbor trial steps")
    out = run_dir / "export"
    write_jsonl(out / "sft.jsonl", samples)
    write_jsonl(out / "rl.jsonl", [
        {**sample, "schema_version": "ad-agent-rl/v1", "reward": sample["meta"]["reward"]}
        for sample in samples])
    write_jsonl(out / "manifest.json", [{"schema_version": "ad-agent-export/v1",
                                         "trials": report, "n_episodes": len(samples)}])
    rejected = sum(item["status"] == "rejected" for item in report)
    review = sum(item["status"] == "review" for item in report)
    print(f"{run_dir}: 七日 SFT {len(samples)} 条 | review {review} | rejected {rejected}")
    for item in report:
        if item["status"] == "rejected":
            print(f"  {item['trial_id']}: {item['reason']}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for directory in args.run_dirs:
        try:
            report = export_run(directory)
            failed |= any(r["status"] == "rejected" for r in report)
        except (ConversionError, evidence.EvidenceError) as exc:
            print(f"{directory}: {exc}")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

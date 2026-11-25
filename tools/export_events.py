#!/usr/bin/env python3
"""Export all trials as auditable event streams, using the same evidence API as SFT.

    python3 tools/export_events.py RUN_DIR [...]

Prompts are read from runtime snapshots or existing archives, never today's task
files. Conditions remain in meta/run.json. Missing evidence is recorded explicitly.
"""
import argparse
from pathlib import Path

import evidence
from agent_sft import (ConversionError, archived_context, digest, read_json,
                       read_jsonl, trial_sources)
from export_trajectories import write_jsonl


def scrub(obj):
    """Remove hidden condition labels from verifier metadata, not model observations."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in ("script", "condition")}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def optional_json(path):
    return read_json(path) if path.exists() else None


def export_run(run_dir):
    run_dir = Path(run_dir)
    old_rows = read_jsonl(run_dir / "export" / "events.jsonl")
    records, trial_meta = [], []
    for trial_id, trial_dir, result in trial_sources(run_dir):
        begin, old_steps = archived_context(old_rows, trial_id)
        lock = read_json(trial_dir / "lock.json")
        task_digest = (lock.get("task") or {}).get("digest")
        try:
            manifest = evidence.load_manifest(trial_id)
            evidence_error = None
        except evidence.EvidenceError as exc:
            manifest, evidence_error = {}, str(exc)
        runtime_begin = manifest.get("run_begin") or {}
        if runtime_begin and runtime_begin.get("trial_id") != trial_id:
            raise ConversionError(f"{trial_id}: run/begin 身份不符")
        begin = {**begin, **runtime_begin}
        if begin.get("task_digest") not in (None, task_digest):
            raise ConversionError(f"{trial_id}: 存档 task_digest 与 trial lock 不符")
        # Do not copy labels into the event stream. Never change actual agent payloads.
        begin = {k: v for k, v in begin.items()
                 if k not in ("type", "seq", "condition", "script", "label")}
        begin.update(trial_id=trial_id, task_digest=task_digest)
        model = (lock.get("agent") or {}).get("model_name")
        begin.setdefault("model", model)
        begin.setdefault("agent", (lock.get("agent") or {}).get("name"))
        begin["legacy_unverified"] = evidence_error is not None

        def emit(kind, payload=None, step_id=None):
            rec = {**(payload or {}), "seq": len(records), "type": kind, "trial_id": trial_id}
            if step_id is not None:
                rec["step_id"] = step_id
                rec["day"] = int(step_id[3:])
            records.append(rec)

        emit("run/begin", begin)
        steps = sorted((trial_dir / "steps").glob("day*"), key=lambda p: int(p.name[3:]))
        count = 0
        for step_dir in steps:
            sid = step_dir.name
            attempts = (manifest.get("steps", {}).get(sid) or {}).get("attempts") or []
            attempt = attempts[-1] if attempts else {}
            header = dict(old_steps.get(sid, {}))
            if "instruction" in attempt:
                header.update(instruction=attempt["instruction"],
                              instruction_sha256=attempt.get("instruction_sha256"),
                              instruction_source="runtime")
            header.setdefault("instruction", None)
            header.setdefault("instruction_sha256", None)
            header.setdefault("instruction_source", "unknown")
            emit("step/begin", header, sid)
            if (header["instruction"] is not None and
                    digest(header["instruction"]) != header["instruction_sha256"]):
                emit("step/evidence", {"status": "invalid_instruction_hash"}, sid)
            try:
                events, source = evidence.load_events(trial_id, sid)
            except (evidence.EvidenceError, OSError, ValueError, KeyError) as exc:
                events, source = [], "missing"
                emit("step/evidence", {"status": "missing", "reason": str(exc)}, sid)
            if source == "missing":
                emit("step/evidence", {"status": "missing", "reason": evidence_error}, sid)
            for event in events:
                if event.get("type") in ("run/begin", "step/begin"):
                    continue  # Metadata has its own record; don't turn it into model output.
                emit(f"agent/{event.get('type')}",
                     {k: v for k, v in event.items() if k != "type"}, sid)
                count += 1
            vdir = step_dir / "verifier"
            reward_path = vdir / "reward.txt"
            reward = float(reward_path.read_text()) if reward_path.exists() else None
            emit("step/verifier", {"reward": reward,
                 **{key: scrub(optional_json(vdir / f"{key}.json"))
                    for key in ("state", "report", "score")}}, sid)
        score = optional_json(trial_dir / "steps" / "day7" / "verifier" / "score.json")
        if score is not None:
            emit("run/score", scrub(score))
        emit("run/end", {"events": count, "steps": len(steps)})
        trial_meta.append({"trial_id": trial_id, "model": begin.get("model"), "steps": len(steps)})
    if not trial_meta:
        raise ConversionError(f"{run_dir}: 没有 Harbor trial steps")
    write_jsonl(run_dir / "export" / "events.jsonl", records)
    meta_path = run_dir / "meta" / "run.json"
    meta = optional_json(meta_path) or {}
    meta["trials"] = trial_meta
    write_jsonl(meta_path, [meta])
    print(f"{run_dir}: {len(trial_meta)} trials → {len(records)} events")
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for directory in args.run_dirs:
        export_run(directory)


if __name__ == "__main__":
    main()

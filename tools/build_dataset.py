#!/usr/bin/env python3
"""Merge full-episode exports by trial UUID; split by episode reward, not day.

This is descriptive stratification. Use promote_sft.py for training selection.
"""
import argparse
import glob
import math
from pathlib import Path

from agent_sft import ConversionError, SCHEMA, read_jsonl, validate_episode
from export_trajectories import write_jsonl


def build_dataset(run_dirs, output, threshold=1.2):
    if not math.isfinite(threshold):
        raise ConversionError("threshold 必须是有限数值")
    rows, seen = [], set()
    for directory in map(Path, run_dirs):
        for sample in read_jsonl(directory / "export" / "sft.jsonl"):
            key = (sample.get("trial_id"), sample.get("step_id"))
            if sample.get("schema_version") != SCHEMA or None in key or key in seen:
                raise ConversionError(f"样本协议不符、身份缺失或重复: {key}")
            validate_episode(sample)
            seen.add(key)
            rows.append(sample)
    top, low, unscored = [], [], []
    for sample in rows:
        reward = sample.get("meta", {}).get("reward")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not math.isfinite(reward):
            unscored.append(sample)
        elif reward >= threshold:
            top.append(sample)
        else:
            low.append(sample)
    output = Path(output)
    for name, values in (("all", rows), ("top", top), ("low", low), ("unscored", unscored)):
        write_jsonl(output / f"{name}.jsonl", values)
    write_jsonl(output / "manifest.json", [{"n_all": len(rows), "n_top": len(top),
                "n_low": len(low), "n_unscored": len(unscored), "threshold": threshold,
                "training_selection": "use promote_sft.py; top is not a quality verdict"}])
    print(f"七日轨迹 {len(rows)} | top {len(top)} | low {len(low)} | unscored {len(unscored)}")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="*")
    parser.add_argument("--output", type=Path, default=Path("work/dataset"))
    parser.add_argument("--threshold", type=float, default=1.2)
    args = parser.parse_args(argv)
    build_dataset(args.run_dirs or sorted(glob.glob("work/harbor-jobs/script*")),
                  args.output, args.threshold)


if __name__ == "__main__":
    main()

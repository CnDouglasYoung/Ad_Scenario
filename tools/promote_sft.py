#!/usr/bin/env python3
"""Select whole seven-day SFT episodes using exact per-step quality verdicts.

    python3 tools/promote_sft.py --min-reward 1.2 [RUN_DIR ...]

All seven steps must be clean; full context and a valid episode reward are required.
Reward is a selection filter, not a guarantee that every action is optimal.
"""
import argparse
import glob
import math
from pathlib import Path

from agent_sft import ConversionError, clean_episode, read_json, read_jsonl
from export_trajectories import write_jsonl


def promote(run_dirs, output, min_reward=1.2):
    if not math.isfinite(min_reward):
        raise ConversionError("min_reward 必须是有限数值")
    promoted, rejected, seen, legacy = [], [], set(), []
    for directory in map(Path, run_dirs):
        gate_path = directory / "gate" / "report.json"
        gates = read_json(gate_path) if gate_path.exists() else []
        if not isinstance(gates, list):
            raise ConversionError(f"{gate_path}: 需要闸门结果数组")
        for sample in read_jsonl(directory / "export" / "sft.jsonl"):
            key = (sample.get("trial_id"), sample.get("step_id"))
            if None in key:
                # 没有 trial/step 身份的旧样本：不进训练集，单独标记
                legacy.append({"reason": "identity_missing", "legacy_unverified": True,
                               "run": directory.name})
                continue
            if key in seen:
                raise ConversionError(f"样本身份重复: {key}")
            seen.add(key)
            reasons = clean_episode(sample, gates, min_reward)
            sample.setdefault("meta", {})["selection"] = {
                "status": "rejected" if reasons else "promoted",
                "reasons": reasons, "min_reward": min_reward,
                "rule": "all_seven_steps_clean_and_valid_episode_reward"}
            (rejected if reasons else promoted).append(sample)
    output = Path(output)
    write_jsonl(output / "sft_promoted.jsonl", promoted)
    write_jsonl(output / "sft_rejected.jsonl", rejected)
    write_jsonl(output / "promote_manifest.json", [{"n_promoted": len(promoted),
                "n_rejected": len(rejected), "min_reward": min_reward}])
    if legacy:
        write_jsonl(output / "legacy_unverified.jsonl", legacy)
    print(f"完整七日 SFT: promoted {len(promoted)} | rejected {len(rejected)} | "
          f"legacy {len(legacy)} → {output}")
    return promoted, rejected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="*")
    parser.add_argument("--min-reward", type=float, default=1.2)
    parser.add_argument("--output", type=Path, default=Path("work/dataset"))
    args = parser.parse_args(argv)
    directories = args.run_dirs or sorted(glob.glob("work/harbor-jobs/script*"))
    promote(directories, args.output, args.min_reward)


if __name__ == "__main__":
    main()

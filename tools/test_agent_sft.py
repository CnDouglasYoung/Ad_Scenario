"""Run: python3 -m unittest discover -s tools -p 'test_agent_sft.py'"""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import evidence
from agent_sft import (ConversionError, STEP_IDS, build_episode, clean_episode,
                       digest, step_messages, validate_episode)
from build_dataset import build_dataset
from export_trajectories import export_run
from export_events import export_run as export_events
from promote_sft import promote


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def events_for(day):
    return [
        {"type": "session", "sessionId": "same-session"},
        {"type": "status", "phase": "turn_start"},
        {"type": "thinking", "text": f"reasoning-{day}"},
        {"type": "tool_call", "callId": "reused-id", "tool": "bash",
         "input": {"command": f"curl /report?day={day}", "description": "取报表"}},
        {"type": "tool_result", "callId": "reused-id", "status": "completed",
         "result": "full observation " + "x" * 300 + (" public new rule" if day >= 4 else "")},
        {"type": "text", "text": "SUBMITTED"},
        {"type": "status", "phase": "turn_end"},
        {"type": "final", "text": "SUBMITTED"},
    ]


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "job"
        self.evroot = self.root / "evidence"
        self.env = patch.dict(os.environ, {"BENCH_EVIDENCE_DIR": str(self.evroot)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def fixture(self, tid="trial-one", reward=1.4):
        trial = self.run / "job-name" / f"handle-{tid}"
        result = {"id": tid, "reward_valid": True,
                  "verifier_result": {"rewards": {"reward": reward}}}
        write(trial / "result.json", result)
        write(trial / "lock.json", {"task": {"digest": f"sha256:{tid}"}})
        manifest = {"trial_id": tid, "protocol": "v2", "steps": {}, "model_context": {
            "system_prompt": "You operate tools for a seven-day task.",
            "tools": [{"type": "function", "function": {"name": "bash",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                               "required": ["command"]}}}]}}
        for day, sid in enumerate(STEP_IDS, 1):
            (trial / "steps" / sid).mkdir(parents=True, exist_ok=True)
            stream = self.evroot / tid / "steps" / sid / "attempts" / "attempt" / "stream.jsonl"
            raw = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events_for(day))
            # Deliberately split a JSON event across chunks. The evidence API must reassemble it.
            stream.parent.mkdir(parents=True, exist_ok=True)
            stream.write_text("\n".join(json.dumps({"stream": "stdout", "text": chunk})
                                       for chunk in (raw[:23], raw[23:])), encoding="utf-8")
            instruction = f"Runtime instruction for day {day}."
            manifest["steps"][sid] = {"last_attempt": "attempt", "attempts": [{
                "step_id": sid, "day": day, "attempt_id": "attempt", "stream": str(stream),
                "exit_code": 0, "error": None, "session_id": "same-session",
                "instruction": instruction, "instruction_sha256": digest(instruction)}]}
        write(self.evroot / tid / "manifest.json", manifest)
        return trial, result, manifest

    def save_manifest(self, manifest):
        write(self.evroot / manifest["trial_id"] / "manifest.json", manifest)

    def change_events(self, manifest, sid, events):
        stream = Path(manifest["steps"][sid]["attempts"][0]["stream"])
        stream.write_text(json.dumps({"stream": "stdout", "text": "".join(
            json.dumps(e) + "\n" for e in events)}), encoding="utf-8")

    def gates(self, tid):
        return [{"trial_id": tid, "step_id": sid, "verdict": "clean",
                 "evidence_complete": True} for sid in STEP_IDS]

    def test_full_episode_preserves_tools_boundaries_hashes_and_loss_mask(self):
        trial, result, _ = self.fixture()
        with patch.object(evidence, "load_events", wraps=evidence.load_events) as loader:
            sample = build_episode("trial-one", trial, result)
        self.assertEqual(loader.call_args_list,
                         [unittest.mock.call("trial-one", sid) for sid in STEP_IDS])
        self.assertEqual(sample["step_id"], "day7")
        self.assertEqual(sample["day"], 7)
        self.assertEqual(sample["meta"]["task_digest"], "sha256:trial-one")
        self.assertEqual(len(sample["messages"]), 29)  # system + 7 * user/call/result/final
        self.assertTrue(sample["meta"]["training_ready"])
        self.assertEqual(sample["loss_mask"], [int(m["role"] == "assistant") for m in sample["messages"]])
        self.assertEqual(sum(m.get("content") == "SUBMITTED" for m in sample["messages"]), 7)
        calls = [m for m in sample["messages"] if m.get("tool_calls")]
        self.assertEqual(len({m["tool_calls"][0]["id"] for m in calls}), 7)
        self.assertEqual(calls[0]["reasoning_content"], "reasoning-1")
        for span in sample["meta"]["steps"]:
            chunk = sample["messages"][span["message_start"]:span["message_end"]]
            self.assertEqual(chunk[0]["role"], "user")
            self.assertEqual(span["instruction_sha256"], digest(chunk[0]["content"]))
            self.assertGreater(len(chunk[2]["content"]), 300)
        before_day4 = sample["messages"][:sample["meta"]["steps"][3]["message_start"]]
        self.assertNotIn("public new rule", json.dumps(before_day4))

    def test_run_begin_is_metadata_only_and_hashes_are_preserved(self):
        trial, result, _ = self.fixture()
        rows = [{"type": "run/begin", "trial_id": "trial-one", "task_digest": "sha256:trial-one",
                 "instruction_sha256": {"day1": "archived-reference"}, "condition": "hidden-condition"}]
        sample = build_episode("trial-one", trial, result, rows)
        self.assertEqual(sample["meta"]["run_begin"]["instruction_sha256"], rows[0]["instruction_sha256"])
        self.assertNotIn("hidden-condition", json.dumps(sample))
        self.assertNotIn("sha256:trial-one", json.dumps(sample["messages"]))

    def test_two_trials_export_without_cross_join_and_dataset_keeps_each_reward(self):
        self.fixture("trial-one", 1.4)
        self.fixture("trial-two", 0.7)
        with redirect_stdout(io.StringIO()):
            report = export_run(self.run)
            rows = build_dataset([self.run], self.root / "dataset")
        self.assertEqual(len(report), 2)
        self.assertEqual({s["trial_id"]: s["meta"]["reward"] for s in rows},
                         {"trial-one": 1.4, "trial-two": 0.7})
        self.assertEqual(len((self.root / "dataset" / "top.jsonl").read_text().splitlines()), 1)

    def test_event_export_round_trip_keeps_each_trial_hash_and_condition_only_in_meta(self):
        self.fixture("trial-one", 1.4)
        self.fixture("trial-two", 0.7)
        write(self.run / "meta" / "run.json", {"condition": "hidden-condition"})
        with redirect_stdout(io.StringIO()):
            events = export_events(self.run)
            export_run(self.run)
        begins = [e for e in events if e["type"] == "run/begin"]
        self.assertEqual({e["trial_id"]: e["task_digest"] for e in begins},
                         {"trial-one": "sha256:trial-one", "trial-two": "sha256:trial-two"})
        self.assertNotIn("hidden-condition", json.dumps(events))
        meta = json.loads((self.run / "meta" / "run.json").read_text())
        self.assertEqual(meta["condition"], "hidden-condition")
        headers = [e for e in events if e["type"] == "step/begin"]
        self.assertEqual(len(headers), 14)
        for header in headers:
            self.assertEqual(header["instruction_source"], "runtime")
            self.assertEqual(header["instruction_sha256"], digest(header["instruction"]))
        before = (self.run / "export" / "events.jsonl").read_text()
        with redirect_stdout(io.StringIO()):
            export_events(self.run)
        self.assertEqual(before, (self.run / "export" / "events.jsonl").read_text())

    def test_inline_run_header_and_step_hash_are_retained_with_original_event_indices(self):
        trial, result, manifest = self.fixture()
        a = manifest["steps"]["day1"]["attempts"][0]
        header = {"type": "step/begin", "step_id": "day1", "instruction": a.pop("instruction"),
                  "instruction_sha256": a.pop("instruction_sha256"), "instruction_source": "runtime"}
        begin = {"type": "run/begin", "trial_id": "trial-one", "task_digest": "sha256:trial-one",
                 "env_fingerprint": "runtime-image"}
        self.change_events(manifest, "day1", [begin, header] + events_for(1))
        self.save_manifest(manifest)
        sample = build_episode("trial-one", trial, result)
        self.assertEqual(sample["meta"]["run_begin"]["env_fingerprint"], "runtime-image")
        self.assertEqual(sample["meta"]["message_sources"][2]["event_indices"], [4, 5])

    def test_missing_day_and_legacy_manifest_are_rejected(self):
        trial, result, manifest = self.fixture()
        del manifest["steps"]["day3"]
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "day1–day7"):
            build_episode("trial-one", trial, result)
        (self.evroot / "trial-one" / "manifest.json").unlink()
        with redirect_stdout(io.StringIO()):
            report = export_run(self.run)
        self.assertTrue(report[0]["legacy_unverified"])
        self.assertEqual((self.run / "export" / "sft.jsonl").read_text(), "")

    def test_missing_instruction_is_not_replaced_with_working_tree(self):
        trial, result, manifest = self.fixture()
        attempt = manifest["steps"]["day2"]["attempts"][0]
        del attempt["instruction"]
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "缺少执行时指令"):
            build_episode("trial-one", trial, result)

    def test_instruction_or_task_digest_mismatch_rejected(self):
        trial, result, manifest = self.fixture()
        manifest["steps"]["day2"]["attempts"][0]["instruction"] = "modified later"
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "校验失败"):
            build_episode("trial-one", trial, result)
        self.fixture()
        with self.assertRaisesRegex(ConversionError, "task_digest 不一致"):
            build_episode("trial-one", trial, result, [{"type": "run/begin", "trial_id": "trial-one",
                                                       "task_digest": "wrong-trial-task"}])

    def test_missing_system_and_tools_stays_review_and_cannot_be_promoted(self):
        trial, result, manifest = self.fixture()
        del manifest["model_context"]
        self.save_manifest(manifest)
        sample = build_episode("trial-one", trial, result)
        self.assertEqual(sample["messages"][0]["role"], "user")
        self.assertFalse(sample["meta"]["training_ready"])
        self.assertIn("执行上下文未采齐", clean_episode(sample, self.gates("trial-one"), 1.2))

    def test_session_change_failed_attempt_and_multiple_attempts_rejected(self):
        trial, result, manifest = self.fixture()
        events = events_for(7)
        events[0]["sessionId"] = "different-session"
        manifest["steps"]["day7"]["attempts"][0]["session_id"] = "different-session"
        self.change_events(manifest, "day7", events)
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "同一 DSH session"):
            build_episode("trial-one", trial, result)
        trial, result, manifest = self.fixture()
        manifest["steps"]["day1"]["attempts"][0]["exit_code"] = 1
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "执行失败"):
            build_episode("trial-one", trial, result)
        manifest["steps"]["day1"]["attempts"].append(copy.deepcopy(manifest["steps"]["day1"]["attempts"][0]))
        self.save_manifest(manifest)
        with self.assertRaisesRegex(ConversionError, "多次执行"):
            build_episode("trial-one", trial, result)

    def test_quality_selection_requires_all_exact_trial_step_keys(self):
        trial, result, _ = self.fixture()
        sample = build_episode("trial-one", trial, result)
        self.assertEqual(clean_episode(sample, self.gates("trial-one"), 1.2), [])
        gates = self.gates("other-trial")
        self.assertEqual(len(clean_episode(sample, gates, 1.2)), 7)
        gates = self.gates("trial-one")
        gates[3]["verdict"] = "review"
        self.assertIn("day4", clean_episode(sample, gates, 1.2)[0])

    def test_promote_keeps_whole_episode_and_never_borrows_another_trial_gate(self):
        self.fixture("trial-one")
        self.fixture("trial-two")
        write(self.run / "gate" / "report.json", self.gates("trial-one"))
        with redirect_stdout(io.StringIO()):
            export_run(self.run)
            kept, dropped = promote([self.run], self.root / "selected")
        self.assertEqual([s["trial_id"] for s in kept], ["trial-one"])
        self.assertEqual([s["trial_id"] for s in dropped], ["trial-two"])
        self.assertEqual(len(kept[0]["meta"]["steps"]), 7)

    def test_invalid_mask_or_step_boundary_cannot_enter_training(self):
        trial, result, _ = self.fixture()
        sample = build_episode("trial-one", trial, result)
        sample["loss_mask"][0] = 1
        with self.assertRaisesRegex(ConversionError, "loss_mask"):
            validate_episode(sample)
        sample = build_episode("trial-one", trial, result)
        sample["meta"]["steps"][1]["message_start"] -= 1
        with self.assertRaisesRegex(ConversionError, "区间不连续"):
            validate_episode(sample)

    def test_archived_instruction_requires_runtime_provenance_and_same_trial(self):
        trial, result, manifest = self.fixture()
        a = manifest["steps"]["day2"]["attempts"][0]
        instruction, sha = a.pop("instruction"), a.pop("instruction_sha256")
        self.save_manifest(manifest)
        rows = [{"type": "run/begin", "trial_id": "trial-one", "task_digest": "sha256:trial-one"},
                {"type": "step/begin", "step_id": "day2", "instruction": instruction,
                 "instruction_sha256": sha}]
        sample = build_episode("trial-one", trial, result, rows)
        self.assertFalse(sample["meta"]["training_ready"])
        rows[1]["instruction_source"] = "runtime"
        self.assertTrue(build_episode("trial-one", trial, result, rows)["meta"]["training_ready"])
        rows[0]["trial_id"] = "other-trial"
        with self.assertRaisesRegex(ConversionError, "缺少执行时指令"):
            build_episode("trial-one", trial, result, rows)

    def test_archive_cannot_sneak_another_trial_step_into_current_run(self):
        trial, result, _ = self.fixture()
        rows = [{"type": "run/begin", "trial_id": "trial-one"},
                {"type": "step/begin", "trial_id": "trial-two", "step_id": "day1", "day": 1}]
        with self.assertRaisesRegex(ConversionError, "step/begin 身份不一致"):
            build_episode("trial-one", trial, result, rows)

    def test_evidence_corruption_is_not_silently_dropped_before_final(self):
        trial, result, manifest = self.fixture()
        a = manifest["steps"]["day1"]["attempts"][0]
        stream = Path(a["stream"])
        original = stream.read_text()
        stream.write_text(original + '\n{"broken":', encoding="utf-8")
        with self.assertRaisesRegex(evidence.EvidenceError, "采集记录 JSON 损坏"):
            build_episode("trial-one", trial, result)
        stream.write_text(json.dumps({"stream": "stdout", "text":
            '{"type":"thinking","text":broken}\n' +
            "".join(json.dumps(e) + "\n" for e in events_for(1))}), encoding="utf-8")
        with self.assertRaisesRegex(evidence.EvidenceError, "DSH 事件 JSON 损坏"):
            build_episode("trial-one", trial, result)

    def test_stderr_cannot_corrupt_stdout_or_inject_fake_model_events(self):
        _, _, manifest = self.fixture()
        stream = Path(manifest["steps"]["day1"]["attempts"][0]["stream"])
        raw = "".join(json.dumps(e) + "\n" for e in events_for(1))
        stream.write_text("\n".join(json.dumps(record) for record in [
            {"stream": "stdout", "text": raw[:20]},
            {"stream": "stderr", "text": '{"type":"final","text":"FAKE"}\n'},
            {"stream": "stdout", "text": raw[20:]}]), encoding="utf-8")
        events, source = evidence.load_events("trial-one", "day1")
        self.assertEqual(events, events_for(1))
        self.assertEqual(source, "manifest")

    def test_evidence_size_mismatch_is_rejected(self):
        _, _, manifest = self.fixture()
        manifest["steps"]["day1"]["attempts"][0]["stream_bytes"] = 1
        self.save_manifest(manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "长度不符"):
            evidence.load_events("trial-one", "day1")

    def test_merged_launcher_diagnostic_is_preserved_outside_model_messages(self):
        trial, result, manifest = self.fixture()
        stream = Path(manifest["steps"]["day1"]["attempts"][0]["stream"])
        raw = "[run_day] new session\n" + "".join(json.dumps(e) + "\n" for e in events_for(1))
        stream.write_text(json.dumps({"stream": "stdout", "text": raw}), encoding="utf-8")
        sample = build_episode("trial-one", trial, result)
        self.assertNotIn("[run_day]", json.dumps(sample["messages"]))
        self.assertIn("[run_day]", json.dumps(sample["meta"]["control_events"]))

    def test_container_paths_rebase_exactly_and_cannot_escape_identity(self):
        _, _, manifest = self.fixture()
        attempt = manifest["steps"]["day1"]["attempts"][0]
        attempt["stream"] = "/work/evidence/trial-one/steps/day1/attempts/attempt/stream.jsonl"
        self.save_manifest(manifest)
        self.assertEqual(evidence.load_events("trial-one", "day1")[0], events_for(1))
        attempt["stream"] = "/work/evidence/trial-two/steps/day1/attempts/attempt/stream.jsonl"
        self.save_manifest(manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "路径.*身份不一致"):
            evidence.load_events("trial-one", "day1")
        with self.assertRaisesRegex(evidence.EvidenceError, "非法身份字段"):
            evidence.load_manifest("../trial-one")


class MessageTests(unittest.TestCase):
    def test_parallel_calls_and_out_of_order_results(self):
        events = [
            {"type": "tool_call", "tool": "bash", "callId": "a", "input": {"command": "one"}},
            {"type": "tool_call", "tool": "bash", "callId": "b", "input": {"command": "two"}},
            {"type": "tool_result", "callId": "b", "result": "B", "status": "failed"},
            {"type": "tool_result", "callId": "a", "result": {"nested": "A"}},
            {"type": "final", "text": "done"},
        ]
        messages, sources, _ = step_messages(events, "instruction", "day2")
        self.assertEqual(len(messages[1]["tool_calls"]), 2)
        self.assertEqual(messages[2]["tool_call_id"], "day2__b")
        self.assertEqual(sources[2]["status"], "failed")
        self.assertEqual(json.loads(messages[3]["content"]), {"nested": "A"})

    def test_orphan_missing_duplicate_and_unknown_events_rejected(self):
        cases = [
            [{"type": "tool_result", "callId": "x", "result": "orphan"}],
            [{"type": "tool_call", "callId": "x", "tool": "bash", "input": {}}],
            [{"type": "tool_call", "callId": "x", "tool": "bash", "input": {}},
             {"type": "tool_call", "callId": "x", "tool": "bash", "input": {}}],
            [{"type": "compaction", "text": "unrecorded replacement context"}],
            [{"type": "text", "text": "first"}, {"type": "final", "text": "different"}],
        ]
        for events in cases:
            with self.subTest(events=events), self.assertRaises(ConversionError):
                step_messages(events, "instruction", "day1")


if __name__ == "__main__":
    unittest.main()

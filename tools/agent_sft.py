"""Seven-day agent SFT: observable messages, exact identities and explicit loss masks.

No synthetic prompts, tool results, successful submissions or missing days are added.
The chat schema is an intermediate format; a trainer must consume loss_mask and tools.
"""
import hashlib
import json
import math
from pathlib import Path

import evidence

STEP_IDS = tuple(f"day{n}" for n in range(1, 8))
SCHEMA = "ad-agent-sft/v1"


class ConversionError(ValueError):
    pass


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConversionError(f"无法读取 {path}: {exc}") from exc


def read_jsonl(path):
    if not Path(path).exists():
        return []
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ConversionError(f"{path}:{n}: 非法 JSON") from exc
            if not isinstance(row, dict):
                raise ConversionError(f"{path}:{n}: 记录必须是对象")
            rows.append(row)
    return rows


def trial_sources(run_dir):
    """Discover every Harbor trial; never infer identity from directory order/name."""
    seen = set()
    for steps in sorted(Path(run_dir).glob("**/steps")):
        if not steps.is_dir():
            continue
        trial_dir = steps.parent
        result = read_json(trial_dir / "result.json")
        trial_id = result.get("id")
        if not isinstance(trial_id, str) or not trial_id:
            raise ConversionError(f"{trial_dir}: result.json 缺少 trial UUID")
        if trial_id in seen:
            raise ConversionError(f"trial_id 重复: {trial_id}")
        seen.add(trial_id)
        yield trial_id, trial_dir, result


def archived_context(rows, trial_id):
    """Metadata only. Agent events must still come from evidence.load_events()."""
    active = False
    begin, steps = {}, {}
    for row in rows:
        typ = row.get("type")
        if typ == "run/begin":
            active = row.get("trial_id") == trial_id
            if active:
                if begin:
                    raise ConversionError(f"{trial_id}: 重复 run/begin")
                begin = row
        elif active and typ == "step/begin":
            sid = row.get("step_id") or f"day{row.get('day')}"
            if (row.get("trial_id") not in (None, trial_id) or sid not in STEP_IDS
                    or row.get("day") not in (None, int(sid[3:]))):
                raise ConversionError(f"{trial_id}: step/begin 身份不一致")
            if sid in steps:
                raise ConversionError(f"{trial_id}: 重复 step/begin {sid}")
            steps[sid] = row
        elif typ == "run/end":
            active = False
    return begin, steps


def step_messages(events, instruction, step_id):
    """Convert DSH events into chat messages; pair parallel calls without reordering.

    thinking is retained as reasoning_content, never fabricated or moved into user
    input. Duplicate text/final snapshots are emitted only once. Control events are
    retained separately and are never prediction targets.
    """
    messages = [{"role": "user", "content": instruction}]
    sources = [{"step_id": step_id, "event_indices": []}]
    controls, pending, used = [], {}, set()
    texts, thoughts, indices = [], [], []
    call_message = None
    final_seen = False

    def flush(calls=None):
        nonlocal texts, thoughts, indices
        msg = {"role": "assistant", "content": "".join(texts)}
        if thoughts:
            msg["reasoning_content"] = "".join(thoughts)
        if calls:
            msg["tool_calls"] = calls
        if msg["content"] or thoughts or calls:
            messages.append(msg)
            sources.append({"step_id": step_id, "event_indices": indices[:]})
        texts, thoughts, indices = [], [], []

    for i, event in enumerate(events):
        if not isinstance(event, dict):
            raise ConversionError(f"{step_id}: 事件必须是对象")
        typ = event.get("type", "").removeprefix("agent/")
        if typ in ("session", "status", "usage", "diagnostic"):
            controls.append({"event_index": i, "event": event})
            continue
        if final_seen:
            raise ConversionError(f"{step_id}: final 后仍有内容事件")
        if typ in ("thinking", "text", "final"):
            if pending:
                raise ConversionError(f"{step_id}: 工具未返回就出现 assistant 内容")
            value = event.get("text")
            if not isinstance(value, str):
                raise ConversionError(f"{step_id}: {typ}.text 缺失或类型错误")
            call_message = None
            indices.append(i)
            if typ == "thinking":
                thoughts.append(value)
            elif typ == "text":
                texts.append(value)
            else:
                streamed = "".join(texts)
                if not streamed or value.startswith(streamed):
                    texts = [value]
                elif value and value != streamed:
                    raise ConversionError(f"{step_id}: text 与 final 不一致，不能无损合并")
                final_seen = True
                flush()
        elif typ == "tool_call":
            call_id, name, args = event.get("callId"), event.get("tool"), event.get("input")
            if not isinstance(call_id, str) or not call_id or call_id in used:
                raise ConversionError(f"{step_id}: 工具 callId 缺失或重复")
            if not isinstance(name, str) or not name or not isinstance(args, dict):
                raise ConversionError(f"{step_id}: 工具名或参数缺失")
            # IDs may repeat on another day; the normalized IDs are episode-unique.
            normalized = f"{step_id}__{call_id}"
            call = {"id": normalized, "type": "function", "function": {
                "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            if pending:
                if call_message is None or texts or thoughts:
                    raise ConversionError(f"{step_id}: 无法表示交错的工具调用")
                messages[call_message]["tool_calls"].append(call)
                sources[call_message]["event_indices"].append(i)
            else:
                indices.append(i)
                flush([call])
                call_message = len(messages) - 1
            pending[call_id] = normalized
            used.add(call_id)
        elif typ == "tool_result":
            call_id = event.get("callId")
            if call_id not in pending or "result" not in event:
                raise ConversionError(f"{step_id}: 孤立、重复或缺少内容的工具返回")
            value = event["result"]
            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": pending.pop(call_id),
                             "content": content})
            sources.append({"step_id": step_id, "event_indices": [i],
                            "source_call_id": call_id, "status": event.get("status")})
            # More calls after the first result cannot be appended to an earlier turn.
            call_message = None
        else:
            raise ConversionError(f"{step_id}: 未支持的事件类型 {typ!r}")
    if pending or not final_seen:
        raise ConversionError(f"{step_id}: 工具返回不完整或缺少 final")
    return messages, sources, controls


def validate_episode(sample):
    """Validate the structure consumed by a trainer, including cross-day boundaries."""
    meta = sample.get("meta", {})
    messages = sample.get("messages", [])
    if (sample.get("schema_version") != SCHEMA or not sample.get("trial_id")
            or sample.get("step_id") != "day7" or sample.get("day") != 7
            or meta.get("step_ids") != list(STEP_IDS)):
        raise ConversionError("不是带完整身份的七日 agent SFT")
    if not messages or sample.get("loss_mask") != [int(m.get("role") == "assistant") for m in messages]:
        raise ConversionError("loss_mask 必须与消息对齐，只监督 assistant")
    sources, spans = meta.get("message_sources", []), meta.get("steps", [])
    if len(sources) != len(messages) or len(spans) != 7:
        raise ConversionError("消息来源或七日区间缺失")
    end = 1 if messages[0].get("role") == "system" else 0
    for sid, span in zip(STEP_IDS, spans):
        start, stop = span.get("message_start"), span.get("message_end")
        if (span.get("trial_id") != sample["trial_id"] or span.get("step_id") != sid
                or span.get("day") != int(sid[3:]) or start != end
                or not isinstance(stop, int) or stop <= start or stop > len(messages)):
            raise ConversionError(f"{sid}: 消息区间不连续或身份错误")
        user = messages[start]
        if (user.get("role") != "user" or not isinstance(user.get("content"), str)
                or digest(user["content"]) != span.get("instruction_sha256")
                or meta.get("instruction_sha256", {}).get(sid) != span["instruction_sha256"]):
            raise ConversionError(f"{sid}: 指令边界或摘要错误")
        if any(item.get("step_id") != sid for item in sources[start:stop]):
            raise ConversionError(f"{sid}: 消息来源跨日")
        if messages[stop - 1].get("role") != "assistant":
            raise ConversionError(f"{sid}: 缺少 assistant 结束消息")
        end = stop
    if end != len(messages):
        raise ConversionError("存在不属于七日区间的消息")
    pending, used = set(), set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            cid = message.get("tool_call_id")
            if cid not in pending:
                raise ConversionError("工具返回无法配对")
            pending.remove(cid)
        elif role in ("system", "user", "assistant"):
            if pending:
                raise ConversionError("工具未全部返回就开始下一消息")
            calls = message.get("tool_calls", [])
            for call in calls:
                cid = call.get("id")
                if not cid or cid in used or call.get("type") != "function":
                    raise ConversionError("工具调用 ID 缺失或重复")
                try:
                    args = json.loads(call["function"]["arguments"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ConversionError("工具参数不是 JSON") from exc
                if not isinstance(args, dict) or not call["function"].get("name"):
                    raise ConversionError("工具参数或名称错误")
                used.add(cid)
                pending.add(cid)
        else:
            raise ConversionError(f"未知消息角色: {role}")
    if pending:
        raise ConversionError("轨迹结束时工具仍未返回")


def build_episode(trial_id, trial_dir, result, archived_rows=()):
    manifest = evidence.load_manifest(trial_id)
    if result.get("id") != trial_id:
        raise ConversionError("Harbor result 与请求 trial_id 不一致")
    if not isinstance(manifest, dict):
        raise ConversionError("manifest 必须是对象")
    if manifest.get("protocol") != "v2":
        raise ConversionError("需要 v2 manifest；旧记录为 legacy_unverified")
    if set(manifest.get("steps", {})) != set(STEP_IDS):
        raise ConversionError("需要恰好 day1–day7 的完整证据")
    trial_dir = Path(trial_dir)
    lock = read_json(trial_dir / "lock.json")
    task_digest = (lock.get("task") or {}).get("digest")
    if not isinstance(task_digest, str) or not task_digest:
        raise ConversionError("trial lock 缺少 task_digest")
    begin, archived_steps = archived_context(archived_rows, trial_id)
    runtime_begin = manifest.get("run_begin") or {}
    if runtime_begin:
        if runtime_begin.get("trial_id") != trial_id:
            raise ConversionError("manifest run/begin 的 trial_id 不一致")
        for key in ("task_digest", "instruction_sha256"):
            if key in begin and key in runtime_begin and begin[key] != runtime_begin[key]:
                raise ConversionError(f"两份 run/begin 的 {key} 不一致")
        begin = {**begin, **runtime_begin}
    if begin.get("task_digest") not in (None, task_digest):
        raise ConversionError("run/begin 与 trial lock 的 task_digest 不一致")

    messages, provenance, spans, controls = [], [], [], []
    hashes, session_ids, missing = {}, set(), set()
    model_context = manifest.get("model_context") or {}
    if not isinstance(model_context, dict):
        raise ConversionError("model_context 必须是对象")
    if model_context.get("system_prompt") is not None:
        system = model_context["system_prompt"]
        if not isinstance(system, str):
            raise ConversionError("system_prompt 必须是执行时记录的文本")
        messages.append({"role": "system", "content": system})
        provenance.append({"step_id": "day1", "event_indices": []})
    else:
        missing.add("system_prompt")
    tools = model_context.get("tools")
    if not isinstance(tools, list) or not tools:
        missing.add("tool_definitions")
        tools = None
    else:
        for tool in tools:
            fn = tool.get("function", {}) if isinstance(tool, dict) else {}
            if (not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(fn.get("name"), str)
                    or not isinstance(fn.get("parameters"), dict)):
                raise ConversionError("需要真实完整的 function 工具定义")

    for day, sid in enumerate(STEP_IDS, 1):
        step = manifest["steps"][sid]
        attempts = step.get("attempts") or []
        # A failed earlier invocation can have changed DSH memory. Do not pretend
        # the last invocation alone is a full continuation of the teacher context.
        if len(attempts) != 1:
            raise ConversionError(f"{sid}: 多次执行需要显式上下文恢复记录")
        attempt = attempts[0]
        if (not isinstance(attempt, dict) or attempt.get("step_id") != sid or attempt.get("day") != day
                or not attempt.get("attempt_id")):
            raise ConversionError(f"{sid}: attempt 身份不一致")
        if attempt.get("exit_code") != 0 or attempt.get("error"):
            raise ConversionError(f"{sid}: 执行失败或未结束")
        if step.get("last_attempt", attempt["attempt_id"]) != attempt["attempt_id"]:
            raise ConversionError(f"{sid}: last_attempt 不一致")
        events, source = evidence.load_events(trial_id, sid)
        if source != "manifest" or not events:
            raise ConversionError(f"{sid}: 缺少权威事件流")
        if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
            raise ConversionError(f"{sid}: 事件必须是对象列表")
        for event in events:
            if event.get("type") == "run/begin":
                if event.get("trial_id") != trial_id or event.get("task_digest") != task_digest:
                    raise ConversionError(f"{sid}: 事件 run/begin 身份或 task_digest 不一致")
                for key in ("task_digest", "instruction_sha256"):
                    if key in begin and key in event and begin[key] != event[key]:
                        raise ConversionError(f"{sid}: run/begin {key} 冲突")
                begin = {**begin, **event}
        header = archived_steps.get(sid, {})
        inline = [e for e in events if e.get("type") == "step/begin"]
        if inline:
            if len(inline) != 1:
                raise ConversionError(f"{sid}: 重复 step/begin")
            header = inline[0]
        if (header.get("trial_id") not in (None, trial_id)
                or header.get("step_id") not in (None, sid)
                or header.get("day") not in (None, day)):
            raise ConversionError(f"{sid}: step/begin 身份不一致")
        instruction = attempt.get("instruction", header.get("instruction"))
        sha = attempt.get("instruction_sha256", header.get("instruction_sha256"))
        if not isinstance(instruction, str) or not isinstance(sha, str):
            raise ConversionError(f"{sid}: 缺少执行时指令与 instruction_sha256")
        if digest(instruction) != sha:
            raise ConversionError(f"{sid}: instruction_sha256 校验失败")
        if header.get("instruction_sha256") not in (None, sha):
            raise ConversionError(f"{sid}: 两份指令快照不一致")
        if "instruction" not in attempt and header.get("instruction_source") != "runtime":
            missing.add("runtime_instruction_provenance")
        hashes[sid] = sha
        session_values = [e.get("sessionId") for e in events if e.get("type") == "session"]
        if any(not isinstance(s, str) or not s for s in session_values):
            raise ConversionError(f"{sid}: session 身份类型错误")
        sid_events = set(session_values)
        if len(sid_events) != 1:
            raise ConversionError(f"{sid}: session 身份缺失或冲突")
        session_ids.update(sid_events)
        if attempt.get("session_id") not in (None, next(iter(sid_events))):
            raise ConversionError(f"{sid}: manifest 与事件的 session 不一致")
        body_indices = [i for i, e in enumerate(events)
                        if e.get("type") not in ("run/begin", "step/begin")]
        body = [events[i] for i in body_indices]
        normalized, sources, control = step_messages(body, instruction, sid)
        start = len(messages)
        messages.extend(normalized)
        for item in sources:
            item["attempt_id"] = attempt["attempt_id"]
            item["event_indices"] = [body_indices[i] for i in item["event_indices"]]
        for item in control:
            item["event_index"] = body_indices[item["event_index"]]
        provenance.extend(sources)
        controls.append({"step_id": sid, "events": control})
        spans.append({"trial_id": trial_id, "step_id": sid, "day": day,
                      "attempt_id": attempt["attempt_id"], "instruction_sha256": sha,
                      "message_start": start, "message_end": len(messages)})
    if len(session_ids) != 1:
        raise ConversionError("七天不是同一 DSH session，不能拼作连续对话")
    if tools:
        names = {t.get("function", {}).get("name") for t in tools if isinstance(t, dict)}
        called = {c["function"]["name"] for m in messages for c in m.get("tool_calls", [])}
        if not called <= names:
            raise ConversionError("工具定义没有覆盖实际调用")

    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    if reward is not None and (isinstance(reward, bool) or not isinstance(reward, (int, float))
                               or not math.isfinite(reward)):
        raise ConversionError("整轮 reward 必须是有限数值")
    meta = {"task_digest": task_digest, "instruction_sha256": hashes,
            "run_begin": {**{k: v for k, v in begin.items() if k not in ("type", "seq")},
                          "trial_id": trial_id, "task_digest": task_digest},
            "step_ids": list(STEP_IDS), "steps": spans, "message_sources": provenance,
            "control_events": controls, "session_id": next(iter(session_ids)),
            "reward": reward, "reward_valid": result.get("reward_valid"),
            "context_fidelity": "observed_cli", "missing_context": sorted(missing),
            "training_ready": not missing, "training_use": "candidate" if not missing else "review",
            "loss_mask_semantics": "one entry per message; assistant=1, system/user/tool=0"}
    # Condition labels live only in meta/run.json, never in the conversational input.
    for key in ("condition", "script", "label"):
        meta["run_begin"].pop(key, None)
    sample = {"schema_version": SCHEMA, "trial_id": trial_id,
              "step_id": "day7", "day": 7, "messages": messages,
              "loss_mask": [int(m["role"] == "assistant") for m in messages], "meta": meta}
    if tools:
        sample["tools"] = tools
    validate_episode(sample)
    return sample


def clean_episode(sample, gate_rows, min_reward):
    """Whole-episode selection requires all seven exact step verdicts."""
    reasons, gates = [], {}
    try:
        validate_episode(sample)
    except ConversionError as exc:
        reasons.append(str(exc))
    for row in gate_rows:
        key = (row.get("trial_id"), row.get("step_id"))
        if key in gates and key[0] == sample["trial_id"]:
            reasons.append(f"重复闸门记录: {key}")
        gates[key] = row
    for sid in STEP_IDS:
        row = gates.get((sample["trial_id"], sid))
        if not row or row.get("verdict") != "clean" or row.get("evidence_complete") is not True:
            reasons.append(f"{sid}: 缺少同 trial 的完整 clean 闸门结果")
    if sample.get("schema_version") != SCHEMA or sample.get("meta", {}).get("step_ids") != list(STEP_IDS):
        reasons.append("不是完整七日 agent SFT")
    meta = sample.get("meta", {})
    if not meta.get("training_ready"):
        reasons.append("执行上下文未采齐")
    if meta.get("reward_valid") is not True:
        reasons.append("整轮 reward 未确认有效")
    reward = meta.get("reward")
    if (isinstance(reward, bool) or not isinstance(reward, (int, float))
            or not math.isfinite(reward) or reward < min_reward):
        reasons.append("整轮 reward 缺失或未达阈值")
    return reasons

# -*- coding: utf-8 -*-
"""按 manifest 精确读取证据（协议 v2）。

规则
- 必须给 trial_id + step_id；不跨 trial 猜测。
- 只读 manifest 指向的文件；缺失/损坏/身份不符分别报错。
- 容器内 /logs 日志仅作展示，不替代宿主证据。
"""
import hashlib
import json, os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAST_ERRORS: dict[tuple[str, str], int] = {}   # 损坏行计数，供闸门判断证据质量


def resolve_path(p: str) -> Path:
    """manifest 里可能记录容器内路径（/work/...），映射回宿主仓库根。"""
    path = Path(p)
    if path.exists():
        return path
    s = str(p)
    if s.startswith("/work/"):
        cand = REPO_ROOT / s[len("/work/"):]
        if cand.exists():
            return cand
    return path


class EvidenceError(RuntimeError):
    pass


def _root() -> Path:
    return Path(os.environ.get("BENCH_EVIDENCE_DIR", "evidence"))


def _segment(value, field):
    if (not isinstance(value, str) or not value or value in (".", "..")
            or "/" in value or "\\" in value):
        raise EvidenceError(f"非法身份字段: {field}")
    return value


def load_manifest(trial_id: str) -> dict:
    _segment(trial_id, "trial_id")
    p = _root() / trial_id / "manifest.json"
    if not p.exists():
        raise EvidenceError(f"缺少 manifest: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"manifest 损坏: {p}: {exc}") from exc
    if not isinstance(doc, dict):
        raise EvidenceError("manifest 必须是对象")
    if doc.get("trial_id") != trial_id:
        raise EvidenceError("manifest 的 trial_id 与请求不一致")
    return doc


def step_attempt(trial_id: str, step_id: str) -> dict:
    _segment(step_id, "step_id")
    doc = load_manifest(trial_id)
    step = (doc.get("steps") or {}).get(step_id)
    if not step:
        raise EvidenceError(f"manifest 中没有 {step_id}")
    if not isinstance(step, dict):
        raise EvidenceError(f"{step_id} 记录格式错误")
    attempts = step.get("attempts") or []
    if not isinstance(attempts, list) or not attempts:
        raise EvidenceError(f"{step_id} 没有执行记录")
    if not isinstance(attempts[-1], dict):
        raise EvidenceError(f"{step_id} attempt 格式错误")
    if step.get("last_attempt", attempts[-1].get("attempt_id")) != attempts[-1].get("attempt_id"):
        raise EvidenceError(f"{step_id} last_attempt 与执行记录不一致")
    return attempts[-1]


def load_events(trial_id: str, step_id: str) -> tuple[list, str]:
    """返回 (事件列表, 'manifest')；缺失、损坏、身份不符抛 EvidenceError。"""
    a = step_attempt(trial_id, step_id)
    if a.get("step_id") != step_id:
        raise EvidenceError("attempt 的 step_id 与请求不一致")
    attempt_id = _segment(a.get("attempt_id"), "attempt_id")
    # /work/evidence is the collector container's mount path. Rebase its exact
    # logical identity under the caller's root; this is not a glob/fallback search.
    suffix = Path(trial_id) / "steps" / step_id / "attempts" / attempt_id / "stream.jsonl"
    recorded = a.get("stream")
    if not isinstance(recorded, str):
        raise EvidenceError("manifest 缺少 stream 路径")
    recorded = Path(recorded)
    if (recorded.parts[-len(suffix.parts):] != suffix.parts
            and recorded != Path(*suffix.parts[1:])):
        raise EvidenceError("manifest 的 stream 路径与 trial/step/attempt 身份不一致")
    p = _root() / suffix
    if not p.exists():
        raise EvidenceError(f"证据文件缺失: {p}")
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"证据文件无法读取: {p}: {exc}") from exc
    if "stream_bytes" in a and a["stream_bytes"] != len(raw):
        raise EvidenceError(f"证据长度不符: {p}")
    if a.get("stream_sha256") and hashlib.sha256(raw).hexdigest() != a["stream_sha256"]:
        raise EvidenceError(f"证据摘要不符: {p}")
    chunks = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as exc:
            raise EvidenceError(f"{p}:{n}: 采集记录 JSON 损坏") from exc
        if not isinstance(rec, dict) or not isinstance(rec.get("text"), str):
            raise EvidenceError(f"{p}:{n}: 采集记录格式错误")
        stream = str(rec.get("stream", "")).lower().rsplit(".", 1)[-1]
        if stream == "stderr":
            continue  # Never splice stderr diagnostics into a partial stdout JSON event.
        if stream != "stdout":
            raise EvidenceError(f"{p}:{n}: 未知输出流 {stream!r}")
        chunks.append(rec["text"])
    out = []
    for n, line in enumerate("".join(chunks).splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # Some Docker exec modes label merged stderr as stdout. The known launcher
        # diagnostic is not a DSH model event; retain it explicitly for auditing.
        if line.startswith("[run_day] "):
            out.append({"type": "diagnostic", "text": line})
            continue
        try:
            event = json.loads(line)
        except ValueError:
            LAST_ERRORS[(trial_id, step_id)] = LAST_ERRORS.get((trial_id, step_id), 0) + 1
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            LAST_ERRORS[(trial_id, step_id)] = LAST_ERRORS.get((trial_id, step_id), 0) + 1
            continue
        out.append(event)
    return out, "manifest"

"""Harbor 的 DSH 适配器（协议 v2）。

身份：trial_id（Harbor context_id）/ step_id（Harbor 步骤目录名）/ attempt_id（每次执行）/ request_id（每次提交）
时序：open_step → 采集执行 → freeze → 拉账本快照
证据：evidence/<trial_id>/steps/<step_id>/attempts/<attempt_id>/stream.jsonl + manifest.json
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.agent_auth_resolution import ModelConnectionSpec, ProviderAccess
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.model_api_format import ModelApiFormat

DAY_RE = re.compile(r"^day(\d+)$")
RETRY_MARKERS = ("RATE_LIMIT", "rate limit", "RateLimit", "status\":429", "[1302]",
                 "already owned by an active write handle", "Overloaded", "overloaded_error")
RETRY_BACKOFF = (30, 60)      # 第一次重试退避 30s，第二次 60s


class DshAgent(BaseInstalledAgent):
    capabilities = AgentCapabilities(base_url=True)
    MODEL_CONNECTION = ModelConnectionSpec(
        default_provider="deepseek",
        agent_provider_access=ProviderAccess(
            api_key_candidate_names=["DEEPSEEK_API_KEY", "GLM_BENCH_API_KEY"],
            base_url_candidate_names=["MODEL_BASE_URL", "DSH_MODEL_BASE_URL"],
        ),
        api_key_destination_names={"default": "DSH_MODEL_API_KEY"},
        base_url_destination_names={"default": "DSH_MODEL_BASE_URL"},
        supported_api_formats=[ModelApiFormat.ANTHROPIC_MESSAGES],
        default_api_format_by_provider={"default": ModelApiFormat.ANTHROPIC_MESSAGES},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session_id: str | None = None
        self._manifest: dict | None = None

    @staticmethod
    def name() -> str:
        return "dsh"

    # ---------- 身份 ----------
    def _trial_id(self) -> str:
        cid = getattr(self, "context_id", None)
        if cid:
            return str(cid)
        if not hasattr(self, "_fallback_trial"):
            self._fallback_trial = f"trial-{uuid.uuid4().hex[:12]}"
        return self._fallback_trial

    def _step_id(self, hint: int) -> str:
        parent = Path(getattr(self, "logs_dir", "")).parent.name
        if DAY_RE.match(parent or ""):
            return parent
        return f"day{hint}"

    # ---------- 证据 ----------
    def _evidence_root(self) -> Path:
        base = Path(os.environ.get("BENCH_EVIDENCE_DIR", "/work/evidence"))
        root = base / self._trial_id()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _next_step_index(self) -> int:
        """已记录的天数 + 1；读磁盘而不是记调用次数。"""
        path = self._evidence_root() / "manifest.json"
        if path.exists():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                return len(doc.get("steps") or {}) + 1
            except Exception:
                pass
        return 1

    def _write_manifest(self, entry: dict) -> None:
        root = self._evidence_root()
        path = root / "manifest.json"
        doc = {"protocol": "v2", "trial_id": self._trial_id(), "steps": {}}
        if path.exists():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        doc.setdefault("steps", {}).setdefault(entry["step_id"], {"attempts": []})
        doc["steps"][entry["step_id"]]["attempts"].append(entry)
        doc["steps"][entry["step_id"]]["last_attempt"] = entry["attempt_id"]
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    async def _engine_admin(self, environment, path: str, payload: dict) -> None:
        """在 engine 容器内调用管理接口（凭据只在 engine 进程里，不经过 agent 环境）。

        用 base64 传 Python 源码，避免 JSON 里的双引号被 shell 吃掉。
        """
        code = (
            "import json,os,urllib.request\n"
            f"path={path!r}\n"
            f"payload={payload!r}\n"
            "req=urllib.request.Request('http://127.0.0.1:8080'+path,"
            "data=json.dumps(payload).encode(),"
            "headers={'Content-Type':'application/json',"
            "'X-Admin-Token':os.environ.get('ADMIN_TOKEN','')},method='POST')\n"
            "r=urllib.request.urlopen(req)\n"
            "print(r.status)\n"
        )
        b64 = base64.b64encode(code.encode()).decode()
        cmd = f"echo {b64} | base64 -d | python3 -"
        res = await environment.service_exec(cmd, service="engine")
        rc = getattr(res, "return_code", 0)
        if rc not in (0, None):
            raise RuntimeError(f"engine 管理调用失败: {path} rc={rc} "
                               f"{getattr(res, 'stderr', '')[:200]}")

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_agent(environment, command="dsh --version")

    async def configure(self, environment: BaseEnvironment) -> None:
        await self.exec_as_agent(environment, command="mkdir -p /logs/agent")

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        # 身份
        trial = self._trial_id()
        hint = self._next_step_index()
        step_id = self._step_id(hint)
        day = int(DAY_RE.match(step_id).group(1))
        attempt_id = uuid.uuid4().hex[:12]
        request_id = f"{step_id}-{attempt_id}"

        # 清掉上一步被超时遗弃的 dsh 进程（Harbor 只杀 docker exec 客户端，
        # 容器内进程会继续持有 session.lock，导致本步 "already owned"）
        await self.exec_as_root(
            environment,
            command="pkill -f 'dsh --profile' 2>/dev/null; sleep 1; true",
        )

        # 收权（Harbor 每步会把 /logs/verifier 重置成 777）
        await self.exec_as_root(
            environment,
            command="chmod 700 /logs/verifier 2>/dev/null; chmod 700 /tests 2>/dev/null; true",
        )

        # 开放本步（可信控制端）
        await self._engine_admin(environment, "/admin/open_step",
                                 {"trial_id": trial, "step_id": step_id, "day": day})

        # 模型连接：缺项直接失败
        conn = self.model_connection
        if conn is None:
            raise ValueError("未解析到模型连接")
        missing = [k for k, v in (("model", conn.model_id), ("base_url", conn.execution_base_url),
                                  ("api_key", conn.api_key)) if not v]
        if missing:
            raise ValueError(f"模型连接缺少字段: {', '.join(missing)}")

        tok_path = Path(__file__).resolve().parents[2] / "server" / "day_tokens.json"
        token = ""
        if tok_path.exists():
            token = json.loads(tok_path.read_text(encoding="utf-8")).get(str(day), "")
        if not token:
            raise ValueError(f"缺少第 {day} 天的令牌（{tok_path}）")

        env = {"DSH_MODEL_NAME": conn.model_id,
               "DSH_MODEL_BASE_URL": conn.execution_base_url,
               "DSH_MODEL_API_KEY": conn.api_key,
               "TRIAL_ID": trial, "STEP_ID": step_id, "DAY": str(day),
               "REQUEST_ID": request_id, "TOKEN": token, "ATTEMPT_ID": attempt_id}
        if self._session_id:
            env["DSH_SESSION_ID"] = self._session_id

        ev_dir = self._evidence_root() / "steps" / step_id / "attempts" / attempt_id
        ev_dir.mkdir(parents=True, exist_ok=True)
        stream_path = ev_dir / "stream.jsonl"
        chunks: list[str] = []

        async def on_chunk(text: str, stream) -> None:
            chunks.append(text)
            with stream_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"stream": str(stream), "text": text}, ensure_ascii=False) + "\n")

        b64 = base64.b64encode(instruction.encode()).decode()
        cmd = f"printf %s {b64} | base64 -d > /tmp/instr.txt && sh /app/run_day.sh /tmp/instr.txt"

        started = time.time()
        result = None
        error = None
        retries: list[dict] = []
        try:
            for attempt in range(len(RETRY_BACKOFF) + 1):
                mark = len(chunks)
                try:
                    with environment.scoped_output_callback(on_chunk):
                        result = await self.exec_as_agent(environment, command=cmd, env=env)
                    break
                except Exception:
                    tail = "".join(chunks[mark:])
                    reason = next((m for m in RETRY_MARKERS if m in tail), None)
                    if not reason or attempt >= len(RETRY_BACKOFF):
                        raise
                    wait = RETRY_BACKOFF[attempt]
                    retries.append({"attempt": attempt + 1, "reason": reason, "wait": wait})
                    print(f"[dsh-agent] 可重试错误（{reason}），{wait}s 后重试", flush=True)
                    # 会话锁冲突：last resort 换新会话（丢上下文但能继续）
                    if "already owned" in reason and self._session_id:
                        retries[-1]["session_reset"] = self._session_id
                        self._session_id = None
                        env.pop("DSH_SESSION_ID", None)
                    await asyncio.sleep(wait)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            # 冻结本步（无论成功、报错还是超时）
            try:
                await self._engine_admin(environment, "/admin/freeze", {"day": day})
            except Exception as e:
                print(f"[dsh-agent] freeze 失败: {e}", flush=True)

            raw = "".join(chunks)
            for line in raw.splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "session" and ev.get("sessionId"):
                    self._session_id = ev["sessionId"]
                    break

            # 会话存档：system prompt 与工具 schema 只能从这里恢复
            session_rel = None
            if self._session_id:
                src = (f"/tmp/.dsh/sessions/--app--/{self._session_id}/"
                       "session.v4.jsonl.zstd")
                dst = ev_dir / "session.v4.jsonl.zstd"
                try:
                    await environment.download_file(src, dst)
                    session_rel = str(dst)
                except Exception as e:
                    print(f"[dsh-agent] 会话存档拉取失败: {e}", flush=True)

            ledger_out = ""
            try:
                led = await environment.service_exec(
                    "cat /var/lib/bench/ledger.jsonl 2>/dev/null || true", service="engine")
                ledger_out = getattr(led, "stdout", "") or ""
                if ledger_out.strip():
                    (ev_dir / "ledger.jsonl").write_text(ledger_out, encoding="utf-8")
            except Exception as e:
                print(f"[dsh-agent] ledger 拉取失败: {e}", flush=True)

            entry = {
                "step_id": step_id, "day": day, "attempt_id": attempt_id,
                "request_id": request_id,
                "started_at": started, "ended_at": time.time(),
                "exit_code": getattr(result, "return_code", None),
                "error": error,
                "stream": str(stream_path), "stream_bytes": stream_path.stat().st_size
                if stream_path.exists() else 0,
                "ledger": str(ev_dir / "ledger.jsonl"),
                "session_id": self._session_id,
                "session_log": session_rel,
                "model": conn.model_id,
                "retries": retries,
                # 执行时指令：用于训练样本溯源，避免事后指令漂移
                "instruction": instruction,
                "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            }
            self._write_manifest(entry)

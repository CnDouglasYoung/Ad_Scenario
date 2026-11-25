# 广告预算决策环境 · Ad Budget Decision Environment

**TL;DR (EN)** — A 7-day, ¥900, 4-slot budget allocation environment for evaluating long-horizon
decision agents. Bit-exact replay engine, five auditable criteria, and a four-way failure
attribution taxonomy. Baseline reproduces three checksums; a real agent (DSH) completes the
7-day run in one continuous session. Harbor task included.

**一句话** — 把「模型厂需要可验收的能力增量」做成一个能跑、能判、能复算的七天决策环境：每天分配预算并出价，引擎逐笔回放结算，判据从台账复算。

![架构](docs/assets/architecture.png)

---

## 它解决什么问题

评测长程 Agent 的 benchmark 大多只判两件事：**单步答对没**（AD-Bench）或**最终状态对不对**（ClawMark）。但真实业务里的失败常常是第三种：

> 每一步都对，合起来是错的。

这个环境把「周期 / 预算 / 可追责」三件事补上：

| 维度 | 做法 |
|---|---|
| **周期** | 7 次决策，次日才见结果，历史不可回改 |
| **预算** | ¥900 总额 + ¥150 日上限，超支扣分、超总额判负 |
| **可追责** | 每次提交落到台账，判据从源数据逐笔复算；Agent 只能看聚合报表 |

---

## 关键结果

同一环境、三次运行（无额外事件 / 第 5 天预算下调 / 第 4 天起回报约束）：

| 运行条件 | 规则基线 | Agent 主分 | 相对基线 |
|---|---:|---:|---:|
| 无额外事件 | 613 | **764.0** | +25% |
| 第 5 天预算下调 | 503 | **650.0** | +29% |
| 第 4 天起回报约束 | 613 | **836.0** | +36% |

> 以上为加入「开局公告」机制之前的结果；公告上线后需重跑刷新。

**三个对账点（可复现）**

```
Day 4–7 赢标曝光   561,234
全周累计实付       69,839,567 单位 = ¥698.39567
七天点击           613
```

第二套实现只要口径一致，这三个数必须逐位相同——这是这个仓库最重要的性质。

---

## 快速开始

```bash
# Harbor：跑一次评测（需 docker + 本仓库的 harbor-cli 镜像）
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  -v /usr/bin/docker:/usr/bin/docker:ro \
  -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro \
  -v "$PWD:/work" -w /work -e PYTHONPATH=/work -e DEEPSEEK_API_KEY=... -e SCRIPT=0 \
  --entrypoint harbor harbor-cli:0.27.0 run \
  -p ./harbor_task -a harbor_task.agents.dsh_agent:DshAgent -m deepseek-chat --sm

# 单容器：跑规则基线，约 3 秒，自带对账
docker compose -f docker/compose.yaml run --rm engine --script 0

# 双容器：引擎（无外网）+ Agent（无数据），HTTP 交互
docker compose -f docker/compose.agent.yaml up --abort-on-container-exit

# 真 Agent：DSH 连续会话跑满七天（需要 DEEPSEEK_API_KEY）
SCRIPT=0 docker compose -f docker/compose.agent.yaml run --rm agent \
  python3 dsh_runner.py --days 7
```

无需 GPU，无需联网（除模型调用）。引擎纯 Python 标准库，整数记账。

---

## 架构

```
        front（可出网）
             │
      ┌──────┴──────┐
      │   agent     │  DSH + Python，无 /data
      └──────┬──────┘
             │ back（internal，无外网）
      ┌──────┴──────┐
      │   engine    │  数据只读，持有标签与判分
      └─────────────┘
```

**信任边界是网络边界，不是君子协定**（均已实测）：

```
engine 出网   → URLError ✅
agent 看数据  → ls /data: No such file ✅
七天 sha256   → 7/7 一致 ✅
```

---

## 五条判据

| 判据 | 测什么 | 状态 |
|---|---|---|
| **算得对** | 数字能从源数据复算 | ✅ |
| **对得上** | 报表与结算差额精确 | ✅ |
| **说得通** | 跨日判断不许无声翻转，翻转须引用**真实发生**的变化 | 🚧 设计完成 |
| **认得出** | 必须列出不确定的部分 | 🚧 部分 |
| **花得起** | 预算 / 调用次数上限 | ✅ |

第 3 条是这个仓库的核心想法：**不靠 LLM 当裁判**，而是让引擎验证「你引用的新证据是否真的变了」。

---

## 我们发现了什么

跑起来才发现的两类问题（都已修复）：

| 问题 | 类型 | 修法 |
|---|---|---|
| `day` 字段可空，一次重试吃掉一天 | 评分问题 | 精确匹配，否则 409 |
| 一步内连交多天，绕开「次日可见」 | 评分问题 | 一天一次 + 验证器「恰好 +1」 |

这两条直接对应一套**四类归因**：

| 类型 | 判定依据 | 本仓库实例 |
|---|---|---|
| 数据缺陷 | 源数据自身矛盾 | 埋点断流（有点击零安装） |
| 环境异常 | 基础设施错误而非模型失败 | 容器 HOME 不可写致 dsh 崩溃 |
| 评分问题 | 评测器接受非法提交 | 上面两条 |
| 模型能力不足 | 合规提交但决策差 | 基线只花 77.6% 预算 |

**「不直接照搬 Agent 的结论」是这套设计的出发点**：不做归因，很容易把「评测器太松」记成「Agent 很强」。

---

## 数据质量闸门

分数高不代表轨迹能用。每条轨迹逐日过闸，信号全部可核验：

| 级别 | 信号 | 例子 |
|---|---|---|
| **HIGH** | 协议违规 | 一步提交两次、用别天的令牌 |
| **HIGH** | 令牌猎取 | `grep -R "t1-…\|token" /app /logs /tmp` |
| **HIGH** | 窥探验收 | `find /tests`、读 `/logs/verifier` |
| **MED** | 环境窥探 | `env`、读会话文件、列 `/logs` |
| **MED** | 环境异常 | 工具报错、权限错误 |
| **MED** | 报表矛盾 | `clicks > impressions` |

判定分三档：`clean` 进 SFT / `review` 人工复核 / `exclude` 只做反例。

**实测**：21 个日样本里 **6 个被剔除**，其中一次是 Agent 主动在文件系统里搜令牌——这不是设计出来的失败，是跑出来的。

## 从轨迹到数据

```bash
python3 tools/export_trajectories.py work/harbor-jobs/scriptB   # → sft.jsonl / rl.jsonl
python3 tools/quality_gate.py       work/harbor-jobs/scriptB   # → gate/report.json
python3 tools/promote_sft.py --min-reward 1.2                  # → sft_promoted.jsonl
```

三条产物：`sft_promoted.jsonl`（训练用）、`sft_rejected.jsonl`（含剔除原因，做反例）、`promote_manifest.json`（来源与统计）。

## 现状

| 模块 | 状态 |
|---|---|
| 回放引擎 + 三对账点 | ✅ |
| HTTP 服务 + 双容器隔离 | ✅ |
| 规则基线 / DSH 七天 | ✅ |
| Harbor 任务（7 步 + 适配器） | ✅ 端到端跑通（7/7 步） |
| Agent 在三种条件下的对照 | ✅ 见上表 |
| 审计归档（思考 + 工具调用） | ✅ `work/audit/` |
| 轨迹导出（SFT / RL） | ✅ `tools/export_trajectories.py` |
| 质量闸门（作弊识别） | ✅ `tools/quality_gate.py` |
| 择优转 SFT | ✅ `tools/promote_sft.py` |
| 多模型跑批 | 🟡 脚本就绪，缺第二个模型凭证 |
| 重复实验（≥3 次） | ⬜ 已观测 ±10% 方差 |
| 「说得通」判据 | ⬜ 设计完成 |
| SFT / RL 轨迹导出 | ⬜ |

**不做**：不训练模型、不做供应商谈判、不做 UI 平台——这些是岗位边界，不是取舍。

---

## 仓库结构

```
engine/      回放、评分、基线、6 个测试
server/      HTTP：state / report / plan / score / reset
agent/       DSH 七天循环、规则与 LLM 策略
harbor_task/ Harbor 任务（7 步）与自定义 agent 适配器
tools/       导出 / 质量闸门 / 择优入库
docs/        状态与架构图
work/        台账、调用链、看板
```

---

## 参考

- **DeepSWE** — 失败归因：区分模型能力失败与评测设施伪影
- **Terminal-Bench 2.0** — 任务 / 环境 / 评分三件套
- **GDPval** — 交付物导向的评测
- **RSIBench-Data / RSIGym** — 隔离原则、审计项、预算即判据
- **Harbor** — 任务格式与运行协议
- 数据：iPinYou 第二期（2013-06-06 ~ 06-12）

---

## License

MIT

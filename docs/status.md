# 设计 vs 实现对照

判断标准：**有没有可复算的证据**。✅ 有 / 🚧 部分 / ⬜ 无

## 判据层

| 模块 | 设计意图 | 状态 | 缺什么 |
|---|---|---|---|
| 算得对 | 数字能从源数据复算 | ✅ | — |
| 对得上 | 报表与结算差额精确 | ✅ | — |
| 花得起 | 预算/日上限，超支扣分、超总预算判负 | ✅ | — |
| **说得通** | 翻转须给理由，且理由引用真实变化；编造理由重扣 | ⬜ | claims schema、历史存储、冲突检查、证据核验 |
| 认得出 | 必须列出不确定项 | 🚧 | verifier 尚未强制 |

## 评分层

| 模块 | 状态 | 说明 |
|---|---|---|
| 主分公式 | ✅ | 点击 − 日超限罚分 + B 曝光加分 |
| 三条件归一化 | ✅ | 主分 ÷ 该条件规则基线（613 / 503 / 613），reward 用 `multi_step_reward_strategy = "final"` |
| day7 判分脚本 | ✅ | 未知条件直接判失败；异常退出由 trap 写 0 |
| 提前终止的语义 | 🚧 | 提前终止时 `final` 取到的不是 day7，需要统一出口 |

## 运行与隔离

| 模块 | 状态 | 说明 |
|---|---|---|
| Harbor 七步运行 | ✅ | 三条件各 7/7 步 |
| agent 非 root | ✅ | uid 1000；探针确认 |
| `/tests`、`/logs/verifier` 不可读 | ✅ | 每步 agent 前 root 收权；探针 `DENIED` |
| verifier 防伪造 | ✅ | 先删 reward + trap 兜底写 0 |
| 未来日不可见 | ✅ | `/report?day=N` 超前进度返回 403 |
| 管理接口鉴权 | ✅ | `/reset`、`/audit/*` 需 `X-Admin-Token` |
| 提交账本 | ✅ | 事件级账本（step_open/plan/committed/rejected），落盘 fsync；宿主从 engine 容器内取快照 |
| 步骤闸门 | ✅ | 只有可信适配器调 `open_step` 开放的当天才接受提交；day1–6 `min_reward=1.0`，协议失败即中止 |
| 证据采集 | ✅ | 宿主侧流式收集到 `evidence/`；不再以 root 回写 agent 可写路径 |
| 并发提交互斥 | ✅ | `/plan` 全流程在同一把锁内，拒绝路径也记账 |

## 数据生产线

| 模块 | 状态 | 说明 |
|---|---|---|
| 多模型 | ✅ | GLM-5.3-Flash 已打通；模型由 Harbor 解析经 adapter 注入（`-m` 生效） |
| 事件级导出 | ✅ | `events.jsonl` 保留思考与工具调用 |
| SFT / RL 导出 | ✅ | `sft.jsonl` / `rl.jsonl`，条件只写在 `meta/run.json` |
| 质量闸门 | ✅ | 168 个日样本：clean 165 / review 18 / exclude 1；假称提交与证据损坏都有规则 |
| 择优入库 | 🚧 | 严格规则下 promoted 0 / rejected 16：**卡在执行上下文未采齐（system prompt、工具定义）** |
| 重复实验 | ✅ | 2 模型 × 3 条件 × 3 次 = 18 次完整七天；极差 0.30–1.25 |
| 运行留档 | ✅ | `work/archive_run.sh` 成功失败都归档 |

## 第二套 Benchmark（AuctionNet）

| 模块 | 状态 |
|---|---|
| 源码与来源核验 | ✅ |
| 独立运行环境 | ✅ |
| 市场启动验证 | ✅ |
| 三广告池业务层 | ⬜ |
| 七天业务层 / Harbor task | ⬜ |

## 明确不做

- 训练模型（岗位边界）
- 供应商开发与商务谈判
- UI 平台

## 数据保留

- 运行证据（`evidence/`）与归档**只增不删**；退役用 `work/retire_run.sh <名字> <原因>`。
- `evidence/` 在仓库根目录，**不会挂进 agent 容器**（Harbor 只挂 trial 下的 agent/verifier/artifacts 目录）。
- 旧数据缺 manifest 的，标注 `legacy_unverified`。

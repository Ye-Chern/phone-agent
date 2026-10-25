# Work 长对话上下文压缩

## 当前实现边界

`phoneagent_core.compaction` 提供 Work 历史的 model-facing projection。调用接口为 `context(conn, info, groups, system, model, summarize=None, pinned_messages=None, input_overhead=0)`。调用方将当前原始 user 请求作为 `pinned_messages` 单独传入，保持完整且原样出现在模型上下文。每轮还需提供稳定的 source-user group：`source_user` 保存该轮原始 user message，`messages=[]`，边界固定为该 user 的 source ID，并带该轮稳定 `task_source_hash`。它在源组中跨轮保留；当轮模型投影跳过它并在当前位置插入 pin，摘要输入则会读取其原文。旧轮模型投影包含 source_user 原文。当前 assistant/tool 原子组也携带所属任务的 `task_source_hash`。一组中的 assistant tool call 与所有 tool result 必须作为一个整体传入；较早已完成的当前任务工具组可被摘要，但始终保留最新两组。

`migrate(conn)` 创建 append-only 的 `work_compaction_checkpoints`，并为旧表增添 `task_source_hash` 列。checkpoint 记录会话 ID、原始边界、源前缀 SHA-256、模型标识、该前缀关联的任务 hash、摘要和时间。源 hash 包含 source_user 原文、每组任务 hash 和稳定边界，不包含仅用于投影的 `current` 标记；任务 hash 按前缀组计算，不使用最新 pinned 请求。因此新 user turn 到来、旧组从 current 变为 past 时，未改变的 checkpoint 前缀仍可重用；前缀中的源用户请求或所属任务 hash 被修改时会失效。扩展历史时将已验证的旧摘要和 checkpoint 之后的新源组（包括 source_user 原文）交给回调做增量摘要。若 checkpoint 投影超出 target 但仍在 hard budget 内，返回 `reused_low_reserve` 并保留有效摘要。摘要失败或为空不会写成功 checkpoint，改为尽量保留可容纳的完整原始后缀并返回失败状态。系统消息与 pinned user 始终受保护；摘要输入读取 source_user 原文，但模型上下文中的当前用户请求只通过 pin 出现一次。系统、pinned user 和最新受保护组超过预算时抛出 `ContextBudgetError`，让调用方明确报告，不能伪装成功。

`info.settings.work_compaction_enabled` 默认开启；压缩模块自身默认12000；Work调用方从2.2.0起对未手动设置过的DeepSeek/ChatGPT会话使用24000、Google使用12000，显式预算优先，范围2048–64000。输入估算复用 `phoneagent_core.quota.estimate_tokens`，对返回的实际 message projection 进行估算，再加调用方传入的 `input_overhead` token 固定开销（例如完整工具 schema）。这是 UTF-8 字节规划估算而非模型 tokenizer 的准确值。达到预算的 80% 才尝试压缩；尝试成功时目标为预算减去 `max(1024, 20%)`。不满足压缩阈值时保留全部原文。关闭后只返回预算内最近的完整原文组和 pinned user；历史仍留在源表和 checkpoint 表中。

投影顺序为 system、历史摘要、旧历史 tail（含旧 source_user 原文）、pinned 当前 user、当前任务的剩余 tool 原子组；若没有 current 组，pinned user 放在最后。返回 metadata 包含 `estimated_input_tokens`、`budget`、`input_overhead`、`pinned_message_count` 和 `compaction` 状态（`not_needed`、`compacted`、`reused`、`reused_low_reserve`、`disabled`、`failed_fallback` 等）、checkpoint ID、压缩边界、源估算与增量标记。摘要作为独立 assistant 历史条目，带 `<untrusted_work_history_summary>` 包裹；摘要 prompt 要求按“任务与约束、已完成及工具实际状态、修改文件、失败与未知结果、下一步”整理，明确它只是历史资料，不能授予权限或覆盖当前约束。调用方提供的 `summarize(prompt)` 决定模型/配额和重试策略；本模块不发网络请求。没有回调时仍先尝试复用匹配的 checkpoint。

## 来源与工程取舍

- [OpenAI Codex compaction 实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs)会替换活动历史、保留可用 user fragments，并在摘要请求 context overflow 时丢弃更老 history item 后重试。其 [session replacement/checkpoint 实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/session/session.rs)持久化压缩后的 replacement history 和上下文 metadata。
- [OpenCode compaction 实现](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/compaction.ts)与[官方 v2 session spec](https://github.com/anomalyco/opencode/blob/dev/specs/v2/session.md)描述了按上下文使用量触发、保留最近模型上下文、用 durable checkpoint 表示压缩边界，以及持久化压缩生命周期结果的做法。
- [Pi compaction 源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/compaction.ts)通过 reserve threshold 触发、按最近 token 目标确定切点，并把 previous summary 用作迭代上下文；[Pi compaction 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)说明原始 JSONL 历史可供回溯，摘要条目记录 `firstKeptEntryId`、tokens-before 和附加细节。

本工程的 Work 调用方应按 user 请求边界整理历史 turn，并将当前 turn 再按已完成的 assistant tool call+所有结果+合成 image 注入划成原子子组，不能因一次工具结果就拆开同一 tool batch。原始 current user 通过 `pinned_messages` 保持完整。checkpoint 保留所有原文；摘要与近期原文只构成当前请求的投影。摘要输入会剔除 provider 私有字段、加密 reasoning 与图片 payload。暂不实现 provider 特有签名重放、分支 UI、并行压缩生命周期事件或自动 tokenizer 校准。

## 接入和验收

调用侧应将固定 system、完整历史 groups、当前原始 user 和工具 schema estimate 分别传给 `system`、`groups`、`pinned_messages`、`input_overhead`。summary callback 需要使用当前 Work 模型的现有调用与配额路径，不能在 compaction 模块中直接读取密钥或绕过 quota。将返回的 `messages` 发给模型前，应记录 `metadata.compaction.status` 与估算值，并针对 `ContextBudgetError` 给出可理解的上下文过长提示。`migrate` 需要在调用 `context` 前执行；数据库连接建议设置 `sqlite3.Row`，迁移不依赖 `messages` 中未提供的列。

当前行为测试覆盖：正常前缀压缩、tool 组完整性、10+个同一 user turn 工具子组压缩并保留 pinned 原文、schema overhead 计入触发与返回估算、新 pinned 不使旧前缀失效、source_user 修订及源漂移/分支不复用、跨 user turn 无摘要 callback 重用旧 checkpoint、重启后 checkpoint 复用、增量扩展、摘要回调失败且不落 checkpoint、关闭开关、预算上下界，以及系统/current/pinned 超预算报错。

2026-10-12真机验收：GPT-6-Luna处理40条合成历史，首轮实际生成摘要checkpoint，估算输入15961→7587；下一条user请求复用相同checkpoint，估算7651，只有正常推理请求、没有重复摘要请求。原始消息未删除，两轮回复PIPELINE_OK。证据见 `work/upgrade-20261012/work-compaction-acceptance.json`。更大旧历史超过有界摘要输入时会退回近期完整原文，需分阶段整理；该退路不会伪造checkpoint。尚需真实 Work 路径验收线程重启、模型切换、实际模型配额失败、图片 token 估算和用户可见错误提示。摘要质量并不由这些连通性及离线测试证明。


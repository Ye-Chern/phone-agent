# PhoneAgent 2.0 升级与验收

## 2.0.1：Work 自动批准

用户明确要求不再逐项审批，因此本机开启 `auto_approve_work=true`，并增加原生和网页版“工具权限”开关。此设置覆盖所有 Work 会话和模型，持久化到私有 runtime.json；新安装默认 false，不因为加载 Skill 或模型文字要求而自动开启。工具执行时重新读取设置，关闭后下一项操作恢复人工批准，已执行的命令需另点停止。Chat 的工具禁用、文件路径/密钥保护和删除项目根目录的禁令保留。命令仍拥有 Termux 已有权限，工作目录不是 shell 沙箱。自动批准的具体工具及参数以 `auto-used` 状态记入审批表；此前拒绝的同一操作在15分钟内仍拒绝。待审批的旧任务不会自行执行，重试后使用当前设置。

开关 API 是 `GET/POST /api/approval-policy`，POST 只接受严格 boolean。修改配置时原子替换并保留已有配额等字段，损坏的配置不会被覆盖，现有 Host/Origin 约束同样适用。新增自动执行、撤销、Chat/路径/明确拒绝边界、配置与 HTTP 验证；共49项 Python测试通过，Web DOM检查覆盖开关启停。2.0.0 验收与失败记录继续保留。

2.0.1 已在手机编译并更新，原生开关读取为开启；实际 Termux 执行链路无需审批完成 `printf AUTO_APPROVE_OK` 并生成自动审批审计记录（`auto-approval-device-execution.json`）。这不是模型调用验证。当前 DeepSeek 合成模型验收失败于连接被拒绝（`auto-approval-acceptance.json`）；只读网络诊断显示 DeepSeek 和 Google 域名解析为198.18网段、TCP连接均被拒绝，未修改用户VPN或DNS，也没有隐藏或冒充模型成功。2.0.0 的早先真实API成功证据仍有效，但不代表当前网络正常。安装/源码一致性和37个原会话保留见 `deployment-check-2.0.1.json`。用户在升级前又产生的会话也未删除；模型验收前52个会话全部保留。

日期：2026-10-11。目标环境为已通过 USB 连接的小米 Android 手机和 Termux，开发目录为 `D:\work\phone-agent`。本阶段交付可运行的双模式应用与外置记忆 MVP，同时留下能够重跑的实验和失败证据。

## 工程审计与可回退基线

原工程是 Python 标准库 HTTP/SSE 服务、SQLite 持久化、原生 Java Activity 与 PWA。Android 客户端通过 localhost 8756 连接 Termux；启动沿用已有 RUN_COMMAND 授权。模型请求为 DeepSeek 官方接口，工具回复、reasoning_content、图片注入和失败任务恢复已有协议。基线 18 项测试通过。

本地目录没有 Git，不能创建真实开发分支或声称已提交。修改前的源码快照在 `work/upgrade-20261011/original/`，没有复制私人密钥、用户 DB 或日志。手机部署另在 Termux 私有 `~/.phoneagent/backups/update-*/` 保存代码、配置和 SQLite 一致性快照；原生构建备份在 `~/.phoneagent-app/backups/`。旧手机会话基线为 37 个；没有删掉或重新发送这些会话。

原 Markdown 问题在两端自制的渲染器，模型传输中原文没有丢失；网页版的简易替换不能处理完整 GFM。采用本地打包的 marked、DOMPurify、highlight.js、KaTeX 替换；120ms 节流重绘增量内容，未闭合代码围栏仍可显示。原生保留 TextView/Spannable，增加标题、强调、列表、任务项、引用、链接、围栏和等宽表格；完整数学、语法高亮使用同一后端的网页版。

原生“会话与工具”已改为按模式显示的分区面板：顶部双列常用操作、当前会话改名/导出、模式内搜索、独立滚动的历史列表及当前项高亮。长标题裁切和按钮换行已在真机修正；搜索 Markdown 实际只留下匹配会话。Work 蓝色、Chat 紫色，默认 Chat 使用 Gemma 26B。截图：[原生面板](../work/upgrade-20261011/native-chat-panel.png)、[原生 Markdown](../work/upgrade-20261011/native-chat.png)、[网页版 Markdown](../work/upgrade-20261011/web-markdown.png)。原生导出仍是分享文本；完整可再导入的 JSON 在网页版提供。

## 与代码一致的架构

继续复用 `agent.py` 的 HTTP/SSE、消息和 run 数据、工具注册、日志、停止与重试。不建立第二套服务，Google 原生 REST 流转成原 Harness 的事件。

- `phoneagent_core/storage.py`：增量数据库迁移与统一 Session；旧记录默认 Work，mode 不可原地改写，settings 按会话独立保存。
- `policy.py`：会话目录解析、符号链接边界、保护配置目录、一次性具体参数批准。Chat 在执行层拒绝工具，即使上游意外返回 functionCall。
- `skills.py`：全局和项目 Skill、描述目录、简单版本/依赖诊断、参考文件边界。用 `load_skill` 延迟加载指令，避免把所有内容塞入上下文。
- `providers.py`：模型注册、Google 原生 generateContent/流与 embedContent。保留原生 parts 和 thoughtSignature，包括文字或空 part 上的签名；回传工具时按原顺序保存，避免破坏 Gemini 的协议。
- `quota.py`：SQLite 事务保留请求额度，持久化实际 usage/未知用量/429 冷却，太平洋日期。单个请求的多帧累计 usage 用最后值替换，再汇总多个物理请求。
- `memory.py`：候选识别、来源验证、修订关系、FTS5/词法/CJK 双字/余弦混合检索、视角和实体筛选、有限上下文编排。原始档案继续保留在 messages。

工具由既有 TOOLS/TOOL_IMPL 注册，新模型由 REGISTRY 注册，Skill 由目录发现。Work 有工具和项目上下文；Chat 不传 tools，使用固定设定、当前场景、相关长期记忆及完整最近轮次。切换恢复独立会话，两个模式的草稿与模型选择分开。异步原生切换有代次校验与输入锁，避免请求显示到另一个会话。

## Android 文件与权限决策

服务运行于 Termux 身份，只能使用 Termux 已有权限。共享存储依赖 Android 对 Termux 的授权；如果尚未授权，需要用户在 Termux 执行 `termux-setup-storage` 并在系统提示中选择。此次没有给 ADB shell 或应用增加权限。

工作目录必须是可访问的具体项目子目录，例如 `/data/data/com.termux/files/home/projects/demo` 或 `/sdcard/Download/demo`。根目录、Termux 主目录、共享存储根和私有配置目录被拒绝。相对路径基于该项目目录；绝对路径也必须位于其内；resolve 后再次检查边界，因此符号链接不能带文件工具越界。目录失效/权限撤销会返回明确错误。

SAF 的 `content://` 不是 POSIX 路径；当前明确拒绝它，没有假装实现 DocumentsProvider 桥。访问只能由 SAF 暴露的资料需后续 Android 桥接并单独保存授权 URI。共享存储与 Termux 私有目录也不是等价环境，不在那里构建 Python虚拟环境或依赖 Unix 文件权限。

目录不是 shell 沙箱。每条命令都需要批准完整 command 与 cwd；命令获批后拥有 Termux 本身已有权限。因此不能承诺恶意 shell 被目录边界完全阻止。删除、覆盖现有文件、CxxDroid 导出也等待批准。批准与工具名及参数哈希绑定，15 分钟内一次有效；拒绝的同一操作不能立即反复请求。断电期间已经 started 的操作仍按“结果未知”处理，禁止盲重跑。

资料依据：[Termux 文件布局](https://github.com/termux/termux-packages/wiki/Termux-file-system-layout)、[执行环境](https://github.com/termux/termux-packages/wiki/Termux-execution-environment)、[Android 11 存储限制](https://developer.android.com/about/versions/11/privacy/storage)。

## Google 模型、密钥与配额

指定的 Windows Google 路径是目录，其中指定文本文件为单行密钥。只读取该文件，没有扫描邻近其他密钥。用一次性 USB localhost 通道在内存中传输到 Termux 私有 `gemini.key`，文件模式 600；共享目录、源码和命令参数没有密钥明文。密钥不在 APK 或网页中，客户端只调用本机服务。

手机请求官方模型目录返回了用户要求的全部五个 ID，并进行了合成请求。两种 Gemma 用于 Chat，两种 Flash 用于 Work，Embedding 2 只用于检索。DeepSeek 现有模型配置和别名继续使用。目录/连通证据在 `work/upgrade-20261011/google-models.json` 与 `live-acceptance-initial.json`；最终工具验收在 `live-final-tools.json`。Flash 最终复测遇到上游503（有限自动重试后返回可重试失败），随后一次明确重试成功完成 list_files 和回复，用户消息数保持1；证据见 `live-flash-retry.json`。没有隐去服务失败。

[官方模型文档](https://ai.google.dev/gemini-api/docs/models)与[托管 Gemma 说明](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)作为 ID/能力依据。Embedding 2 请求使用官方任务前缀与 `outputDimensionality=768`，不使用旧模型的 taskType；每条文本单独一个向量，不把多条记忆聚合成一个向量。向量存模型 ID 与维度，拒绝跨模型空间比较。[Embedding 文档](https://ai.google.dev/gemini-api/docs/embeddings)

没有取得已登录 AI Studio 的项目额度读数，也没有修改计费设置。用户提供的 RPD/RPM/TPM 是初始规划值；本地日上限采用更保守值，Flash 留 2 次保留额度。覆盖设置从私有 runtime.json 的 quotas 对象读取。实际项目与其他应用共用额度，面板不能代表 Google 真正剩余量。每天按 America/Los_Angeles 午夜重置，支持夏令时，测试覆盖冬夏边界。[官方配额说明](https://ai.google.dev/gemini-api/docs/rate-limits)

每次物理请求（含有限重试）先事务预留。Google 网络/服务失败最多额外重试一次，400/403/429 不自动重试；429 保存冷却，用户重试同样受限额检查。无法确认是否到达上游的失败仍计数，宁愿保守。Embedding 响应没有被使用的实际 usage 时标为未知，不能按估算冒充实际 token。

实测发现 Google SSE 含重复累计 usage；初始 Harness 把帧重复相加。已修为一请求一记录，并保留初始错误报告用于追溯。requests 表的最后实际 usage 原先没有此重复问题。另通过审查修正了 SQL 占位、图片 base64 预算、坏工具 JSON 及完整签名保存；单 DB 文件锁阻止不同端口的第二服务恢复活跃任务。

## 记忆 schema 与写入选择

条目有稳定 id、conv_id、kind、content、source_id、entity、epistemic、validity、occurred、recorded、revises、audience、slot、vector、vector_model。kind 覆盖事实、声称、信念、偏好、承诺、事件、计划、状态；epistemic 为 confirmed/claimed/believed/unverified；有效性为 active/superseded/retracted/expired。只有明确人工确认能设 confirmed。

固定身份只由设置操作更改；聊天不能自动写回。自动候选仅读用户明确的记住、偏好、承诺与更正线索，逐条来源去重，不额外调用摘要模型。助手虚构的内容不会自动进入世界正典。人工纠正新增版本、令旧版本失效；撤销保留记录和原始来源。当前没有复杂模型抽取、自动事实裁决或批量整理：在低配额阶段优先可审计的规则写入，保留未来抽取接口所需的 schema。C3 的模型抽取、批量整理和语义冲突裁决是后续研究项。

检索只注入 active 条目；支持 audience、明确实体和词法+FTS5+可选向量。唯一明确提及的已知角色用于实体过滤；多人问题不强制一个实体。被标记为其他受众的来源，其最近完整用户/助手轮次也从模型上下文排除，原始档案仍保留。没有标签的信息与后续转述不能获得严格信息流隔离；用户需要正确设置受众，不能宣称模型永远不会串角色。

最近轮次保持成组，当前输入不静默截断。默认估算预算 5000，可设 512–6000；长期记忆最多 1200 或预算的四分之一。超过预算返回可处理的错误。估算基于 UTF-8 字节及图片规划值，真实 tokens 以后端 usage 为准。关键词默认本地；逐条建立索引、每轮云端语义查询均为主动设置，检索失败退回本地且不阻断正常聊天。

Chat 记忆目前严格会话隔离，不自动跨会话共享；用户可导入历史建立独立副本。跨会话世界正典需要显式 world/group scope、人物可知信息及授权，不能为了“记得更多”混入其他会话或 Work 文件。

## 实验与已经观察到的失败

`scripts/evaluate_memory.py` 固定种子运行 100/200 轮合成原文，对照最近窗口、逐轮抽取式摘要、无结构词法检索、生产分层检索。B 逐轮更新但不是模型语义摘要；C 离线阶段未跑真实向量。D 用人工正确标签的夹具，因此不是自动抽取端到端成绩。预算一致，问题不包含标准答案提示；输出包括逐题覆盖、禁止证据、纠错、延迟、预算和抽取漏项。

当前常规支持题覆盖 A=0，B=0.9231，C=0.7692，D=0.8462。B 每规模带入 8 条禁止证据、14 次新旧过敏并存；C 有 4 条禁止证据；D 在这一小题组没有观察到禁止证据与纠错冲突。D 仍漏同义问法，512 预算构造失败一次，多实体问法带入无关信息。规则候选只提取 11 条目标中的 3 条。不能把召回提高解释为文学质量提高，不能将人工标签下的效果外推到自动写入。

真机另使用三个独立的 200 轮合成 Chat：Gemma 记忆开启回答修订后的“龙井茶”，关闭回答“不知道”，768 维 Embedding 的语义问法也回答“龙井茶”。各组早期原文已在最近预算外；真实输入 usage 分别 448/415/454，估算上下文不超过 1200。该小样本说明连接、检索和生成链路可工作，不证明 200 轮自然互动或文学质量。初始语义测试虽然召回正确，模型仍答“不知道”，也保留在 `final-device-acceptance-initial.json`；独立会话的复测避免前一组回答混入对照。

盲评材料 30 题上下文、匿名臂顺序及双人评分协议已生成，尚未进行真实四臂文学盲评。模型辅助摘要、带真实向量的全面四臂比较、自然度/未知编造/延迟成本的统计评估，需要后续固定模型批次和人工评审。详细定义及失败均见 [MEMORY_EXPERIMENT.md](MEMORY_EXPERIMENT.md)。

两个参考 ZIP 在当前附件及工程入口未找到。durable-task 参考已安装版本的契约、检查点与恢复设计；没有猜测 novel-framework 的具体文件或实现。如后续提供 ZIP，可继续比对事件与角色认知协议。

## 构建、自动检查与运行恢复

本轮离线 Python 测试 46 项通过，覆盖原工具/恢复协议、Chat 执行层拒绝、路径/符号链接、审批一次性、来源/修订/秘密、上下文和图片预算、非法/原子导入、HTTP Host/Origin、busy、配额并发/持久化/429与冬夏日期、Google native parts，以及实验失败探针。Web DOM 验收加载真实脚本，测试两模式模型/草稿隔离、状态轮询不能解锁发送、GFM/代码/数学与危险 HTML/链接过滤。

Termux 完成 Java→DEX→APK 签名，`adb install -r` 成功，APK versionCode=200/versionName=2.0.0；生成包在 `app/PhoneAgent-2.0.0.apk`。最终手机源码与部署包校验一致，APK 与本地交付包哈希一致，数据库完整性检查为 ok，原有 37 个会话全部保留、损失 0。构建与合成实测证据位于 `work/upgrade-20261011/`，见 `device-build.txt`、`deployment-check.json`。当前手机原有权限和签名沿用，无需重新授权。

升级脚本在活跃任务时拒绝更新，部署前保留一致性备份；没有自动替换损坏 DB 的策略，损坏返回错误保留原文件。恢复代码可从最近私有备份复制 agent.py、phoneagent_core、web 和启动脚本，再安装对应旧 APK；保留现行 agent.db，不将旧快照直接覆盖。2.0 的增量列不会使新后端丢失旧记录；1.x 创建记录的 SQL 未适配增量列，因此需要旧版时应使用兼容新 schema 的修复版，或只读导出后另建隔离数据目录，不能贸然让 1.x 写当前数据库。

剩余实际运行限制：小米后台冻结 Termux仍可能断连；本轮验证服务重启，没有为了验收重启整台私人手机。需要系统后台设置时由用户决定。服务默认监听 loopback、限制 Host 与修改请求 Origin；不提供远程账号/多用户认证，不应直接向互联网暴露。

免费 API 可能用于产品改进；本轮全部云端素材为合成测试，仅 Work 验收列出部署包目录，没有发送私人小说或密钥。未来对原创内容应先明确发送范围，并核对[Google 数据使用条款](https://ai.google.dev/gemini-api/terms)。

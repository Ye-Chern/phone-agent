# PhoneAgent 2.2

2.2.0 提供可分享的APK、自带运行包、Termux首次设置引导和“API与连接”配置页；他人填写自己的API或登录自己的ChatGPT。见[首次安装与分享说明](docs/SHARING.md)。本版修复了图片上下文误估，以及允许外部访问时未选目录仍挡工具的问题。

Termux 上的 Python 标准库服务，供原生 Android 应用和网页版共用。Work 用项目目录和工具完成任务；Chat 用 Gemma 与本地记忆继续对话。原有 DeepSeek、图片、搜索、工具时间线、中断和重试协议保留。

## 手机上开始使用

在 Termux 运行 `bash ~/.phoneagent/start.sh`，然后打开 PhoneAgent。顶部 Work / Chat 切换会恢复该模式的会话。旧会话自动归为 Work；切换保留记录和草稿。

- Work：在“会话与工具 → 工作目录”填写具体项目目录，选模型后发消息。文件操作限制在该目录；默认命令、删除、覆盖和 CxxDroid 导出等待具体操作批准，批准后点击“重试本轮”。

2.0.1 新增“会话与工具 → 工具权限 → Work 自动批准（所有工作会话）”。开启后上述操作直接执行，无需逐项点击；设置保存在 Termux 私有 `runtime.json` 的 `auto_approve_work`，重启和新建 Work 会话仍生效，所有 Work 模型共用。Chat 仍不执行工具。关闭后下一项操作恢复手动审批；正在执行的命令不会被关闭开关中断，请使用“停止”。自动操作仍留审计记录，文件工具保留目录/密钥边界，命令使用 Termux 已有权限。此前明确拒绝的同一操作在15分钟有效期内仍被阻止；已有等待任务需要重试本轮，不会自行启动。
- Chat：默认 Gemma 4 26B A4B，也可选 31B。菜单可设角色、场景、视角、记忆开关，查看来源和纠正记忆。
- “完整网页版”打开 `http://127.0.0.1:8756/web/`，提供完整 Markdown、代码高亮、公式、JSON 导入导出和显式语义索引。原生端支持常用 Markdown、等宽表格和代码背景；公式与完整语法高亮使用网页版。
- 配额面板显示本机请求记录和估算剩余。模型实际可用额度以 Google 项目为准。

## 模型和私有配置

现有 `~/.phoneagent/secrets.json` 中的 DeepSeek / Exa 设置继续有效。Google 密钥独立保存在 Termux 私有目录 `~/.phoneagent/gemini.key`（文件权限 600），不要放到 Download、源码或导出数据中。

Google 注册表包含 `gemma-4-26b-a4b-it`、`gemma-4-31b-it`、`gemini-3.5-flash-lite`、`gemini-3.8-flash`，检索向量模型为 `gemini-embedding-2`。2026-10-11 已在连接手机上核对实际模型目录并用合成素材调用；见 [升级与验收报告](docs/UPGRADE.md)。

环境变量支持 `PHONEAGENT_CONFIG_DIR`、`PHONEAGENT_GEMINI_KEY_FILE` 或 `PHONEAGENT_GEMINI_KEY`；DeepSeek 仍支持 `PHONEAGENT_DEEPSEEK_KEY`、`PHONEAGENT_MODEL`、`PHONEAGENT_MAX_TOKENS`、`PHONEAGENT_REASONING_EFFORT`。不要把真实值写进命令历史。Windows 的默认数据目录与 Termux 分开；PC 无网络时可以离线测试、管理本地会话。

配额覆盖写入私有 `~/.phoneagent/runtime.json` 的 `quotas` 对象（此文件只放运行设置，不放密钥），例如：

```json
{"quotas":{"gemini-3.8-flash":{"local_rpd":10,"reserve":2,"rpm":3,"tpm":12000}}}
```

这里只是本地配置示例。默认 Flash 日上限 20、预留 2；Gemma 日上限 100、预留 2；Flash-Lite 日上限 100、预留 2；Embedding 日上限 100、预留 5。成功、失败和实际重试均保守计入请求次数；429 持久化冷却并停止自动重试。Google RPD 按美国太平洋午夜重置；输入/输出用量优先使用 API 返回值。额度是按项目共享的，本机不能看到其他客户端消耗。[Google 配额文档](https://ai.google.dev/gemini-api/docs/rate-limits)

## 记忆与隐私

固定身份、当前场景、最近原文、结构化长期记忆和完整原始档案分别保存。自动写入只接受用户明确表达的记忆候选，保留为声称；模型的回答不会自动变成确认事实。人工纠正保留旧条目及修订关系。记忆开关只控制检索/候选提取，不删除原始历史。

关键词检索默认离线。为单条记忆建立语义索引，以及启用每轮云端语义查询，都需要主动操作；Embedding 失败会退回关键词。生成回复时仍会向所选云端模型发送固定设定、场景、有限最近原文和必要记忆。没有上传私人小说目录或原始资料库。Google 免费服务的数据使用规则应按[现行条款](https://ai.google.dev/gemini-api/terms)理解。

`agent.db` 保存消息、任务、记忆和配额；诊断日志只记录标识、状态和错误类别，轮转保存，不写密钥、用户正文或工具参数。完整 Chat 导出包含历史与记忆，应当作私人数据保管。导入创建新 Chat，会重新映射来源和修订关系，清除工具权限、云端索引和语义自动查询；非法条目整体回滚。

## Skills

全局：`~/.phoneagent/skills/<name>/SKILL.md`；项目：`<项目>/.skills/<name>/SKILL.md`。同名项目 Skill 覆盖全局条目。安装附带的只读 `project-review` 示例；现有同名 Skill 不会被更新覆盖。UI 可查看与禁用，模型先看描述，再通过 `load_skill` 加载入口和参考文件。

入口支持简易单行 frontmatter：`name`、`description`、`version`、`dependencies: [name1, name2]`。参考路径仅在该 Skill 目录内，单文件不超过 64KB。依赖缺失会拒绝加载；第三方指令不授予任何工具权限。示例见 [project-review](examples/skills/project-review/SKILL.md)。

## 更新、回退和验证

2.1.2 按ChatGPT账户模型目录的 `input_modalities` 开放图片输入，GPT-6-Luna可在Chat和Work用输入框旁的🖼按钮添加图片。真实识图由用户自行试用，本补丁未发送图片推理验收请求。

2.1 的 ChatGPT 套餐授权、Work自动压缩、思考强度、顶部折叠和显示限速见 [CHATGPT_PLAN.md](docs/CHATGPT_PLAN.md)。授权依赖在Windows执行 `python -m pip install -r requirements-phoneagent-auth.txt`；Termux优先 `pkg install python-cryptography`，再安装PyJWT。依赖缺失会禁用授权入口并明确提示，原DeepSeek/Google功能仍可使用。Work压缩预算默认12000个估算输入tokens，可设2048–64000；它不需要Embedding。顶部模型的思考选择按实际支持档位保存到当前会话。

USB 更新包必须包含 `agent.py`、`phoneagent_core/`、`web/`（含 vendor）、`start.sh`、`watchdog.sh`、`deploy_update.sh` 和可选 `examples/`。复制到手机 Download 下独立目录，在 Termux 执行其中的 `deploy_update.sh`。运行中任务会阻止更新；私有备份包含代码、配置及 SQLite 一致性快照。源码校验后重启，并检查版本。

原生 APK 用 `app/build.sh` 在现有 Termux 构建目录编译，保留原签名，电脑用 `adb install -r` 更新。回退时恢复备份代码及旧 APK，保留当前数据库；不要用旧数据库覆盖新产生的消息。完整操作与限制见 [UPGRADE.md](docs/UPGRADE.md)。

```powershell
python -m unittest discover -s tests -v
python scripts/evaluate_memory.py
```

离线测试不调用云端。Web DOM 验收需要 Node 和测试用 jsdom：`npm install --prefix work/upgrade-20261011/tooling jsdom@30.1.2`，再执行 `node scripts/test_web.cjs`。运行时网页依赖已本地打包，无需 npm/CDN。

100/200 轮实验、失败样例和人工盲评协议见 [MEMORY_EXPERIMENT.md](docs/MEMORY_EXPERIMENT.md)。它主要测检索与上下文，不能当作文学质量结论。

## 从源码构建

本仓库不含任何密钥、数据库或构建产物。原生 APK 构建分两步：先在 PC 上运行 `python scripts/build_backend_asset.py`，用白名单从源码生成 `app/assets/backend.zip`（自动排除 secrets.json、agent.db、密钥等私有文件）；再把工程复制到手机，在 Termux 按 [app/build.sh](app/build.sh) 打包（需自备 `android-30.jar` 构建依赖，见脚本说明）。发布前可用 `scripts/audit_share_package.py` 复核产物不含凭据。直接使用请下载 Release 中的 APK。

## License

MIT，见 [LICENSE](LICENSE)。网页端第三方库（KaTeX、marked、DOMPurify、highlight.js 等）以 vendor 目录内各自 LICENSE 为准。

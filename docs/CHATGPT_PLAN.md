# ChatGPT 套餐接入与本地 Harness 升级

2.1.2 补丁：图片能力改为读取账户模型目录的 `input_modalities`，包含 `image` 就开放两端图片入口。用户账户的GPT-6-Luna及其他当前列出的ChatGPT模型均包含text/image；不再将其一律设为不支持图片。[GPT-6-Luna官方模型说明](https://developers.openai.com/api/docs/models/gpt-6-luna)确认图片输入能力；Responses适配器已把图片转为 `input_image`，格式参考[Images and vision](https://developers.openai.com/api/docs/guides/images-vision)。用户明确要求自己试用：本补丁没有发送真实图片推理验收请求，不能宣称实际识图已验证。

本补丁新增的目录能力回归检查通过；整套84项离线检查中83项通过，一项既有HTTP跨来源检查在Windows遭遇WinError10053连接中断，复跑仍中断。记录此验证限制，不宣称本补丁全套通过；2.1.1的83项通过记录是此前版本的结果。

2026-10-12，PhoneAgent 2.1.1 已在连接手机构建、安装，版本代码211。用户本人已在手机完成 OAuth 和套餐授权；GPT-6-Luna 的 Chat 及 Work 真实请求通过公共 Responses API。Chat 输入122/output11；Work一次 list_files 后完成，两次物理请求合计 input2407/output46（这只是合成连通性验收，不是模型质量评价）。资料在 `work/upgrade-20261012/chatgpt-acceptance.json`，没有复制本机 Codex 授权或改 API 计费。

## 官方支持的接入范围

[官方接入概览](https://developers.openai.com/siwc/token-sharing-open-source)说明开源及本地托管工具可经用户同意使用 ChatGPT 套餐。[首次授权流程](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)提供动态 public client 注册、PKCE、state、nonce及loopback回调；商业网站登录是不同流程，本项目没有因此公开发布或改源码许可。账户身份登录与 `chatgpt.tokens.use.direct` 套餐权限分别检查，不会读取 ChatGPT 已有聊天或个人记忆。

在“会话与工具 → ChatGPT 账户”点击 **Continue with ChatGPT**，由系统浏览器打开官方授权页面；手机回到 `127.0.0.1:8756/auth/callback`。电脑通过USB转发时回调端口可用18756，同样回到手机本地服务。只有 JWT 完成 JWKS签名、issuer/audience/expiry/nonce验证，且正确授权后，才允许套餐推理。账户/工作区注册以已验证subject和issued clientID隔离；同邮箱不合并注册。token及稳定hostID只保存在 Termux私有 `.phoneagent_auth/`，文件0600、目录0700；没有前端令牌输入框。刷新与凭据轮换一起原子落盘；退出尝试撤销，并清本地令牌。

已有任务运行时拒绝登录、回调激活、切换和退出账号；切换与run admission在同一锁内提交，生成记录绑定请求开始时的账号，不能误标或在另一个账号重放加密原生项。同一身份的令牌续期保留模型目录缓存，避免已入场的模型在配额检查时消失；权限撤回、退出仍清目录。

## 模型、工具和思考强度

模型来自账户实际目录，不把客户端列表当作推理资格证明。用户当前目录列出GPT-6.1-Sol、GPT-6-Astra、GPT-6-Sol、GPT-6-Luna以及GPT-5.6系列；每次授权/切换可以刷新。根据 [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)，实际推理才确定本次请求可用。

套餐走 `api.openai.com/v1/responses`，强制 `store=false`、`stream=true`，每次带本地编排的完整input；不用ChatGPT的backend-api或previous_response_id。本地函数工具位于PhoneAgent namespace，工具结果保持call_id；完整原生reasoning/encrypted项只在同模型及同账号重放。必须收到 `response.completed`才标成功，EOF、incomplete和流中套餐额度错误不会假装成功。[预览限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)中禁止的max_output_tokens、temperature、托管tool_search等字段均未加入。没有API Key付费兜底。

思考强度是会话设置，实际目录的supported_reasoning_levels与公开Responses参数取交集，显示可用档位并写入请求的 `reasoning.effort`。GPT-6-Luna目录默认medium，支持low/medium/high/xhigh/max；DeepSeek保留low/high/max。目录的ultra属于不同编排能力，当前公共Responses参数没有相同保证，因此未冒充这个选择生效。暂未实现Google的thinkingConfig控制，不显示一个无效开关。

选中ChatGPT模型会显示 **Using ChatGPT plan**，用量入口是[ChatGPT usage设置](https://chatgpt.com/settings/usage)。套餐限制属于账户，不能根据本机统计虚构真实剩余额度；耗尽时不给无限重试。

## Work压缩与Embedding

本次实现标准Harness压缩：保存原始档案，按输入估算预算触发，用当前模型对旧的已完成协议组做摘要，并保留当前原始请求和最新工具组。工具schema计入输入估算；摘要请求无工具，同一物理请求的usage不重复累计。输出留量是本地规划，不是厂商tokenizer或总窗口硬保证。checkpoint带原始消息边界、hash、模型、来源任务，源变化会失效；失败退回预算内原文，超大受保护组报明确错误。完整细节及Codex/OpenCode/Pi源码依据见 [WORK_CONTEXT.md](WORK_CONTEXT.md)。

压缩不需要Embedding。Embedding适合语义回查旧事件/项目片段，应是补充层。此次仅读取指定SiliconFlow单行密钥，对`BAAI/bge-m3`发送一条无私人资料的合成文本，真实返回1024维、14个输入tokens；没有把它变成压缩依赖，也未改现有Gemini记忆向量空间。其免费价格尚未独立核对。[SiliconFlow嵌入接口](https://docs.siliconflow.cn/docs/api/embeddings-post)说明bge-m3输入上限8192 tokens，dimensions可调整参数仅适用于Qwen3系列。未来如果采用bge-m3，应分模型/维度建立索引，不能混用已有Gemini768维向量。

本次新增`search_work_history`，只在当前Work原始档案查消息证据，返回稳定消息编号和片段，Chat不能调用。它让模型在摘要遗漏时回查来源，先用本地关键词，不额外调用Embedding。后续可测试“文件版本变化令相关任务结论失效”“决定/计划/工具结果独立记录”“按查询重新展开证据”，这些仍是研究方向，不宣称已实现。

## 显示与权限

接收、完整后端落盘和界面显示解耦。Java在接收线程合并增量，避免每个delta都产生UI Runnable；两端按Unicode codepoint默认120字符/秒释放，约150ms更新Markdown，保留工具事件顺序，接收结束后继续排队显示。可主动“显示全部”，切换会话丢弃过期视图但不丢服务器原文。压测模拟10002字符全部释放、83.55秒、556次Markdown更新，emoji有效、队列归零；这是模拟时钟与功能测试，不是GPU帧率实测。

顶部可折叠，设置在本机记住；原生会话导航改为左侧全高侧栏，目录/账号等具体表单仍可用小窗口。菜单中仍能访问模型/思考/账户/上下文/权限。

手机已检查真实侧栏、思考强度选项、选择high后保存及顶部折叠后重新打开仍保持。选择high的同一合成Work会话通过一次真实请求，回复REASONING_OK，input3301/output20。截图和结果见 `work/upgrade-20261012/native-sidebar.png`、`native-header-folded.png`、`native-reasoning.png`、`reasoning-acceptance.json`。

按用户授权，Work自动批准与工作区外文件访问均开启，也提供单独关闭接口。`GET/POST /api/approval-policy`保存`auto_approve_work`和`allow_outside_workspace`，POST只更新给定布尔字段，不重置另一个开关。打开外部范围后文件工具可访问Termux已有权限路径，`.env`和证书不再按后缀一律拒绝；应用自身私有凭据路径及已知令牌文件仍有文件工具保护。命令本来就能使用Termux权限，保护不是OS沙箱，不声称命令无法绕过它。没有扩大Android系统权限。Chat工具禁用继续保留。

## 验收边界

用户登录和上述真实ChatGPT请求已经通过。另在40条合成Work历史上验证：第一轮将15961个估算输入tokens压至7587，生成checkpoint；第二轮复用同一checkpoint，估算7651，无第二次摘要调用。两轮均回复PIPELINE_OK，原始历史保留；资料在 `work/upgrade-20261012/work-compaction-acceptance.json`。首个更大合成样本超过摘要输入上限，正确退回原文后又暴露令牌续期清目录的错误；已修复后用预算内样本重验，没有把失败算成功。

83项Python测试和Web实际脚本检查通过；JWT刷新、异常回调、模型协议、压缩源漂移/失败/跨轮次、权限撤销、UI队列由离线测试覆盖。原37个会话全部保留，数据库完整性检查通过。大规模自然对话、真实模型长期摘要质量、手机GPU帧率、全部账户/工作区/系统版本兼容性尚未全面测量。全部云端验收均为合成素材，没有发送私人小说、读取其他私人密钥或公开发布。

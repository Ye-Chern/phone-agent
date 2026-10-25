# 分享 PhoneAgent 2.2.0

可以直接把 `PhoneAgent-2.2.0.apk` 发给朋友。APK 内含运行源码和网页资源，不含你的 API 密钥、ChatGPT 授权、会话、工作文件或实验日志。对方的服务和配置都保存在自己的手机上。

## 朋友首次使用

1. 安装 APK，再安装并打开一次 Termux。应用引导提供[Termux 官方发布页](https://github.com/termux/termux-app/releases)。已装 Termux 时沿用原来的发行渠道，不必卸载现有环境。
2. 按应用“首次设置”提示，允许 PhoneAgent 在 Termux 环境中运行命令，并复制初始化命令到 Termux 执行。它会开启 Termux 外部应用调用并安装 Python、加密库和下载工具；首次需要网络，不能只有 APK 而没有 Termux。
3. 返回 PhoneAgent，点“安装 / 修复服务”，等待连接成功。应用会把 APK 自带的运行包通过本机回环连接安装到 Termux 私有目录，无需搬源码、电脑、ADB 或你的密钥。
4. 打开 **☰ → API 与连接**，填写自己的 DeepSeek 或 Gemini API Key；Exa 是可选搜索服务。也可以不填 API，直接从 **ChatGPT 账户** 点 Continue with ChatGPT，在官方浏览器中登录并授权自己的套餐，再刷新模型。
5. API 密钥留空会保留已保存值；要删除时勾选对应清除项再保存。应用不会显示保存过的密钥。配置好后选择模型、创建自己的会话即可。

两项 Termux 调用许可来自[官方 RUN_COMMAND 文档](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent)。共享存储权限仅在需要访问手机文件时另由 Termux 申请；只用其私有目录不需要为 PhoneAgent 扩大文件权限。系统若冻结 Termux，可按引导调整其电池设置。ChatGPT 套餐资格与额度取决于对方账户，PhoneAgent 不附送模型额度。

Work 自动批准及工作区外访问由每位用户在“工具权限”控制。全新安装默认关闭，现有用户升级保留原值。开启外部访问而未指定项目时，以 Termux 主目录作为相对路径及命令的默认基准；仍可选具体项目目录。对方的默认策略不会继承本机已开启的设置。

## 图片输入与预算

图片可能是带 base64 的 JSON 字符串。应用会识别其中图片，不把编码当成几十万普通文本tokens；仍保留原图输入和完整工具协议组。每图预留2048个估算tokens，实际用量以模型返回为准。未手动设预算的DeepSeek/ChatGPT Work默认24000，Google仍12000；显式预算优先，范围2048–64000。过大的当前组会用中文说明预算，不会无限增大。

分享包提供运行源码，排除签名私钥。APK中的公钥证书是安装所需的公开信息。构建先运行 `python scripts/build_backend_asset.py` 生成白名单资产，再在Termux用 `app/build.sh` 打包。不要把 `.phoneagent`、数据库、日志或密钥塞进资产。

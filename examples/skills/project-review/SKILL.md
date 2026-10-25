---
name: project-review
description: 用户要求了解或审查当前项目时，先检查目录和说明，提出有证据的改进。
version: 1.0.0
dependencies: []
---

先使用 list_files 查看用户选定的项目目录；再读取项目说明及与任务直接有关的源码。
阅读 references/checklist.md 获取审查要点。避免扫描整个存储、私人资料和密钥。
指出问题时提供文件与具体触发条件；区分已验证的事实和待验证的推测。
用户要求修改时再修改。工具权限由 Harness 执行；本 Skill 不授予任何额外权限。


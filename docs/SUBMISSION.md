# 提交说明

目标仓库为 <https://github.com/Awon-111/agent>，文件路径以 `main` 分支为基准。压缩包使用 `agent-main/` 作为统一顶层名称；不包含任何作者电脑上的目录结构。

## 阅读顺序

1. 根目录 `README.md`：了解功能并运行系统。
2. `docs/骨科规划智能体_实验报告.docx`：查看图文实验报告。
3. `docs/ARCHITECTURE.md` 和 `docs/API.md`：检查实现与接口。
4. `examples/cases.json`、`examples/results.json`：复算报告数据。
5. `docs/TESTING.md`：查看实际测试范围与结果。

## 重新打包

```bash
python -B scripts/check_submission.py
python -B scripts/package_submission.py
```

打包器按 `SUBMISSION_FILES.json` 逐项检查SHA256，包含清单本身并排除Git内部目录。清单之外的运行数据、私有配置和临时资料不会被加入。压缩包与仓库提交文件逐项一致；真实Key、病例运行数据库、上传附件、缓存和日志均不提供。

下载后直接运行 `python -B app.py` 可创建新的运行数据库。需要真实模型时，由使用者根据 `.env.example` 创建私有配置。公开病例参考库及其来源许可随提交提供，可离线进行规则复算和病例检索。

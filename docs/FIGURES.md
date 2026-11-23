# 报告图表定位

本页对应 `main` 分支的 `docs/骨科规划智能体_实验报告.docx`。报告共14幅正文图、12张内容表；封面另有3张信息表。截图使用固定参数的软件样本，不是临床数据集的验证结果。

| 图号 | 仓库素材 | 来源或复现方式 |
| --- | --- | --- |
| 图1 | `docs/assets/mode-workflow.png` | 用户底稿中的处理流程图 |
| 图2 | `docs/assets/ui-case-entry-current.png` | 当前发布源码的真实病例录入截图，报告局部展示 |
| 图3 | `docs/assets/ui-service-status-current.png` | 不含Key的复现环境真实截图 |
| 图4 | `docs/assets/cobb-calculation.png` | `scripts/generate_figures.py`生成的几何示意 |
| 图5 | `docs/assets/spine-sensitivity.png` | 用户底稿的六点复算图，数值与固定输入核对 |
| 图6 | `docs/assets/hip-matching.png` | 用户底稿的髋样本图，数值与固定输入核对 |
| 图7 | `docs/assets/ui-knee-results-current.png` | 当前KNEE-BASE真实结果，报告局部展示 |
| 图8 | `docs/assets/trauma-comparison.png` | 用户底稿的条件对照图，数值与固定输入核对 |
| 图9 | `docs/assets/ui-review-record-current.png` | 固定样本的软件流程复核真实截图 |
| 图10 | `docs/assets/architecture.png` | 内置图像生成工具生成，按源码核对组件关系 |
| 图11 | `docs/assets/evidence-pipeline.png` | `scripts/generate_figures.py`按代码流程绘制 |
| 图12 | `docs/assets/case-distribution.png` | 按`data/evidence/manifest.json`实际数量绘制 |
| 图13 | `docs/assets/report-recovery.png` | 按`agent.py`有限次数纠错逻辑绘制 |
| 图14 | `docs/assets/repository-map.png` | 按`main`分支实际路径绘制 |

架构和完整分析流程另提供 `docs/diagrams/architecture.mmd` 与 `docs/diagrams/analysis-workflow.mmd`。检索及恢复图源也位于 `docs/diagrams/`。报告中的4个原生可编辑公式，其LaTeX源位于 `docs/equations.tex`。

重绘新增图表需要可选依赖 `requirements-docs.txt`；应用后端和自动化测试均只用Python标准库。中文字体可通过环境变量CJK_FONT指定。重绘会改变图片文件时，应重新审核发布清单及对应报告。

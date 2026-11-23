# HTTP接口

实现文件：`main/app.py`。服务默认绑定 `127.0.0.1:8765`，下表保留完整 `/api` 前缀。JSON请求使用 `Content-Type: application/json`。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/agent/config` | 只读脱敏配置、实际模式与连接状态 |
| `POST /api/agent/test` | 发起真实模型连接测试 |
| `POST /api/agent/config` | 固定405，浏览器不能写配置 |
| `GET /api/cases` | 病例及版本摘要 |
| `POST /api/cases` | 校验创建病例，返回201及病例ID |
| `GET /api/cases/{id}` | 病例、历史方案、运行和审计 |
| `POST /api/cases/{id}/analyze` | 开始新分析，返回202与run_id |
| `POST /api/cases/{id}/chat` | 追问，启动新一轮运行 |
| `GET /api/agent/runs/{run_id}` | 轮询运行状态与方案关联 |
| `POST /api/cases/{id}/image` | 上传PNG/JPEG原始二进制 |
| `POST /api/plans/{id}/review` | 提交具体版本的人工复核 |
| `GET /api/plans/{id}/export` | 导出该版本的可打印HTML |
| `GET /api/evidence/status` | 病例参考库数量、来源与许可 |
| `GET /api/evidence/search?scenario=spine&q=侧弯` | 按场景和关键词检索 |

## 创建病例示例

```json
{
  "code": "SPINE-BASE",
  "scenario": "spine",
  "age": 32,
  "sex": "female",
  "measurements": {
    "cobb_angle": 48,
    "flexibility_pct": 55,
    "coronal_balance_mm": 28,
    "apex_region": "thoracic"
  },
  "clinical_summary": "固定参数的软件复算样本，不含真实患者身份信息。"
}
```

运行参数 `mode` 可为 `auto` 或 `local_rules`。有Key且选择auto时使用云端；显式local_rules不请求模型。追问另带 `message`。实际字段和校验以 `app.py` 及测试为准。

状态包括 `running`、`completed` 与 `failed`。失败返回明确原因，调用方应检查状态再读取方案，不能仅凭HTTP请求成功认定报告生成完成。

## 数据约束

年龄必须是1至120的整数；布尔值不作为数字接受。摘要最多3000字。测量值按场景独立校验。附件有类型、大小、签名与路径检查，只允许本地存档，不是模型影像分析接口。

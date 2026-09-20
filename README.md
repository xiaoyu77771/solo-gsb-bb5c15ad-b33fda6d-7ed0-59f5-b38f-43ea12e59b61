# 设备告警聚合服务（Alert Aggregation Service）

基于 FastAPI + SQLite 的告警汇聚服务。客户端上报设备告警事件，服务按 `source`
在可配置的静默间隔内将乱序到达的事件归入同一事件段（segment），并以
`event_id` 幂等去重；可查询事件段详情（起止时间、最高级别、事件数、revision）。

## 一键启动

```bash
docker compose up --build
```

启动后服务地址：**http://localhost:8000**（交互式 API 文档：http://localhost:8000/docs）

该命令会自动完成依赖安装与 SQLite 数据库初始化（建表在服务启动时执行，
数据库文件持久化在名为 `alerts-data` 的卷中）。

## 配置项

均通过环境变量配置，可在 `docker-compose.yml` 的 `environment` 中修改：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SILENCE_INTERVAL_SECONDS` | `300` | 静默间隔（秒）。同一 `source` 相邻事件（按 `occurred_at`）间隔 ≤ 该值时归入同一事件段 |
| `DATABASE_PATH` | `/data/alerts.db`（容器）/ `./data/alerts.db`（本地） | SQLite 数据库文件路径 |
| 端口映射 | `8000:8000` | 在 `docker-compose.yml` 的 `ports` 中调整 |

## API

### 提交事件 `POST /events`

```bash
curl -X POST http://localhost:8000/events \
  -H 'Content-Type: application/json' \
  -d '{
        "source": "pump-01",
        "event_id": "evt-8f31a2",
        "occurred_at": "2026-09-20T08:15:30Z",
        "severity": "error"
      }'
```

- `severity` 取值：`debug` < `info` < `warning`（别名 `warn`）< `error` < `critical`，大小写不敏感。
- `occurred_at` 为 ISO 8601 时间；不带时区时按 UTC 处理。
- 新事件返回 `201`；重复提交（同一 `source` + `event_id`）返回 `200` 且
  `deduplicated: true`，计数、边界、revision 均不变（幂等）。

响应示例：

```json
{
  "segment_id": 1,
  "deduplicated": false,
  "segment": {
    "id": 1,
    "source": "pump-01",
    "start_at": "2026-09-20T08:15:30Z",
    "end_at": "2026-09-20T08:15:30Z",
    "max_severity": "error",
    "event_count": 1,
    "revision": 1
  }
}
```

### 查询事件段详情 `GET /segments/{segment_id}`

```bash
curl http://localhost:8000/segments/1
```

返回 `id`、`source`、`start_at`、`end_at`、`max_severity`、`event_count`、`revision`；
不存在时返回 `404`。

### 列出事件段 `GET /segments?source=pump-01&limit=100`

按 id 倒序列出事件段，可按 `source` 过滤。

### 健康检查 `GET /health`

## 分段与 revision 语义

- 事件按**事件时间**（`occurred_at`）而非到达时间归段，因此乱序/迟到事件
  会被正确归入既有事件段。
- 迟到事件落在某段的静默窗口内（段首/段尾 ± `SILENCE_INTERVAL_SECONDS`）时并入该段，
  可能扩展 `start_at`/`end_at`、提升 `max_severity`。
- 迟到事件同时落在两个段的静默窗口内时，会将两段**合并**为一段（保留较早创建的段 id），
  事件数累加、最高级别取两者较高者。
- 每当事件段被新事件修改（计数、边界、级别变化或合并），`revision` 递增；
  重复提交不会改变任何字段，也不会递增 `revision`。

## 本地开发与测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q          # 运行测试
.venv/bin/uvicorn app.main:app --reload --port 8000
```

## 项目结构

```
app/
  main.py      # FastAPI 路由与生命周期
  service.py   # 分段/合并/去重核心逻辑（单事务）
  db.py        # SQLite 连接、建表、事务
  models.py    # 请求/响应模型
  severity.py  # 级别归一化与比较
  config.py    # 环境变量配置
tests/test_api.py
Dockerfile
docker-compose.yml
```

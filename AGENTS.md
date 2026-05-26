# AGENTS.md

## 全局协作

- 永远用中文回答用户。
- 这是一个本地 EDC 同步管理控制台，优先保证配置安全、操作可验证、前端可用。
- 默认访问入口是 Flask 统一托管的 `http://127.0.0.1:8081/`。

## 配置与安全

- 不要提交真实配置和运行态数据：`config.ini`、`.env`、`edc_extractor/scheduler.db`、前端依赖和构建产物都应保持忽略。
- 不要在回答、日志或前端页面中展示数据库密码。
- 修改数据库相关逻辑时，优先保留幂等、可回滚、可重复执行的路径。
- Docker 镜像发布到 GitHub Release 产物中，不默认依赖外部镜像仓库。

## 后端约定

- 启动方式使用 `python -m edc_extractor.web`。
- 手动同步和自动同步应共用 `edc_task_executions` 执行记录与进度口径。
- 同步大范围数据时保持时间分片、源端聚合、批量写入、连接复用。
- `edc_entities` 是显式映射表，不要把业务维度长期依赖名称解析；主备节点必须使用 `is_backup` 字段表达，名称包含 `backup` 只作为默认识别和旧数据回填规则。

## 前端约定

- 前端是 React + Vite，开发目录为 `frontend/`。
- 界面保持克制、密集、操作型后台风格；不要做营销页或大 hero。
- 同步任务页要能看见任务状态、进度、错误和自动同步配置。

## 验证

- 后端改动后运行 `python -m pytest -q`。
- 前端改动后运行 `npm --prefix frontend run build`。
- 发布和部署相关改动后检查 `.github/workflows/ci.yml`、`.github/workflows/release.yml` 和 `Dockerfile`，必要时运行 `docker build -t edc-extractor:local .`。
- 涉及页面交互时，启动服务后在浏览器检查 `http://127.0.0.1:8081/`。

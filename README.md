# EDC Extractor

EDC 数据同步管理服务。它从源库 `edc_data` 按时间分片读取源端聚合后的 5 分钟流量数据，并写入 `nfa-dashboard` 本地库的 `edc_traffic_5m`。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.ini.example config.ini
python -m edc_extractor.web
```

默认 Web 地址：`http://0.0.0.0:8081`。

## Docker 部署

Release 产物会包含 Docker 镜像归档、示例配置和 compose 示例：

```text
edc-extractor-v0.1.0-linux-amd64.docker.tar.gz
config.ini.example
docker-compose.example.yml
SHA256SUMS.txt
```

在目标机器加载镜像：

```bash
docker load -i edc-extractor-v0.1.0-linux-amd64.docker.tar.gz
```

准备配置：

```bash
cp config.ini.example config.ini
# 编辑 config.ini，填入源库、目标库和调度配置
```

直接运行：

```bash
docker run -d --name edc-extractor \
  --restart unless-stopped \
  -p 127.0.0.1:8081:8081 \
  -e TZ=Asia/Shanghai \
  -e EDC_EXTRACTOR_CONFIG=/app/config.ini \
  -e EDC_SCHEDULER_DB=/app/data/scheduler.db \
  -v "$(pwd)/config.ini:/app/config.ini:ro" \
  -v edc-extractor-data:/app/data \
  edc-extractor:0.1.0
```

也可以使用 `docker-compose.example.yml`，把其中的镜像标签改成当前 release 版本后启动。

## CI 与 Release

GitHub Actions 分为两条流水线：

- `.github/workflows/ci.yml`：PR 和 `main/master` push 时运行后端测试、前端构建和 Docker 构建。
- `.github/workflows/release.yml`：推送 `v*` tag 或手动触发时，构建 Docker 镜像并上传到 GitHub Release 产物。

发布示例：

```bash
git tag v0.1.0
git push origin v0.1.0
```

## 前端控制台

前端使用 React + Vite。生产构建后由 Flask 统一托管，直接访问：

```text
http://127.0.0.1:8081/
```

首次构建前端：

```bash
cd frontend
npm install
npm run build
cd ..
python -m edc_extractor.web
```

开发时可以单独启动 Vite，API 会代理到本地 Flask：

```bash
python -m edc_extractor.web
cd frontend
npm run dev
```

控制台当前支持：

- 健康状态查看。
- 按时间窗口发现源端 EDC 名称。
- EDC 发现页支持名称/SN 搜索、表头排序、同名重复徽标筛选，以及对已配置条目启用/禁用；禁用只停止后续同步，不删除历史事实。
- 标识 `backup` 备份数据。
- 对未配置项确认并写入 `edc_entities`，主备状态会写入 `is_backup` 字段。
- 手动触发同步、配置自动同步并查看执行记录。
- 自动发现未登记的 `(edc_name, sn)`，保存待录入状态并按级别提醒；映射确认后从源端最早时间开始精确补录，补录失败可通过 API 重试。

## 自动同步

`[scheduler]` 配置块会在服务启动时创建默认自动同步任务：

```ini
[scheduler]
default_cron = */10 * * * *
time_window_minutes = 60
delay_minutes = 10
enabled = true
```

默认含义是每 10 分钟执行一次，取“当前时间延迟 10 分钟后”的最近 60 分钟窗口，并把结束时间对齐到 5 分钟边界。启动后也可以在 Web 控制台的“同步任务”页面调整 cron、窗口、延迟和启停状态。

## EDC 录入提醒与历史补录

`[scheduler] discovery_cron` 默认每 5 分钟扫描源端最近 24 小时的数据。未出现在 `edc_entities` 的精确 `(edc_name, sn)` 会进入 `edc_entity_candidates`；确认映射后接口立即返回，历史边界查询和补录在后台执行。飞书提醒按每条 EDC 记录控制，任意连续 24 小时最多提醒一次，不会因每次 5 分钟检查重复告警。补录按 `(edc_name, sn)` 严格匹配，不会把同名不同 SN 的流量串到一起。

飞书通知默认关闭。生产建议使用环境变量注入凭据，不要把应用密钥写入配置文件：

```text
EDC_FEISHU_ENABLED=true
EDC_FEISHU_APP_ID=...
EDC_FEISHU_APP_SECRET=...
EDC_FEISHU_CHAT_ID=oc_...
EDC_FEISHU_MENTION_OPEN_ID=ou_...
EDC_FEISHU_MENTION_NAME=郝金鑫
EDC_FEISHU_START_HOUR=9
EDC_FEISHU_END_HOUR=18
```

也可以配置 `EDC_FEISHU_WEBHOOK_URL` 使用群自定义机器人。录入状态通过 `GET /api/onboarding` 查询；补录失败后可调用 `POST /api/onboarding/<candidate_id>/retry` 重试。

`config.ini`、`.env`、前端依赖、构建产物和运行态数据库已在 `.gitignore` 中忽略，不要提交真实数据库密码。

## 性能策略

- 默认每 6 小时一个时间片，手动补数超过 31 天会被拒绝。
- 源端 SQL 按 `create_time, edc_name, sn` 聚合，只拉取同步结果。
- 同步前加载启用的 `edc_entities`，按 `edc_name` 分批查询，默认每批 200 个。
- 首次读写目标库映射时会自动确保 `edc_entities.is_backup` 存在，并按名称包含 `backup` 的旧数据回填一次，方便后续查询时过滤主/备节点。
- 目标写入使用 `ON DUPLICATE KEY UPDATE`，同一窗口重复执行不会膨胀。
- 启动时检查源表是否存在 `edc_name + create_time` 组合索引；缺失时拒绝大窗口任务。

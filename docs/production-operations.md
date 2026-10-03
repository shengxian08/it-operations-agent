# 正式环境运维

本手册中的命令在相同发布 commit 的仓库根目录执行。使用独立的正式配置，不读取演示 `.env`：

```bash
dc() { docker compose --project-name itops-production --env-file /srv/itops/runtime.env -f compose.prod.yml "$@"; }
```

## 健康、日志与告警

`dc ps` 应显示 API 和 worker 健康，迁移/storage-init 一次性任务成功退出。API readiness 校验依赖，worker 每轮更新 `/app/logs/worker.heartbeat`；超时会使容器 healthcheck 失败。模型任务有超时和租约，重启后的任务由持久化队列恢复；不要把 SSE 断开当作后台任务取消。

应用以 trace_id / run_id 关联 JSON 日志，API/worker 共用 `app_logs`，按 UTC 日期滚动并保留 30 天。容器 stdout 的 json-file 日志限制为 20 MB × 5；Prometheus 数据保留 30 天。调查问题时查安全错误码、trace、节点耗时、队列和依赖状态，不导出密钥、完整输入、检索正文或员工资料。完整诊断文件只保存到受限运维目录。

```bash
dc logs --since 15m api worker
dc exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=3).status)"
```

`infra/monitoring/prometheus.yml` 将规则发送到私有 Alertmanager。`alertmanager.example.yml` 只有无动作样例 receiver，不能算已接通告警。将批准的 webhook/SMTP/on-call 配置放在受限文件，设置 `ALERTMANAGER_CONFIG`；凭据使用受控的 receiver credential 文件。先验证配置，再在 staging 用故障演练确认真实接收者收到通知，并记录发送、收到和确认的时间。API 不可用、5xx、读接口 P95、依赖异常和 worker heartbeat 告警应有明确值班责任人。监控端口不公开，采用 SSH 隧道或受控管理网络访问。未经真实接收验证不得设置 `alerts_passed=true`。

## 加密外部备份

每日备份一次，正式批准的验收目标为 RPO ≤ 24 小时、RTO ≤ 4 小时，以实际演练记录验证。备份会暂停 gateway/API/worker/Keycloak 的写入，包含业务库、Keycloak 库、原始知识文件、Qdrant snapshots、secret 文件、Compose/realm 模板、运行配置（提供 `--env-file` 时）、镜像清单及逐文件 SHA256。Redis 会话不作为恢复源，恢复后员工重新登录，持久化运行/确认/工单以 PostgreSQL 为准。模型缓存可按固定 revision 重新准备。

备份内容含身份和密钥，生产必须 `age` 加密；解密 identity 与备份文件分开保存，优先由另一名获准运维保管。为 rclone 配置企业批准的外部目标和最小写入权限，启用版本/保留锁策略。以下 recipient 和 remote 是要填写的参数：

```bash
umask 077
python scripts/production/ops.py --project itops-production \
  --env-file /srv/itops/runtime.env backup \
  --secret-dir /srv/itops/secrets --output-dir /srv/itops/backups \
  --age-recipient <approved-age-public-recipient> \
  --offsite company-backup:itops/production
```

记录输出的 artifact、SHA256、seconds 和 offsite_copied。校验外部副本大小/校验和并抽样下载解密；成功创建本地文件不足以证明外部备份可恢复。备份暂停直到完成外部复制，安排维护窗口并监测实际停机时间。每次下载Qdrant临时snapshot后在finally删除服务端临时副本，保留归档里的受保护副本；cleanup失败会输出安全告警事件，需要人工跟进，避免夜间备份副本持续占满Qdrant磁盘。脚本失败会尝试重新启动先前运行的写服务；失败日志应立即告警。

## 从空环境恢复

恢复目标必须是独立主机或名称包含 `restore` 的独立 Compose 项目，空数据库、空知识卷、空 Qdrant 数据卷，不覆盖现有生产项目。先取得已校验加密 artifact、受保护的 age identity、备份时镜像/commit、DNS/TLS 配置和批准的告警 receiver。不能从新版本代码随意恢复旧 schema。

先只提取 secret 和配置；这个阶段不启动任何容器：

```bash
python scripts/production/ops.py --project itops-restore restore \
  --artifact /secure-restore/itops-<timestamp>.tar.gz.age \
  --age-identity /secure-restore/age-identity.txt \
  --secret-dir /srv/itops-restore/secrets --prepare-only
```

从 `/srv/itops-restore/restore-config/runtime.env` 建立受限的恢复配置，核对 manifest 中的镜像并使用备份时的 commit，编辑绝对 `SECRET_DIR=/srv/itops-restore/secrets`、接收配置路径和恢复主机的域名/证书安排。恢复配置中的存储路径和模型 endpoint 应经运维核对。归档的 Compose/realm 用于核对版本；从检出的同 commit 仓库运行 Compose，以保持相对 `infra/` 挂载可解析。

```bash
restore_dc() { docker compose --project-name itops-restore --env-file /srv/itops-restore/runtime.env -f compose.prod.yml "$@"; }
restore_dc config --quiet
restore_dc pull
restore_dc up -d --wait --wait-timeout 180 postgres redis qdrant storage-init
python scripts/production/ops.py --project itops-restore \
  --env-file /srv/itops-restore/runtime.env restore \
  --artifact /secure-restore/itops-<timestamp>.tar.gz.age \
  --age-identity /secure-restore/age-identity.txt \
  --secret-dir /srv/itops-restore/secrets
restore_dc --profile setup run --rm model-download
restore_dc up -d --wait --wait-timeout 180
```

恢复脚本校验归档内每个文件、拒绝路径穿越/链接、拒绝非空数据库和知识卷，恢复两个数据库、知识文件与向量快照。不要在数据恢复之前启动 migrate/API/Keycloak，也不要对生产执行 downgrade。基础 PostgreSQL init 脚本只建立角色/空数据库，Alembic 在恢复完成后验证已恢复版本；启动命令中的 migrate 不能替代数据恢复。

切流前确认三角色登录、知识版本与引用、历史对话、草稿重新签名/确认幂等、工单评论和审计、支持队列、管理员账号权限及告警送达。比较业务计数、active revision、向量集合和备份时镜像清单。记录故障开始、最后可恢复数据时间、备份下载/解密/数据库/向量/模型/探针耗时和恢复完成时间；单库 restore 冒烟不等于全系统 RPO/RTO 通过。验证完成后才切换 DNS/流量，原故障现场保留用于调查。

## 发布与回滚

每次发布前取得成功的加密外部备份，记录旧 commit、image IDs/digests、Alembic revision、active knowledge revision，并明确新旧镜像是否兼容当前 schema。`ops.py release` 需要 [验收 evidence](production-acceptance.md)，执行 pull、暂停流量与应用、迁移升级、健康启动。不要更改镜像 tag 内容。

应用异常时优先回退到已证明兼容**当前 schema** 的旧镜像。修改受保护配置中的 `IMAGE_TAG` 为该旧 commit，记录批准原因，再启动旧 API/worker/Web 和网关：

```bash
dc stop gateway api worker
# 由运维将 runtime.env 的 IMAGE_TAG 改为已验证兼容当前 schema 的旧 commit。
dc pull api worker web
dc up -d --no-deps api worker web
dc up -d --no-deps gateway
```

重新验证 readiness、worker heartbeat、登录、问答、工单确认与审计。正式回滚不执行 `alembic downgrade`，也不清空命名卷。如果旧镜像不兼容新 schema，维持维护窗口，采用经过验证的向前修复，或在独立空环境恢复升级前的备份并经业务确认后切流；备份恢复会影响 RPO，需明确数据差额。知识内容回滚使用管理员「激活此版本」，它切换 active revision，与应用镜像/schema 回滚分别验证。

## 留存与计划任务

聊天正文/引用/反馈和运行事件保留 90 天，业务/工具审计保留 180 天，本地加密备份保留 14 天，应用日志保留 30 天。聊天和运行的标识/元数据保留；已发布知识 revision 不由 orphan 清理删除。支持工单和知识原件不通过聊天留存任务自动删除。

maintenance 默认 dry run，先检查输出，再显式 `--apply`。orphan-indexes 只处理任务 manifest 注册且未属于已发布版本或有效运行租约的集合，不清理其他系统的 Qdrant collection。

```bash
dc exec -T worker python -m app.production.maintenance retention
dc exec -T worker python -m app.production.maintenance orphan-indexes
dc exec -T worker python -m app.production.maintenance retention --apply
dc exec -T worker python -m app.production.maintenance orphan-indexes --apply
dc run --rm --no-deps -v /srv/itops/backups:/backups worker \
  python -m app.production.maintenance backups --directory /backups
```

备份清理需要容器 UID10001 对主机备份目录拥有删除权限；在 Linux 上核对 owner/group 并保留目录 0700、文件 0600。先 dry run 后对同一命令加 `--apply`。不递归删除目录，不清理未匹配的文件。外部副本保留由批准的 rclone/object storage 生命周期单独管理。

建议用 root 管理的 cron 包装脚本（0700）设置 `set -euo pipefail`、`cd <检出的仓库>`、上述 `dc` 函数，使用 `flock` 防重入、受限日志文件和失败告警。每天 02:00 运行 backup，成功且外部校验后再清理过期本地备份；每天 03:00 retention；每周日 03:30 orphan-indexes。避免备份、发布和索引维护时间冲突；文件任务用独立 flock，数据库维护已有 advisory/pointer 锁。每月在独立空环境恢复一次，证书到期、磁盘使用和最后成功外部备份时间由主机监控接收者告警。

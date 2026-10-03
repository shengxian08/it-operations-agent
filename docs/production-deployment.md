# 正式环境部署

本指南对应单企业、100 名员工、10 个并发对话和 500 篇知识文档的单 Linux 主机方案。正式入口使用 `compose.prod.yml`；仓库默认 `docker-compose.yml` 保留演示环境。正式 API 为 `/api/v1`，默认静态前端不会构建 DemoApp，生产配置必须保持 `DEMO_ENABLED=false` 和 Secure Cookie。

命令示例在获准管理 Docker 和 `/srv/itops` 目录的运维终端执行，文件权限按 root 管理的受限目录编写。

## 主机与网络

按已批准方案准备 8 vCPU、16 GB 内存、至少 100 GB 可用 SSD 的 Linux 主机，安装 Docker Engine 和 Compose v2、Python 3.11+、`age` 与 `rclone`。这些是验收的起始资源前提，不是已完成的容量承诺；按 [验收流程](production-acceptance.md) 在这一配置上执行 30 分钟测试。worker 限制 4 CPU / 6 GB，数据库、Keycloak、Qdrant 各限制 2 GB，其他服务和主机需要额外余量。磁盘预算包括原始文档、所有保留版本的向量、数据库、模型缓存、日志和 14 天本地加密备份；500 个 20 MB 文件仅原始文件就可能达到 10 GB。代表性大文件、版本保留或备份增长在实测中超过资源余量时，可经批准扩到 24 GB / 200 GB 并重新验收，记录配置变化。

公开入口只有 TCP 443；SSH 仅允许运维网段。PostgreSQL、Redis、Qdrant、Keycloak 管理、Prometheus、Alertmanager 和 OTLP 使用 Compose 私有网络，不发布主机端口。配置企业 DNS 和有效 TLS 证书。网关只开放正常登录路径，阻止外部 Keycloak 管理与 master realm、`/metrics` 和 readiness 路径。单机故障会中断服务，恢复依赖外部备份。

## 固定发布物

CI 使用 `backend/uv.lock` 的 `uv sync --frozen` 与 `frontend/package-lock.json` 的 `npm ci`，执行后端检查、独立数据库迁移与测试、演示/正式 mock 浏览器测试和真实 Keycloak 浏览器测试。全部通过后，构建 API、worker 和正式 Web 三个以完整 Git commit SHA 标记的镜像，保存为带校验和的 artifact。CI 不推送外部仓库，也不自动部署。

下载待发布 commit 的 `immutable-images-<sha>`，校验后导入镜像：

```bash
cd /srv/itops/release-artifacts
sha256sum -c SHA256SUMS
docker load -i itops-<full-commit-sha>.tar.gz
```

镜像清单包含 image ID 和 commit label。运维人员经批准可将这三个镜像推广到企业镜像仓库，保持完整 commit tag 不变并记录 registry digest；`ops.py release` 会 pull 配置中的镜像。工作目录检出相同 commit，保持 `infra`、Compose、迁移和镜像一致。不要以 `latest` 或重复覆盖的 tag 发布。

CI 还从 `/tmp` 工作目录导入 API/worker 的已安装 Python 包，逐文件比对镜像中的源码，并将两个 `*-package-verification.json` 与镜像清单一起保存、校验。部署前对目标镜像复核相同探针；从源码工作目录运行不能替代已安装应用的一致性验证：

```bash
# dc 配置完成后运行；脚本只比对镜像内的源码与安装包，不读取业务或密钥。
dc run --rm --no-deps --workdir /tmp \
  -v "$PWD/scripts/production/verify_image.py:/verify_image.py:ro" api python /verify_image.py
dc run --rm --no-deps --workdir /tmp \
  -v "$PWD/scripts/production/verify_image.py:/verify_image.py:ro" worker python /verify_image.py
```

## 配置、证书与密钥

在受保护目录创建配置，示例不包含可用的生产凭据：

```bash
sudo install -d -m 700 /srv/itops
cp .env.production.example /srv/itops/runtime.env
chmod 600 /srv/itops/runtime.env
```

编辑 `runtime.env`：填写 `ITOPS_API_IMAGE`、`ITOPS_WORKER_IMAGE`、`ITOPS_WEB_IMAGE`、完整 `IMAGE_TAG`；设置 `PUBLIC_BASE_URL=https://<企业域名>` 和同域的 `OIDC_ISSUER=https://<企业域名>/identity/realms/itops`。填写经批准的模型 HTTPS endpoint / 模型名、正数月预算和每百万输入/输出 token 价格，三者使用同一货币。模板中的零预算和示例模型会被生产配置检查拒绝。设置绝对 `SECRET_DIR`、批准的 `ALERTMANAGER_CONFIG` 文件路径。

先提供已有的模型密钥文件和证书，不把密钥放到 shell 命令、Git、镜像或日志里：

```bash
python scripts/production/ops.py bootstrap \
  --secret-dir /srv/itops/secrets \
  --model-key-file /secure-input/approved-model-key \
  --tls-cert /secure-input/fullchain.pem \
  --tls-key /secure-input/privkey.pem
```

bootstrap 要求目标目录为空，不覆盖已有凭据。它生成独立的数据库应用/迁移密码、Redis、OIDC、session、Qdrant 写入/只读密钥。目录权限为 0700，文件为 0444：主机通过父目录限制访问，Docker file-backed secrets 可以由不同容器 UID 读取。所有容器密钥通过 `/run/secrets/*` 读取；正式 API/worker 使用 UID 10001。证书更换后重建 gateway，OIDC/session/数据库密钥轮换要同时更新依赖端并安排重新登录。不要仅修改单侧 secret 文件。

## 模型准备与首次启动

下列命令在仓库根目录执行，`dc` 只指向正式项目和受保护的配置：

```bash
dc() { docker compose --project-name itops-production --env-file /srv/itops/runtime.env -f compose.prod.yml "$@"; }
dc config --quiet
dc pull
dc up -d --wait --wait-timeout 180 postgres redis qdrant storage-init
dc --profile setup run --rm model-download
dc run --rm migrate
dc up -d --wait --wait-timeout 180
```

正式 embedding 固定为 `BAAI/bge-small-zh-v1.5`、revision `7999e1d3359715c523056ef9478215996d62a620`、512 维，模型缓存在命名卷 `model_cache`。准备步骤按固定 revision 下载，正常 worker 设置 `HF_HUB_OFFLINE=1`。模型下载可以在批准的网络窗口执行；不要把 revision 换成 `main`，也不要启动缺少缓存的 worker。验证离线加载时使用 worker 镜像及相同 model_cache，避免把测试用 deterministic embedding 当正式语义检索。

storage-init 为知识、日志和模型目录设置 UID 10001；migrate 使用独立迁移数据库用户运行 Alembic，API 使用仅 DML 权限的账号。首次启动后检查 `dc ps`、API readiness 和 worker heartbeat。在容器中检查 readiness：

```bash
dc exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=3).status)"
dc exec -T worker python -c "import pathlib,time; print(time.time()-pathlib.Path('/app/logs/worker.heartbeat').stat().st_mtime)"
```

## 创建账号与角色

Keycloak 管理口没有公开入口。首次运维使用容器内 CLI，从密钥文件建立短期管理配置；完成后删除配置并改用企业批准的管理账号：

```bash
dc exec keycloak /bin/bash
umask 077
KCADM=/opt/keycloak/bin/kcadm.sh
$KCADM config credentials --server http://localhost:8080/identity \
  --realm master --user bootstrap-admin \
  --password "$(cat /run/secrets/keycloak_admin_password)" --config /tmp/itops-kcadm.config
$KCADM create users -r itops --config /tmp/itops-kcadm.config \
  -s username=employee.example -s enabled=true -s firstName=Employee -s lastName=Example
$KCADM add-roles -r itops --config /tmp/itops-kcadm.config \
  --uusername employee.example --rolename employee
$KCADM set-password -r itops --config /tmp/itops-kcadm.config \
  --username employee.example --temporary
rm -f /tmp/itops-kcadm.config
exit
```

`set-password` 未传入密码参数时由 CLI 隐藏提示输入；不要在 shell history 中写明文密码。首次管理员分配 `admin` realm role，支持人员分配 `support`，普通员工分配 `employee`；每位账号应只有对应业务角色。首次企业登录后账号出现在正式「账号管理」页面。该页面管理本地业务角色和 enabled，乐观版本冲突要求刷新再保存，至少保留一位可用管理员。本地角色覆盖不会被下一次 OIDC 登录还原。新账号、密码、MFA、IdP 生命周期仍由 Keycloak 运维管理；禁用业务账号立即使其会话失效并停止待执行/执行中的任务。

## 批量准备知识资料

正常文档可直接在管理员知识管理上传。批量准备时使用正式导入 CLI，仅接受指定目录里的普通 `.md/.pdf` 文件；目录必须存在且非空，单文件受同样大小限制。指定已有 enabled 管理员的 `/me.id`，先 dry run，再去掉 `--dry-run` 入队：

```bash
dc run --rm --no-deps \
  -v "$PWD/scripts:/app/scripts:ro" -v /secure-input/knowledge:/corpus:ro worker \
  python /app/scripts/ingest_knowledge.py --directory /corpus \
  --actor-id <enabled-admin-user-id> --dry-run
```

输出列出文件、每项 access_level、已有文档或 queued/running/ready job。不指定 `--access-level` 时保留每个 Markdown frontmatter 的 employee/support/admin 权限；PDF 或没有权限元数据的文件默认 employee。只有明确审核过整个目录的统一权限时，才用 `--access-level employee|support|admin` 覆盖，避免将支持人员资料批量降为员工可见。重复准备同内容会复用已有文档/任务。worker 解析后，管理员必须在页面逐个查看 ready job 正文与访问级别并确认发布，发布为 durable index job，等待 completed 才切换知识版本。CLI 不自动发布，不根据空目录/漏挂载删除生产知识，也不允许正式环境使用 `--demo` 或目录同步删除。

## 验证与上线

用三个获准的测试账号检查登录、身份只读、logout、知识权限和账号禁用，再完成管理员上传/预览/发布、员工带引用问答、修改草稿重新确认、支持分派/解决/员工重开以及知识停用/回滚。确认网关 SSE 不缓冲，生产 Cookie 带 Secure/HttpOnly，浏览器没有外部字体请求。

正式流量切换前应具备真实模型评测、30 分钟容量、外部备份恢复、同 schema 镜像回滚和告警送达的证据。`release` 校验六项门禁及精确镜像 tag：

```bash
python scripts/production/ops.py --project itops-production \
  --env-file /srv/itops/runtime.env release \
  --image-tag <full-commit-sha> --evidence /srv/itops/evidence/release.json
```

不要将尚未执行的门禁填为 true。备份、空环境恢复、日常留存和回滚步骤见 [运维手册](production-operations.md)；结果记录要求见 [验收手册](production-acceptance.md)。

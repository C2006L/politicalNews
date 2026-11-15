# 本地服务、快照与恢复

核验日期：2026-11-15。默认主阅读服务 `127.0.0.1:8004`，WeRSS `127.0.0.1:8003`。阅读服务由 `scripts/run_learning_local.ps1` 启动；WeRSS 和快照导出容器由兄弟目录 `werss-local/local-deploy/compose.yaml` 管理。

## 已验证运行版本

镜像与本地补丁摘要保存在 `deploy/local-stack.lock.json`，当前 WeRSS 和快照容器均使用固定镜像摘要，未依赖 `latest`。补丁包含 `weread_mp.py` 与 `webhook.py`，配置及授权仍留在仓库外。只读核对命令：

```powershell
python -m scripts.verify_local_stack --docker docker
```

该脚本只读取指定容器镜像 ID、compose 中的镜像引用和补丁哈希，不读取容器环境变量、Cookie 或口令。更换版本前先停服务备份，在副本验收后再更新锁定记录。

## Windows 不直接读取容器运行中的 WAL 库

本次发现 WeRSS 返回接口鉴权失败，但凭据启用、未过期且散列匹配。进一步检查容器数据库得到 `disk I/O error`。停止服务后已做完整性检查、备份与 WAL checkpoint，恢复服务后原有凭据通过认证，两账号最新入口恢复可访问，没有重新扫码或新建凭据。

为减少 Windows/Linux 同时访问 SQLite WAL/SHM 的冲突，新增 `political-news-werss-snapshot` 容器，每 60 秒在 Linux 中执行 `scripts/werss_snapshot_export.py`。它使用 SQLite backup 获取一致快照，关闭目标并转为 DELETE 日志后原子替换 `learning.snapshot.db`。失败保留上一份，单次导出有时间上限，不暴露半成品。容器不开放端口，也不配置任何 API 密钥。

Windows 主项目只读该独立快照，`WERSS_SOURCE_IS_SNAPSHOT=1`。源路径切换后保守重扫，以来源 ID 和指纹去重；本次 55 篇全部跳过，没有产生重复版本。导入状态页显示快照年龄，超过 180 秒提示过时；快照新鲜不代表上游有新文章，也不证明历史完整。自动整理仍每 15 分钟一批，因此新内容还需要经过“上游取得 → 最长约一分钟导出 → 主项目导入”。

## 启动与停止

在 `werss-local` 目录执行：

```powershell
docker compose -f local-deploy/compose.yaml up -d
```

确认 `learning.snapshot.db` 已生成，再在主项目运行：

```powershell
.\scripts\run_learning_local.ps1
```

停止阅读服务使用启动终端的 Ctrl+C；停止 WeRSS 与快照导出使用：

```powershell
docker compose -f local-deploy/compose.yaml stop
```

不使用 `down -v`，不删除数据目录。该 compose 仍为本机部署，不包含在 GitHub 仓库；镜像身份和导出程序已在主仓库记录。副本、快照及授权文件不提交。

## 维护上游数据前必须停止两个容器

不要在 WeRSS 运行时用 Windows SQLite 对其 `db.db` 写入、checkpoint 或做维护。显式历史导入脚本检测到快照部署时要求 `--source-stopped` 确认；这只表示操作者确认已停服，程序不会代替检查 Docker。停止 WeRSS 及快照容器、先备份再维护，完成后重启，等新快照生成后再导入主项目。普通页面和导入任务都不写上游库。

主库备份：

```powershell
python -m scripts.database_backup test.db D:/your-backups/new-name.db
```

目的文件必须不存在。恢复时先停阅读服务，恢复到新文件并通过 `DATABASE_URL` 指向它，不在线覆盖正在使用的库。主库包含新闻、资料、不可变修订、考点和审核记录；不包含浏览器收藏、已读或复习安排。

浏览器数据另备份：收藏弹窗中的“备份收藏与已读”，以及复习页的“导出复习备份”。恢复均先预览再确认，同编号冲突优先保留本机。换资料库后编号可能不同，不能只凭编号认为原文一致。

## 尚需真实环境验收

一次恢复成功不能代替长期采集观察；最新一篇订阅通道、未知历史、图片和音频覆盖仍有限制。干净 Windows 机器、完整下载落盘与安装分发尚未全部验收。当前无窗口测试中备份 Blob 内容、文件导入、写入失败回滚和重启持久化已验证，但浏览器下载落盘被取消，不能将此项标为完成。继续暂停重新打包。

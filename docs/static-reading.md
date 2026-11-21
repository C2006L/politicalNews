# 静态阅读版：导出、自己上传和更新

本机采集和整理，服务器仅提供静态页面。电脑关机后，服务器继续提供上一次发布的内容。

默认导出近 90 天时政和全部公众号学习资料；原始 HTML、数据库、历史原页、登录凭据和管理接口不进入发布包。公众号文字是否完整仍以原有质量标记为准，导出不会补造缺失内容。首页沿用原来的精选和方向筛选规则，资料库可在浏览器内筛选和检索全文。

服务器使用 Python 3.12 自带的静态文件服务，不安装项目依赖，不新增数据库、Redis 或 Docker 容器。所有上传文件和运行数据限定在 `/home/ubuntu/lcz` 的两个独立子目录。无需 sudo，不修改防火墙或已有服务。

## 1. 在 Windows 本机导出

```powershell
cd D:\politicalNews\political-news
.\scripts\export_static.ps1
```

脚本会打印本次输出目录和预览命令。目录结构：

```text
dist/StaticReading-日期时间/
  site/                         本机可预览的网站
  political-news-upload/        只上传这个文件夹
    site.tar.gz
    static_release.py
    serve_static.py
    install_static.py
```

这个命令不会连接 SSH，不会上传。生成时先通过 SQLite 在线备份取得一致快照，临时数据库留在本机 dist 私有目录并在结束后移除，原数据库不会修改。为生成快照，本机 D 盘需临时预留约 1GB。

导出失败时不要上传该目录，换一个新目录重新生成；脚本不会覆盖已有版本。

## 2. 本机预览

用脚本打印的命令启动，示例：

```powershell
& D:\politicalNews\.conda\python.exe scripts/serve_static.py --directory "D:\politicalNews\political-news\dist\StaticReading-20261118-final\site" --port 8018
```

打开 `http://127.0.0.1:8018/`。检查首页、四个阅读方向、公众号筛选、正文关键词检索、文章详情、收藏及收藏备份。服务默认只监听本机；在运行该命令的窗口按 Ctrl+C 停止。

静态阅读版没有在线采集、人工分类修改、历史版本比较和复习卡接口；原文重点及问答仍可阅读和展开，收藏与已读继续保存在当前浏览器。旧网站收藏属于旧网址的浏览器存储，迁移时先在旧站导出，再在新站导入。

## 3. 首次上传：由用户执行

本次已准备好的目录为 `D:\politicalNews\political-news\dist\StaticReading-20261118-final\political-news-upload`。

在 Windows PowerShell 执行：

```powershell
scp -i "$env:USERPROFILE\.ssh\id_ed25519" -r "D:\politicalNews\political-news\dist\StaticReading-20261118-final\political-news-upload" ubuntu@43.153.144.83:/home/ubuntu/lcz/
```

然后登录服务器：

```powershell
ssh tx
```

在服务器执行：

```bash
python3 /home/ubuntu/lcz/political-news-upload/install_static.py --port 3011
```

安装器会检查端口占用、磁盘余量、文件路径和 SHA-256 校验值，验证完成才切换网站版本。3011 若被占用会停止，不会杀掉其他程序。可用 `--port 3010` 改用另外一个端口；3012 已用于 RunWise，不要覆盖。

成功后访问：

```text
http://43.153.144.83:3011/
```

只有已发布的文件可读取，无目录列表、上传或管理接口。这个入口是 HTTP；手机和微信能否正常打开需要在实际上线后检查，静态化本身不能保证所有网络都能访问。安全组已开放 3011 时一般无需修改；若外网不通，先确认本机服务器响应及系统防火墙，不直接关闭防火墙。

安装器使用独立后台进程，退出 SSH 不会终止阅读站。服务器重启后需要重新执行上面的安装命令启动；本方案不向 `/etc` 写入自启动配置。

## 4. 以后怎么更新

### 手动上传

本机更新采集 → 再运行 `export_static.ps1` → 预览新目录 → 将新输出目录中的 `political-news-upload` 文件夹用相同 scp 命令上传到 `/home/ubuntu/lcz/` → 再运行服务器安装命令。

完整包仅含阅读文件，通常比数据库小很多。后台阅读进程无需重启，已验证的新版本通过 `current` 链接原子切换。

### 可选的一键增量发布

这一步会真正连接并上传服务器，只在你主动执行时运行：

```powershell
.\scripts\publish_static.ps1 -SiteDirectory "D:\politicalNews\political-news\dist\本次输出目录\site" -SshHost tx -Port 3011
```

脚本先读取服务器当前清单，只上传内容哈希变化的文件，再由安装器完整校验。没有变化的文件通过硬链接复用上一版，避免复制整站占空间。使用 SSH 配置中的 `tx` 登录方式；不会上传 `.env`、数据库和密钥。

传输失败或文件校验失败不会切换当前版本。若新页面已经发布但启动服务失败，需要查看报错；这时不能保证一个原本未启动的网站自动变得可访问。

## 5. 状态、停止、回退

都在服务器执行，路径始终位于 lcz：

```bash
python3 /home/ubuntu/lcz/political-news-upload/install_static.py --action status
python3 /home/ubuntu/lcz/political-news-upload/install_static.py --action stop
python3 /home/ubuntu/lcz/political-news-upload/install_static.py --action rollback
```

停止前会核对进程编号、启动标识和脚本路径，不停止其他服务。回退不需要重启阅读进程。

版本保存在 `/home/ubuntu/lcz/political-news-static/releases`，自动保留历史，不自动删除服务器文件。未变的文件共用磁盘内容；多次发布后可查看 `du -sh /home/ubuntu/lcz/political-news-static`，再决定是否清理不再使用的旧版本。不要删除 `current`、`previous` 指向的目录。

## 验收

- 导出清单中的每个文件哈希正确，所有本地文章链接和资源能找到对应文件。
- 首页和文章正文无需项目后端即可打开，筛选、检索只请求静态目录数据。
- 考研教材与带背不因公众号来源而混入公考筛选。
- 服务器无需运行 WeRSS、采集任务或本机数据库。
- 上传失败不替换已发布版本；成功后可以回退上一版。
- 电脑关机后，用另一部设备仍可访问服务器地址；此项在用户上传启动后验收。

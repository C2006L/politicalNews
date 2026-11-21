# 时政自习室

面向**考研政治、公考常识与申论面试**的时政整理和阅读工具。把公开报道、政策文件、官方评论和公众号学习资料分别组织起来，让使用者找到值得读的材料，并能回到原文核对。

当前仓库：[C2006L/politicalNews](https://github.com/C2006L/politicalNews)。项目已在原时政聚合项目基础上持续改造，目前采用“本机采集整理 + 服务器静态阅读”的使用方式。

## 在线阅读

**[打开时政自习室](http://43.153.144.83:3011/)**

- [每日时政](http://43.153.144.83:3011/today/)：综合、考研政治、公考常识和申论面试四种方向。
- [专题资料](http://43.153.144.83:3011/categories/)：政策制度、经济民生、科技教育、国际外交、理论党建等主题。
- [公众号研读](http://43.153.144.83:3011/learning/)：按备考方向、内容类型和公众号来源查找资料。
- [全文检索](http://43.153.144.83:3011/search/)：在已发布文章的标题和保存文字中检索。

这是**静态阅读版**：展示维护者最近一次导出并发布的内容，不会自动跟随本机数据库更新。电脑关机不影响已发布内容的阅读。文章数量以当前发布清单为准，不代表已完整覆盖所有公众号或所有历史文章。

公开入口不包含采集、管理或数据库接口，不提供 `/status`、`/docs` 等本机后台页面。收藏与已读保存在当前浏览器，不跨设备自动同步。当前入口使用 HTTP 和端口，尚未配置域名与 HTTPS。

## 当前功能与边界

| 功能 | 说明 |
| --- | --- |
| 时政初筛 | 政策、报道与评论分别识别；首页最多展示 5 条、同主题最多 2 条，不为凑数量混入背诵和推广 |
| 备考方向 | 考研与公考分别筛选；教材带背与考研提醒不混入公考资料，地域不是统一限制条件 |
| 公众号资料 | 保留已保存文字、重点提法、可展开问答、来源链接和正文状态 |
| 原文与依据 | 摘录和规则提示保留依据；评论观点与政策事实区分展示 |
| 阅读工具 | 桌面及移动端阅读、主题与来源组合筛选、全文检索、浏览器本地收藏与已读 |
| 静态发布 | 一致数据库快照导出、文件哈希校验、手动上传或增量发布、版本切换与回退 |
| 本机维护 | 来源配置、采集诊断、正文质量检查、分类纠正、事件候选核对和复习工具等 |

未实现或不能保证：公众号历史完整采集、图片 OCR、音视频整理、通用语义归并、教师审定考点和命题预测。当前没有接入 AI 总结；文字可读不代表图片、音频及事实已完整核验。WeRSS 授权和来源访问受限时仍可能漏采，不能把“无新增”当作公众号没有更新。

本机维护功能不会因为导出网站而全部搬到公网。静态版保留文章中的重点和问答阅读，复习卡管理、人工审核与历史版本管理仍在本机使用。

## 如何运行与更新

### 1. 本机采集整理

本项目使用 Python 3.13，应用依赖见 `requirements.txt`，开发与测试依赖见 `requirements-dev.txt`。

```powershell
python -m pip install -r requirements.txt
.\scripts\run_learning_local.ps1
```

本机入口为 `http://127.0.0.1:8004/`，公众号研读为 `/learning`。启动脚本按当前工作区布局读取相邻的 WeRSS 配置；公众号采集需要单独准备 WeRSS、确认账号与有效授权，克隆本仓库不会自带登录状态或完整文章库。

详细环境要求、快照与恢复步骤见 [本地运行说明](docs/LOCAL_STACK.md)。私有数据库、文章原始页面、登录凭据和 `.env` 不应提交到 GitHub。

### 2. 导出静态阅读版

```powershell
.\scripts\export_static.ps1
```

默认导出近 90 天时政和全部已保存的公众号资料。脚本完成后输出 `Local preview:` 和 `Upload folder:`；只有分词器初始化提示不代表导出完成。导出会生成一致数据库快照并在结束后移除临时数据库，不修改原库、不自动上传。

输出位于 `dist/StaticReading-日期时间/`：`site` 用于本机预览，`political-news-upload` 是手动上传目录。现有 PowerShell 包装脚本面向当前 Windows 工作区布局；更换目录或 Python 环境时需要调整解释器路径，也可直接运行 `scripts/export_static.py --help` 查看参数。

### 3. 上传并发布

服务器文件限定在 `/home/ubuntu/lcz` 的独立子目录；当前阅读端口为 `3011`。部署使用服务器自带 Python 标准库，不要求运行项目后端、WeRSS、Redis 或新增 Docker 容器。

手动上传新导出的 `political-news-upload` 文件夹后，在服务器运行：

```bash
python3 /home/ubuntu/lcz/political-news-upload/install_static.py --port 3011
```

也可以在预览确认后，主动运行一键增量发布：

```powershell
.\scripts\publish_static.ps1 -SiteDirectory "D:\politicalNews\political-news\dist\StaticReading-实际日期时间\site" -SshHost tx -Port 3011
```

该命令会连接服务器并上传变化文件，校验通过后切换版本。`tx` 是维护者本机 SSH 别名，不是公共账号。其他使用者应使用自己的 SSH 配置和部署路径。

完整上传、状态检查、停止与回退方法见 **[静态阅读版操作说明](docs/static-reading.md)**。服务器目前通过独立后台进程运行，重启服务器后需要按该说明重新启动；不宣称已配置开机自启。云安全组与 Ubuntu 防火墙均需允许所选 TCP 端口。

`deploy.sh`、`server-bootstrap.sh`、Nginx 与 Docker 配置保留用于完整后端的可选部署，不是当前静态阅读站的发布步骤，不应直接用于更新现有阅读站。

## 开发与验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
python -m mypy app/ --ignore-missing-imports
python -m flake8 app/ --select=E9,F63,F7,F82 --show-source
```

普通测试使用临时数据库并禁止外部采集，不依赖私有公众号授权。Linux 发布切换涉及符号链接，Windows 未开放符号链接权限时对应测试会跳过；实际服务器发布仍需验证。

推送 GitHub 会运行 CI 检查，**代码推送不等于阅读网站内容已经更新**。镜像发布需要额外配置；CI 已移除旧的完整后端 SSH 自动部署，当前内容更新以维护者主动导出与发布为准。

## 文档与反馈

- [静态阅读版：导出、上传、更新、回退](docs/static-reading.md)
- [本地服务、WeRSS、快照与恢复](docs/LOCAL_STACK.md)
- [功能实施记录与待办](docs/NON_UI_ROADMAP.md)
- [历史功能与接口技术记录](docs/LOCAL_TECHNICAL_REFERENCE.md)：包含多个开发阶段的数据与限制，非当前部署指南。
- [提交问题或建议](https://github.com/C2006L/politicalNews/issues)

## 内容来源与许可

内容用于学习和信息检索。整理规则不代表官方立场、事实审定或命题预测，完整信息和适用条件请以原站原文、政策文件和指定教材为准。文章文字的权利归原作者或发布方，MIT 许可适用于本仓库代码，不授予第三方文章的再许可。

项目源自 [WangBaoHe333/political-news](https://github.com/WangBaoHe333/political-news)，原作者版权声明保留于 [LICENSE](LICENSE)；本仓库维护与反馈入口为 C2006L/politicalNews。

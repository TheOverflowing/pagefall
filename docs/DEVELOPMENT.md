# 开发说明

安装依赖及构建命令见根目录 README。构建后的 `.app` 记录当前项目路径，移动项目后应重新构建；Chrome 本机连接器也需重新安装。

## 目录

| 目录 | 职责 |
| --- | --- |
| `native/` | SwiftUI/AppKit 界面、双语资源、Vision OCR 与钥匙串工具 |
| `novelist/` | 本机 FastAPI 服务、书库、目录、下载、校对、翻译与导出 |
| `chrome-extension/` | 晋江 Chrome 会话连接扩展 |
| `engine/` | 上游规则适配、晋江正文桥接与离线浏览器测试 |
| `vendor/novel-downloader/` | 固定提交的上游源码与原许可 |
| `scripts/` | 构建、图标生成、Chrome 连接器安装与原生测试 |
| `tests/` | Python 行为测试、原生筛选测试与合成测试素材 |

`engine/generated.ts` 和 `engine/dist/` 由构建生成，不提交。`engine/rules-manifest.json` 保留平台目录；改变规则后运行 `npm run build:engine` 更新它。

## 检查

先完成依赖安装和应用构建，再运行：

```sh
swift tests/make_fixture.swift
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s engine/tests -v
bash scripts/test_native_library_filters.sh
bash scripts/test_native_filter_performance.sh
```

默认测试使用模拟响应、合成页面和自写文本，不调用付费 AI，也不需要真实晋江账号。一个独立前台浏览器会话实验默认跳过，只有显式设置 `NOVELIST_SESSION_TEST=1` 才运行。

原生性能脚本默认使用合成书架；不要提交个人书架快照。浏览器规则离线测试检验解析逻辑，不代表所有站点的线上兼容性。

书架启动直接读取现有 `library.json`、`library-tags.json` 和 `library-classifications.json`，随后接收本机服务更新；连接中断不清空已显示的书架。筛选索引仅在书籍资料变化时重建，已完成分类补齐的作品直接复用缓存。状态轮询中的章节完成判断只查找有效正文字符，不重复清洗全文；真正导出时仍执行完整清洗。

已应用筛选保存到 UserDefaults 的 `library.appliedFilters`，面板草稿取消不覆盖已保存选择。详情接口先返回磁盘缓存，`details_stale` 表示需要后台更新，显式刷新使用 `refresh: true`；更新期间原生详情保持可读。公开目录和元数据使用无会话 Reader，不访问登录钥匙串或 Android Token；正文读取保留原有凭证和权限流程。

章节 `source_updated_at` 取晋江目录“更新时间”列的可见日期，保留页面北京时间，格式为 `YYYY-MM-DD HH:mm:ss`；不采用该单元格提示中的首发或存稿时间。目录缓存使用 `chapter_metadata_version` 标记字段已检查，旧缓存先显示，再通过既有详情后台刷新补齐，未提供的时间显示为破折号。

章节存档位于 `.local/chapter-versions/<book_id>/<chapter_id>.json`。成功取得的正文、手动修改、自动校对及撤销校对分别记录来源；相同正文不重复记录，已有下载和在线缓存首次阅读或查看历史时导入。来源时间使用正文取得时的 `text_source_updated_at`，目录刷新不会改写旧正文的来源时间。`/api/chapter-versions`、`/api/chapter-version` 和 `/api/chapter-diff` 提供版本列表、正文及逐行对比；`/api/chapter-refresh` 显式更新单章，读取失败或锁定不替换本机副本。普通阅读优先使用下载正文和在线缓存，即使后来锁章或目录移除仍可阅读。版本存档不向平台请求历史稿。

自动追更随本机服务启动，`.local/updates.json` 保存开关、目录基线、正文指纹、待重试章和未读标记。范围仅为已有下载/在线缓存和晋江已开启自动续订的作品；续订名单只读 VIP 服务的关注中、未关注两类全部分页，每 10 分钟同步，未完整读取时保留原名单。订阅作品约每 60 秒检查，其他已保存作品每 15 分钟检查；晋江通过无凭证的公网页轻量目录检查更新时间、字数变化，缺少更新时间的来源每日复查正文。只有正文变化才追加来源版本并标为“有修改”，首次监控订阅书不会下载全本，新章会建立正常下载记录。每书正文最多处理 3 章后让出执行，失败保留原文与待重试记录并延后重试。

`GET /api/updates` 返回检查状态及范围；`POST /api/updates/settings` 修改开关，`/api/updates/check` 调度一次立即检查，`/api/chapter-updates/read` 清除单章更新标记。持久 `revision` 仅在正文变化或清标记时递增，供目录刷新使用。手动校对提交 `base_text`，后台已更新时返回冲突而不是覆盖新版。关闭主窗口后保留菜单栏和服务，启用追更时防止 App Nap 延迟检查但允许系统休眠；退出应用、关机和休眠期间不检查。轮询与网络延迟意味着无法保证取得尚未抓取就被锁定或改写的版本。

`scripts/build_keychain_helpers.sh` 会复用源码、编译器及参数未变化的钥匙串助手，保留已有代码身份。助手代码或签名身份变化后，macOS 仍可能要求重新授权；构建过程不修改钥匙串访问控制，也不在普通文件中保存密钥。

## 调试

```sh
.venv/bin/python -m uvicorn novelist.server:app --host 127.0.0.1 --port 8765 --no-access-log
```

请不要在应用已启动同一服务时重复占用该端口。启动日志位于 `~/Library/Application Support/Shiye/backend.log`。

## 贡献

改动应附受影响行为的检查结果。UI 文案保持简洁，中英文资源同步更新。提交时不要加入 `.local/`、`exports/`、个人截图、账号信息、API Key 或浏览器会话；这些路径已在 `.gitignore` 排除。

修改上游适配时保留源码版权声明，并同步 `engine/NOTICES.md` 中的修改记录。不要直接编辑生成的 JavaScript bundle。

## 自包含安装包

在目标架构的 Mac 上安装构建依赖并生成 DMG：

```sh
.venv/bin/python -m pip install -r requirements-build.txt
bash scripts/build_release.sh 0.8.0
```

输出位于 `.build/releases/`，包含 DMG 和 SHA-256 文件。构建脚本只复制运行组件，不读取或打包 `.local`、`exports` 和钥匙串数据。使用 PyInstaller 打包 Python 服务与 Playwright 驱动，Chromium 使用已安装的匹配版本。安装版通过独立资源目录读取组件，通过 `~/Library/Application Support/Pagefall` 保存可写数据。

当前脚本生成本机架构、临时签名的开发发行包；Developer ID 分发签名与公证尚未接入。用户安装说明见 [INSTALL.md](INSTALL.md)。

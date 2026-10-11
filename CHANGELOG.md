# 更新记录 / Changelog

## 未发布 / Unreleased

以下改动已进入最新源码，尚未包含在 v0.8.0 安装包中。

### 新增

- 书架文件夹、列表/封面视图、批量移动及多种排序；晋江收藏分类可导入，本机整理不修改平台收藏。
- 整本下载版本：每轮下载保留正文和译文快照，可查看、阅读、删除，并在导出时选择版本。
- 下载页面支持搜索和状态筛选，进行中的下载也可阅读已保存章节。

- 自动追更：监控已有下载和在线缓存，以及晋江已开启自动续订的作品；保存新章和正文变更，显示未读更新标记，支持立即检查和关闭自动保存。
- 章节历史版本：记录读取正文、手动校对、自动校对及撤销校对的版本，提供历史正文与逐行差异对比。
- 单章更新与更新时间：目录展示平台更新时间，支持手动重新读取，读取失败保留本机副本。
- 关闭主窗口后通过菜单栏继续管理追更。

### 改进

- 晋江目录优先通过公开页面直接解析；App 正文接口无需启动浏览器，复用连接并缩短接口下载间隔，读取失败才进入网页/OCR 回退。
- 支持重新下载和断点继续，记录本轮进度；新正文读取失败时保留旧稿。
- 统一原文/译文导出入口，历史版本使用自己的译文快照，导出文件名区分版本。
- 历史记录支持删除；普通阅读不会重新导入已删除的存档。

- 书架启动先显示本机缓存，连接中断时保留已显示内容；详情先显示缓存再后台刷新。
- 已应用筛选记住选择，取消面板草稿不覆盖之前的筛选。
- 普通阅读优先使用已保存正文，即使后来锁章或目录移除仍可阅读。
- 后台更新与手动校对发生冲突时提示重新读取，避免覆盖新正文。
- 公开目录与元数据读取不访问登录钥匙串；未变化的钥匙串组件在构建时复用。
- 优化状态轮询和正文完成判断，减少重复清洗全文。

The latest source adds bookshelf folders, bulk organization, sorting, whole-book download snapshots, version-aware exports, browser-free Jinjiang API reads with reusable connections, automatic update monitoring, local chapter history and diffs, single-chapter refresh, persistent filters, and faster cached bookshelf/details loading. These changes are not included in the v0.8.0 installer.

## 0.8.0

- 首次提供 Apple Silicon、macOS 14+ 自包含 DMG。
- 内置 Python 服务、Chromium、正文解析器及 Apple Vision OCR，无需开发环境。
- 安装版书库保存在应用支持目录，Chrome 连接器使用内置运行时。
- 提供中英文说明、第三方许可和 SHA-256 校验文件。

First self-contained Apple Silicon macOS installer, including the backend, browser, parsers, and OCR runtime.

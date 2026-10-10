# 安装拾页 · Pagefall

适用于 Apple Silicon（M1/M2/M3/M4 及后续芯片），macOS 14 或更新版本。

1. 打开下载的 DMG。
2. 将“拾页.app”拖入“Applications / 应用程序”。
3. 从应用程序打开拾页，在左下角连接晋江账号，或配置 AI 翻译服务。

本安装包包含 Python 服务、Chromium、正文解析器和 OCR 引擎，不需要安装 Python、Node、Xcode 或下载开发依赖。首次使用平台和 AI 服务仍需联网。

当前版本使用本机临时签名，尚未获得 Developer ID 签名或 Apple 公证。如果 macOS 阻止打开，在确认下载来自本项目 GitHub Release 后，可到“系统设置 → 隐私与安全性”查看应用的打开选项。不要关闭系统安全功能。

书库、截图、译文和导出保存在：
~/Library/Application Support/Pagefall/

晋江凭证和翻译 API Key 保存在 macOS 钥匙串。应用升级不会覆盖书库；删除 .app 不会自动删除书库。

Chrome 连接为可选功能。安装版可直接使用晋江 App 账号连接；如需复用 Chrome 登录，在拾页中安装本机连接器，再按窗口指引加载扩展。扩展复制到应用支持目录，不需要源码或系统 Python。移动应用后请重新安装连接器。

若同时运行源码开发版，请先退出开发版，再打开安装版。两者使用同一本机服务端口，但书库目录独立。

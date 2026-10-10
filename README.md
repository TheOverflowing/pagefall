# 拾页 · Pagefall

简洁的 macOS 小说阅读与整理应用。把书架、在线阅读、后台下载、本地 OCR 校对和 AI 翻译放在同一个原生界面中。

[English](README.en.md) · [开发说明](docs/DEVELOPMENT.md) · [上游与第三方许可](engine/NOTICES.md)

## 功能

- **书架与筛选**：同步晋江收藏，显示封面和账号昵称；按作品种类、视角、标签、发表时间、收藏数等组合筛选，本机缓存支持增量补齐。
- **详情与在线阅读**：查看作者、文案、作品进度、人物关系和章节提要，按需读取免费或已购买章节，无需先下载全本。
- **后台下载**：晋江优先使用已连接的 App 接口，再使用网页解析与字体解码，失败时回退到 Apple Vision OCR；支持暂停、继续和下载队列。
- **本地校对**：整理 OCR 段落、检查重复与疑似缺字，保留原始文本和截图，可撤销自动校对。
- **AI 翻译**：支持 DeepSeek、OpenRouter，自选模型和语言；人名术语表、上下文摘要、分段检查点和续译，原文独立保留。
- **纯净导出**：原文或译文导出为 TXT、Markdown、EPUB，默认不附检查报告。
- **双语界面**：简体中文与 English，可跟随系统。

晋江是主要适配平台；长佩、番茄有独立适配，另外导入了 95 条兼容试用规则。平台页面变化、访问限制或验证可能影响读取，兼容规则不代表每个站点都已实测可用。工具只读取免费或账号已有访问权限的章节，不代为购买。

## 安装与运行

无需配置开发环境的安装包见 [GitHub Releases](https://github.com/TheOverflowing/pagefall/releases/latest)。下载 Apple Silicon DMG，将应用拖入“应用程序”；支持 macOS 14+。当前安装包尚未公证，详见[安装说明](docs/INSTALL.md)。

当前提供源码开发版，需要 **macOS 14+、Python 3.10+、Node.js 18+、Xcode Command Line Tools**。首次准备需要联网。

```sh
git clone https://github.com/TheOverflowing/pagefall.git
cd pagefall
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
npm ci --ignore-scripts --no-audit --no-fund
npm run build:engine
bash scripts/build_app.sh
open 拾页.app
```

构建会生成 SwiftUI 应用、Apple Vision OCR、钥匙串组件和晋江解析器。本机服务监听 `127.0.0.1:8765`。

这是依赖项目目录的开发版，尚未进行 Apple 公证。请保留完整项目目录，不要单独移动 `.app`；移动项目后重新构建。

## 开始使用

1. 打开应用，在左下角账号入口连接晋江。可连接 App 账号，也可通过[Chrome 扩展](chrome-extension/README.md)复用日常 Chrome 的登录。
2. 在 **我的书架** 搜索、筛选或添加作品链接。点击封面或标题查看详情，双击目录章节在线阅读。
3. 选择 **下载整本**，在 **下载** 页面查看进度、暂停或继续。已保存章节可直接导出。
4. 在 **翻译** 选择已保存的书，或粘贴文本创建译稿；配置服务商 API Key 后选择语言，先用首章试译检查效果。

正常下载使用后台浏览器；只有需要登录或验证时才打开相应窗口。退出应用会停止处理，下次可继续已保存进度。

AI 翻译会将所选文本、术语和必要上下文发送给所选服务商，并按其规则收费。术语与结构检查有助于保持一致，译文仍可能出现误译，需要阅读检查。OCR 结果也可能需要人工修正。

## 本机数据

| 位置 | 内容 |
| --- | --- |
| `.local/` | 会话、收藏、书籍正文、截图、在线阅读缓存、译文与检查点 |
| `exports/` | 导出文件 |
| macOS 钥匙串 | 晋江连接凭证、翻译 API Key |
| `~/Library/Application Support/Shiye/backend.log` | 启动诊断日志 |

这些阅读数据、凭证和日志不在仓库中。若应用无法连接本机书库，可在账号页选择 **允许访问本机书库…** 并选中当前项目目录。

## 开发

目录、构建与测试见[开发说明](docs/DEVELOPMENT.md)。欢迎提交问题或改进；提交前请移除账号信息、阅读正文和本机日志中的私有内容。

## 许可与致谢

本项目以 **GNU AGPL-3.0-or-later** 发布，全文见 [LICENSE](LICENSE)。第三方组件保留各自许可。

晋江解析、解密与字体处理，以及兼容规则，改编自 [404-novel-project/novel-downloader](https://github.com/404-novel-project/novel-downloader)，固定提交 `57cef4fc94638009fb1734e9e90feb8ccb7720bb`。对应上游源码、修改说明及第三方许可保留在 [vendor/novel-downloader](vendor/novel-downloader)、[engine/NOTICES.md](engine/NOTICES.md)。

原生界面使用 SwiftUI/AppKit，OCR 使用 Apple Vision，后台浏览器使用 Playwright，Chrome 连接使用 Cookie API 与 Native Messaging。

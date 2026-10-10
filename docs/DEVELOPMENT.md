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

## 调试

```sh
.venv/bin/python -m uvicorn novelist.server:app --host 127.0.0.1 --port 8765 --no-access-log
```

请不要在应用已启动同一服务时重复占用该端口。启动日志位于 `~/Library/Application Support/Shiye/backend.log`。

## 贡献

改动应附受影响行为的检查结果。UI 文案保持简洁，中英文资源同步更新。提交时不要加入 `.local/`、`exports/`、个人截图、账号信息、API Key 或浏览器会话；这些路径已在 `.gitignore` 排除。

修改上游适配时保留源码版权声明，并同步 `engine/NOTICES.md` 中的修改记录。不要直接编辑生成的 JavaScript bundle。

# Pagefall · 拾页

A native macOS app for organizing and reading novels, downloading accessible chapters in the background, correcting OCR, translating with AI, and exporting clean books.

[简体中文](README.md) · [Changelog](CHANGELOG.md) · [Development](docs/DEVELOPMENT.md) · [Third-party notices](engine/NOTICES.md)

## Features

- Jinjiang bookshelf sync, covers, account names, and combined filters for categories, perspective, tags, publication dates, and favorites.
- Book details, character relationships, chapter summaries, and on-demand reading of free or purchased chapters.
- Automatic update checks for locally saved books and existing Jinjiang automatic-renewal subscriptions, with saved revisions and unread markers.
- Local chapter history, line-by-line comparisons, and explicit single-chapter refresh.
- Jinjiang App API first, webpage parsing and font decoding next, Apple Vision OCR as fallback. Pause and resume downloads.
- Local OCR checks with preserved source text and screenshots.
- DeepSeek and OpenRouter translation with configurable models and languages, character glossaries, context summaries, and resumable checkpoints.
- Original or translated TXT, Markdown, and EPUB exports. Clean mode is enabled by default.
- English and Simplified Chinese UI.

Jinjiang is the primary integration. Changpei and Fanqie have dedicated adapters; 95 additional imported rules are experimental. Site changes and verification may affect availability. The app reads chapters already accessible to the account and does not purchase chapters.

## Build and run

For installation without developer tools, download the Apple Silicon DMG from [GitHub Releases](https://github.com/TheOverflowing/pagefall/releases/latest), then drag the app into Applications. Requires macOS 14+. The current package is locally signed and not notarized; see [installation notes](docs/INSTALL.md).

Requires macOS 14+, Python 3.10+, Node.js 18+, and Xcode Command Line Tools.

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

This source development build is locally signed and not notarized. Keep the full project directory; the app starts its Python service from that directory. Rebuild after moving it. The service listens on `127.0.0.1:8765`.

Connect Jinjiang from the account entry at the bottom of the sidebar, either through the App account flow or the [Chrome extension](chrome-extension/README.md). Open a cover/title for details, double-click a chapter to read, or download a book. The Translation sidebar accepts saved books or pasted text.

Automatic updates can be managed from the app menu or menu bar. Existing automatic-renewal subscriptions are checked roughly every minute, other saved books every 15 minutes. Pagefall reads the subscription list without changing it. Checks continue while the main window is closed, and pause while the app quits or the Mac sleeps.

Chapter history contains text actually acquired or edited locally. Saved copies remain readable after a chapter becomes locked or disappears from the directory. Polling and network delays can miss short-lived edits. See the [changelog](CHANGELOG.md) for differences between `main` and the published installer.

AI translation sends the selected text and relevant context to the chosen provider and may incur charges. OCR and translations can contain errors and should be reviewed.

## Data and license

Reading data, screenshots, sessions, and translation checkpoints remain in `.local/`; exports go to `exports/`. Credentials and API keys are stored in macOS Keychain. These files are excluded from Git.

Licensed under [AGPL-3.0-or-later](LICENSE). Third-party components retain their own licenses. Parser and font-decoding code derives from [novel-downloader](https://github.com/404-novel-project/novel-downloader), pinned to `57cef4fc94638009fb1734e9e90feb8ccb7720bb`; corresponding sources and notices are included in `vendor/novel-downloader` and `engine/NOTICES.md`.

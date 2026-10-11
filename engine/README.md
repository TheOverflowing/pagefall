# Novelist browser rule bridge

This experimental **text parser** imports selected `novel-downloader` rules. The current build contains 95 rules for 134 exact hosts. It does not imply those live websites are all reachable or still compatible. Jinjiang uses a separate dedicated upstream parser bundle, with the native screenshot OCR adapter as fallback.

## Browser API

Load `engine/dist/novelist-rules.js` in the book's browser page, then wait for `window.NovelistRules`. The bundle is an async IIFE to support upstream lazy modules with top-level await. It does not import the userscript entry point.

```js
const rule = window.NovelistRules.detect();
// null, or { id, name, hosts, status: 'unverified' }
const book = await window.NovelistRules.scan({ rule_id: rule.id });
// { rule_id, title, author, url, imported: true, verified: false,
//   chapters: [{ id, title, url, order, vip, paid, blocked }] }
const chapter = await window.NovelistRules.read({ chapter_id: book.chapters[0].id });
// { title, text, raw_text, method: 'upstream_text', images,
//   needs_ocr, gate?, review: true }
```

`scan` requires a real book/contents page; exact host detection alone does not establish that an arbitrary page is a book. Missing metadata or an empty directory fails explicitly. `book_patterns` is empty when upstream provides only host routing; `book_exclude_patterns` carries known chapter-page exclusions. The manifest is at `engine/rules-manifest.json`.

Keep the same book page for subsequent `read` calls. Rule constructors and chapter options stay only in memory, not in the serialized book. After a restart/navigation, inject and scan again. Chapter IDs are `c_` plus the first 24 hex characters of SHA-256 of the canonical chapter URL; fragments, tracking and known credential query parameters are removed. Reordering the directory does not change IDs. Duplicate chapter URLs are deduplicated. A foreign-host link causes scan to fail rather than silently removing chapters.

`read` returns plain strings and counts, **not executable HTML or synthetic screenshots**. `needs_ocr` means images/private-use characters/custom-font evidence was detected; the native app must not count it as a successful text capture. This bridge does not provide OCR. Font evidence detection is conservative and cannot prove that every external stylesheet is safe; all imported text remains reviewable. Readable login gates are reported via `gate`; empty content and unavailable/unconfirmed paid chapters throw an error.

## Runtime boundary

- No upstream `run()`, save/export module, statistics, archive upload, UI, settings, scheduler, arbitrary finish callback, broadcast channel, or telemetry is included.
- `Book`/`Chapter` are data containers; logging is disabled; attachment helpers return descriptors without downloading images or covers.
- All free `fetch` references in imported parsers are rewritten at build time to a private network wrapper. The page's own `fetch` is untouched.
- The wrapper permits only HTTP(S), default ports, the exact hostname of the current book page, and URLs without embedded credentials. Redirects are rejected before following. Custom authorization/cookie headers are rejected. GET/HEAD only, apart from the audited directory POST endpoint on `b.guidaye.com`.
- Existing browser authentication follows ordinary browser cookie rules. There is no cookie-store access, user-password access, automatic purchase, or fallback to another website to read a paid chapter.
- Fetched HTML is parsed as a detached document. Scripts, frames and event handlers are removed before the rule receives it. The bridge returns no HTML to the host. The syosetu directory adapter appends sanitized, resource-free directory links to its book page; this local source modification is documented in NOTICES.
- Rules requiring eval/page callbacks or frame-specific execution are excluded and listed in the manifest. Parser requests have a 256-request / 120-second operation limit and 30-second request timeout.

A website itself may request its normal resources when the browser opens it; this boundary applies to the imported parser, not the website's own application code.

## Build and verify

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run build:engine
npm run build:jinjiang
.venv/bin/python -m unittest discover -s engine/tests -v
```

Build input is pinned at `vendor/novel-downloader/UPSTREAM.json`; build output records every included input in `engine/dist/build-report.json`. The generator rejects accidental inclusion of upstream entry points, UI, saving, routing, settings, or statistics modules. Offline tests exercise actual one-page/direct-class/two-page/paid rules, stable IDs, the network boundary and OCR-required flags. They do not verify live compatibility across the catalog.

## Dedicated Jinjiang bridge (0.6)

Normal Jinjiang directories use the host's public HTML parser. With a saved Android Token, `novelist/jinjiang.py` requests chapter content directly and runs `engine/dist/novelist-jinjiang-api.cjs` with the Node binary already packaged by Playwright. The bundle extracts the pinned upstream response/body decryption routines; it receives response bytes, never the Token, and needs no DOM or Chromium. Python performs the matching text cleanup and completeness checks. Requests share a reusable HTTPS connection and a 0.5-second minimum start interval across downloads and reading. Network reads remain serial; system/environment proxy settings and bypass rules follow urllib. A disconnected reused socket reconnects once for the read-only request; HTTP status errors do not retry. Web/OCR fallback retains a three-second queue interval.

Only when this path cannot produce valid text does the host start a browser and load `engine/dist/novelist-jinjiang.js`, which imports the pinned `jjwxc.ts`, `jjwxcFontDecode.ts`, and `OCRDecoder.ts`. This fallback uses existing Jinjiang cookies for webpage crypto/CSS/font decoding, followed by rendered-page Apple Vision OCR if needed. It does not retry the same failed Token API call. Without a Token, webpage parsing comes first. The native app keeps its queue, library and export pipeline.

The dedicated bridge permits only the requested chapter, its known font path, the matching read-only App chapter endpoint, and the exact public OCR model URL, with 16 requests per operation. Account requests reject redirects; the model downloader accepts only the public GitHub release asset redirect without account cookies. The host does not return credential headers. Crypto metadata scripts are retained as inert text in detached documents, never executed. Explicit chapter identity checks reject mismatched decrypted forms/API responses. Private-use glyphs, gate text, empty/very short results and substantially insufficient word counts fall back; login/captcha gates pause in the rendered adapter.

Font matching reuses upstream glyph comparison and PaddleOCR when needed. Models are cached in `.local/ocr-models` and IndexedDB; the upstream ONNX runtime loads pinned WASM components from unpkg. Network/model failure falls back to native OCR. Tests exercise pinned response/body decryption, direct reads with browser initialization forbidden, and API-to-web and web-to-OCR fallback. A live public directory and an authorized VIP chapter were also read without starting a browser. The Paddle model runtime remains outside these live checks. Run `.venv/bin/python -m unittest discover -s tests -p 'test_jinjiang*.py' -v`.

`scripts/build_jinjiang.mjs` records included sources in `engine/dist/jinjiang-build-report.json`. Build-time compatibility and identity checks are listed in NOTICES. No upstream userscript bootstrap, statistics, archive upload, account UI or export pipeline is included.

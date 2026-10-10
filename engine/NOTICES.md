# Source and licensing notices

The parser bundle includes code from [404-novel-project/novel-downloader](https://github.com/404-novel-project/novel-downloader), authored by BGME and project contributors, pinned to commit `57cef4fc94638009fb1734e9e90feb8ccb7720bb` (upstream version 5.2.0).

Upstream code is licensed under **GNU Affero General Public License, version 3 or later**. The full license is retained at `vendor/novel-downloader/license.txt`. Source notices and original source files are retained in that directory. The derived rule bundle and its adaptation code in `engine/` are provided under that same license. Distributors must retain the notices and provide the corresponding source and build instructions as required by that license. Third-party components retain their own licenses. The local application source is also released under AGPL-3.0-or-later, as stated in the root LICENSE.

## Local adaptation

- Upstream rule files and DOM utilities are copied from the pinned revision. One local safety modification is applied to `src/rules/onePage/original/syosetu.ts`: directory pagination uses the detached/sanitized HTTP helper, strips resource elements before appending directory nodes, and fails explicitly on a missing/failed page instead of logging and silently importing a partial directory. Other copied sources remain unchanged.
- `scripts/build_engine.mjs` selects eligible exact-host routes and generates the manifest/loader. It imports only the selected rule modules, not upstream bootstrap or router execution.
- Build-time aliases replace upstream BaseRuleClass, model containers, logging, HTTP, attachment fetching and task helpers with the small local implementations under `engine/shims/`.
- These replacements remove the upstream userscript UI, scheduler, export pipeline, archive/statistics behavior and page-provided callbacks; they enforce the selected-site request boundary.
- `engine/bridge.ts` supplies the serializable scan/read interface, explicit paid/readability checks, stable chapter IDs and conservative OCR-required flags.
- `engine/inject.ts` redirects imported modules' free `fetch` references to the scoped wrapper without mutating the website's global fetch.
- esbuild is pinned as a build-time dependency in the root package lock. Its own license remains in the installed package. A local serial task helper replaces the upstream p-limit import. The adaptation bundles @noble/hashes 1.8.0 (MIT, Paul Miller and contributors) for deterministic SHA-256 on both HTTPS and non-secure HTTP pages; its license is retained at engine/third-party/noble-hashes-LICENSE. Source: https://github.com/paulmillr/noble-hashes/tree/1.8.0. The exact dependency and reproducible installation are recorded in package-lock.json.

The manifest's `status: unverified` describes imported, experimental rules. It is not a statement of live-site support. `engine/tests` uses synthetic pages and does not contain copied novel chapters or account data.

## Dedicated Jinjiang adaptation (0.6)

Original `src/rules/special/original/jjwxc.ts`, `src/rules/lib/jjwxcFontDecode.ts` and `src/lib/decoders/OCRDecoder.ts` are retained unchanged from the same pinned revision. `scripts/build_jinjiang.mjs` applies only build-time adaptations: replace the constructor account/settings UI with native-host configuration; supply a private login flag and opaque API Token placeholder; reject mismatched chapter IDs in decrypted forms/API responses; reject unknown API encryption instead of returning ciphertext; and record font mapping counts. Host compatibility, network and model-storage helpers live in `engine/jinjiang/`; Python owns real credentials and the bounded network bridge. These adaptations and the dedicated derived bundle are AGPL-3.0-or-later, with the original full license retained above. Upstream scheduler, export/archive, statistics and userscript UI are excluded.

The dedicated bundle also includes the following pinned npm dependencies. License copies are retained in `engine/third-party`; exact versions and transitive dependencies are recorded in `package-lock.json`. The upstream eSearch patch changes TypeScript generic annotations only; the published runtime is used unchanged.

- CryptoJS 4.2.0 — MIT, CryptoJS contributors; `crypto-js-LICENSE`.
- css-tree 3.2.1 — MIT, Roman Dvornov and contributors; `css-tree-LICENSE`.
- @oovz/esearch-ocr 8.4.4 — Apache-2.0, eSearch-OCR contributors; `esearch-ocr-LICENSE`.
- onnxruntime-web 1.29.0 — MIT, Microsoft Corporation; `onnxruntime-web-LICENSE`.
- fflate 0.8.3 — MIT, Arjun Barrett; `fflate-LICENSE`.

PaddleOCR models are downloaded on demand from the upstream-configured public eSearch-OCR release, not redistributed inside the application bundle. Network/component availability may cause native OCR fallback.

## Native Jinjiang account connection (0.6.1)

`novelist/jinjiang_auth.py` adapts the pinned upstream constructor's DES-CBC/PKCS7 login encoding, Android login endpoint and device verification endpoint to a native flow. This adaptation is AGPL-3.0-or-later. It retains the service's phone/email verification, uses a stable random local device identity, bounds challenge lifetime/attempts/resends, rejects redirects and redacts server errors instead of logging URLs or responses. Only the returned Token is written through the existing Keychain helper. Passwords/codes are request-local and are never persisted; the web parser bridge does not gain access to login endpoints or credentials.

Python dependency `pycryptodomex==3.23.0` (PyCryptodome contributors, BSD/Public Domain) supplies the upstream-compatible DES primitive. The installed distribution retains its license, with a copy at `engine/third-party/pycryptodomex-LICENSE`. DES here is only the upstream login protocol encoding; HTTPS certificate validation remains enabled. Node is still a build/test dependency, not a login runtime dependency.

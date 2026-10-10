"""Entry point for the bundled backend and Chrome connector."""
import os
from pathlib import Path
import sys

if getattr(sys, 'frozen', False):
    resources = Path(sys.executable).resolve().parent.parent
    os.environ.setdefault('NOVELIST_RESOURCE_ROOT', str(resources))
    os.environ.setdefault('NOVELIST_DATA_ROOT', str(Path.home() / 'Library/Application Support/Pagefall'))
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(resources / 'browsers')


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    if command == 'native-host':
        from novelist.native_host import main as native_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return native_main()
    if command == 'install-chrome-bridge':
        from scripts.install_chrome_bridge import main as install_main
        install_main()
        return 0
    if command == 'serve':
        import uvicorn
        from novelist.server import app
        port = int(os.environ.get('NOVELIST_PORT', '8765'))
        uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)
        return 0
    if command == 'smoke':
        import asyncio
        import json
        from playwright.async_api import async_playwright
        from novelist.platforms import platforms
        from novelist.browser import recognize_image
        async def check():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content('<h1>Pagefall standalone</h1>')
                assert await page.locator('h1').inner_text() == 'Pagefall standalone'
                await browser.close()
            result = {'browser': 'ok', 'platforms': len(platforms())}
            if len(sys.argv) > 2:
                import tempfile
                with tempfile.TemporaryDirectory() as folder:
                    text, lines = await recognize_image(Path(sys.argv[2]), Path(folder))
                    assert '清晨' in text and len(lines) >= 10
                    result['vision_ocr'] = 'ok'
            print(json.dumps(result))
        asyncio.run(check())
        return 0
    raise SystemExit('Unknown command')


if __name__ == '__main__':
    raise SystemExit(main())

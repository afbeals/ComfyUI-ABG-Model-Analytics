"""Exercise the real frontend and backend with a minimal ComfyUI host adapter.

Run from the repository root: python -m tests.browser_smoke
Optional: ABG_SCREENSHOT=/absolute/path.png to capture the dashboard.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

from abg_analytics.api import register_routes
from abg_analytics.service import Analytics
from tests.test_analytics import Folders

ROOT = Path(__file__).resolve().parents[1]


async def run():
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        folders = Folders(base)
        local = Path(folders.models_dir) / "loras" / "flux2" / "sample.safetensors"
        external = base / "external" / "models" / "loras" / "flux2" / local.name
        for path in [local, external]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"dummy model weights")
        analytics = Analytics(folders)
        workflow = base / "user" / "default" / "workflows" / "Portrait.json"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            json.dumps({"nodes": [{"type": "LoraLoader", "widgets_values": ["flux2/sample.safetensors", 1]}]})
        )
        server = web.Application()
        routes = web.RouteTableDef()
        register_routes(routes, analytics)

        @routes.get("/")
        async def index(request):
            return web.Response(
                text='<!doctype html><html lang="en"><title>Analytics test host</title><main><h1>ComfyUI host adapter</h1><div id="sidebar"></div></main><script type="module" src="/extensions/ABG/js/main.js"></script></html>',
                content_type="text/html",
            )

        @routes.get("/scripts/app.js")
        async def app_module(request):
            return web.Response(
                text='export const app = {extensionManager:{registerSidebarTab(tab){tab.render(document.querySelector("#sidebar"));}},registerExtension(extension){extension.setup();}};',
                content_type="text/javascript",
            )

        @routes.get("/scripts/api.js")
        async def api_module(request):
            return web.Response(
                text="export const api = {fetchApi:(path, options)=>fetch(path, options)};",
                content_type="text/javascript",
            )

        @routes.get("/favicon.ico")
        async def favicon(request):
            return web.Response(status=204)

        server.add_routes(routes)
        server.router.add_static("/extensions/ABG/", ROOT / "web")
        runner = web.AppRunner(server)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with async_playwright() as playwright:
                options = {"headless": True}
                chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
                if chrome.exists():
                    options["executable_path"] = str(chrome)
                browser = await playwright.chromium.launch(**options)
                page = await browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(f"http://127.0.0.1:{port}")
                await page.get_by_role("button", name="Open ABG Model Analytics").click()
                await page.get_by_text("Tracking since", exact=False).wait_for()
                await page.get_by_label("Trash parent directory").fill(str(base / "cleanup"))
                await page.get_by_role("button", name="Save settings").click()
                await page.get_by_role("status").filter(has_text="Settings saved.").wait_for()
                await page.get_by_role("button", name="Unknown history (2)", exact=True).click()
                await page.get_by_role("button", name=f"Protect {local}", exact=True).click()
                await page.get_by_role("status").filter(has_text="Model protected.").wait_for()
                assert await page.get_by_role("checkbox", name=f"Select {local}", exact=True).is_disabled()
                await page.get_by_role("button", name="Protected (1)", exact=True).click()
                await page.get_by_role("button", name=f"Unprotect {local}", exact=True).click()
                await page.get_by_role("status").filter(has_text="Protection removed.").wait_for()
                await page.get_by_role("button", name="Copies (2)", exact=True).click()
                await page.get_by_text("Shadowed copy", exact=False).wait_for()
                await page.get_by_role("button", name="Check exact duplicates", exact=True).click()
                await page.get_by_role("status").filter(has_text="1 identical-content groups").wait_for()
                assert (
                    await page.locator(".abg-model-details summary").filter(has_text="Identical content").count() == 2
                )
                await page.get_by_role("button", name="Saved workflows (1)", exact=True).click()
                await page.get_by_role(
                    "button", name="View models referenced by default/workflows/Portrait.json", exact=True
                ).click()
                await page.get_by_role("checkbox", name=f"Select {local}", exact=True).check()
                await page.get_by_role("button", name="Preview move (1)", exact=True).click()
                confirmation = page.get_by_role("dialog", name="Review moves to trash")
                await confirmation.wait_for()
                await confirmation.get_by_text("Review saved workflow dependencies", exact=True).wait_for()
                expected = base / "cleanup" / "trash" / "models" / "loras" / "flux2" / local.name
                await confirmation.get_by_role("cell", name=str(expected), exact=True).wait_for()
                await confirmation.get_by_role("button", name="Cancel", exact=True).click()
                assert local.exists() and not expected.exists(), "Preview changed a source file"
                await page.get_by_role("button", name="Preview move (1)", exact=True).click()
                await confirmation.get_by_role("button", name="Move to trash", exact=True).click()
                await page.get_by_role("status").filter(has_text="1 file moved.").wait_for()
                assert expected.exists() and not local.exists(), "Trash move did not complete"
                assert external.exists(), "Same-named model in another root was touched"
                assert analytics.trash_summary()["size_bytes"] == len(b"dummy model weights")
                await page.get_by_role("button", name=f"Restore {local}", exact=True).click()
                await (
                    page.get_by_role("dialog", name="Restore model")
                    .get_by_role("button", name="Restore file", exact=True)
                    .click()
                )
                await page.get_by_role("status").filter(has_text="File restored to its original location.").wait_for()
                assert local.exists() and not expected.exists(), "Restore did not complete"
                assert analytics.trash_summary()["size_bytes"] == 0
                await page.get_by_role("button", name="All models (2)", exact=True).click()
                await page.get_by_label("Search", exact=True).fill("no-match-for-this-model")
                await page.get_by_text("No models match this view.", exact=True).wait_for()
                await page.get_by_label("Search", exact=True).fill("")
                screenshot = os.environ.get("ABG_SCREENSHOT")
                if screenshot:
                    await page.screenshot(path=screenshot, full_page=True)
                await page.set_viewport_size({"width": 600, "height": 850})
                assert await page.locator("body").evaluate("e => e.scrollWidth <= window.innerWidth"), (
                    "Page overflows on a narrow screen"
                )
                await page.keyboard.press("Escape")
                await page.get_by_role("dialog", name="ABG Model Analytics").wait_for(state="detached")
                assert not errors, errors
                await browser.close()
                print(
                    "Browser smoke passed: settings, discovery, protection, saved workflows, shadowing, exact duplicates, impact warnings, trash totals, preview/cancel, trash/restore, search, narrow viewport, Escape; no browser errors."
                )
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())

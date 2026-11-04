"""Check the standalone sample preview, including its simulated feature flows."""

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def run():
    async with async_playwright() as playwright:
        options = {"headless": True}
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.exists():
            options["executable_path"] = str(chrome)
        browser = await playwright.chromium.launch(**options)
        page = await browser.new_page(viewport={"width": 1560, "height": 1120})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto((ROOT / "preview/index.html").as_uri())
        await page.get_by_role("button", name="Not recently used (5)", exact=True).wait_for()
        await page.get_by_role("button", name="Protected (3)", exact=True).wait_for()
        assert await page.locator(".abg-stat-trash strong").inner_text() == "4.6 GiB"
        await page.get_by_role("button", name="Copies (2)", exact=True).click()
        await page.get_by_role("button", name="Check exact duplicates", exact=True).click()
        await page.get_by_role("status").filter(has_text="1 identical-content groups").wait_for()
        assert await page.locator(".abg-model-details summary").filter(has_text="Identical content").count() == 2
        await page.get_by_role("button", name="Saved workflows (4)", exact=True).click()
        await page.get_by_role(
            "button", name="View models referenced by default/workflows/Quick drafts.json", exact=True
        ).click()
        assert await page.locator("[data-id]").count() == 3
        assert await page.locator("[data-id]:disabled").count() == 3
        await page.get_by_label("Saved workflow", exact=True).select_option("")
        await page.get_by_role("button", name="Not recently used (5)", exact=True).click()
        first = page.locator("[data-protect]").first
        await first.click()
        await page.get_by_role("status").filter(has_text="Model protected.").wait_for()
        await page.get_by_role("button", name="Protected (4)", exact=True).wait_for()
        assert await page.locator("[data-id]").first.is_disabled()
        await page.locator("[data-id]").nth(1).check()
        await page.locator("[data-id]").nth(2).check()
        await page.locator(".abg-content > .abg-scroll").evaluate("element => { element.scrollTop = 0; }")
        screenshot_dir = os.environ.get("ABG_PREVIEW_SCREENSHOT_DIR")
        if screenshot_dir:
            directory = Path(screenshot_dir)
            directory.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(directory / "dashboard.png"), full_page=True)
        await page.get_by_role("button", name="Preview move (2)", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Review moves to trash")
        await confirmation.get_by_text("Review saved workflow dependencies", exact=True).wait_for()
        if screenshot_dir:
            await page.screenshot(path=str(Path(screenshot_dir) / "confirmation.png"), full_page=True)
        await confirmation.get_by_role("button", name="Move to trash", exact=True).click()
        await page.get_by_role("status").filter(has_text="2 files moved.").wait_for()
        trash_total = await page.locator(".abg-stat-trash strong").inner_text()
        assert 13.49 <= float(trash_total.split()[0]) <= 13.61, trash_total
        if screenshot_dir:
            await page.screenshot(path=str(Path(screenshot_dir) / "trash.png"), full_page=True)
        await page.locator("[data-restore]").first.click()
        await page.get_by_role("button", name="Restore file", exact=True).click()
        await page.get_by_role("status").filter(has_text="File restored to its original location.").wait_for()
        assert await page.locator(".abg-stat-trash strong").inner_text() != trash_total
        await page.set_viewport_size({"width": 600, "height": 850})
        assert await page.locator("body").evaluate("e => e.scrollWidth <= window.innerWidth")
        await browser.close()
        assert not errors, errors
        print(
            "Standalone preview passed: protection, workflow filtering, copies/hash check, impact warnings, trash sizes, moves and restore; no browser errors."
        )


if __name__ == "__main__":
    asyncio.run(run())

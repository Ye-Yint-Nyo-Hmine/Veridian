"""tools/browser — Playwright-backed page tools.

Playwright is optional. If it is not importable the brick still starts and reports ready; its tool
list is empty and every invoke returns an error explaining that Playwright is not installed. It
never fails to start.
"""

from __future__ import annotations

from veridian.sdk import Brick, rpc, run

try:
    from playwright.async_api import async_playwright  # type: ignore

    _AVAILABLE = True
except Exception:  # noqa: BLE001
    _AVAILABLE = False

_TOOLS = [
    {
        "name": "open_page",
        "description": "Load a URL in a headless browser and return its visible text.",
        "input_schema": {
            "type": "object",
            "required": ["url"],
            "properties": {"url": {"type": "string"}, "wait_ms": {"type": "integer"}},
        },
    }
]


class BrowserTools(Brick):
    name = "tools/browser"
    version = "0.1.0"
    implements = {"tools": ["list", "invoke"]}

    async def on_initialize(self) -> bool:
        self.available = _AVAILABLE
        if not self.available:
            await self.host.log("playwright not installed; browser tools are unavailable", level="warning")
        return True  # ready either way

    @rpc("tools.list")
    async def list_(self, params, ctx):
        return {"tools": _TOOLS if self.available else []}

    @rpc("tools.invoke")
    async def invoke(self, params, ctx):
        if not self.available:
            return {"output": "browser unavailable: playwright is not installed", "is_error": True}
        if params["name"] != "open_page":
            return {"output": f"unknown tool {params['name']!r}", "is_error": True}
        inp = params.get("input", {})
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(inp["url"], wait_until="load", timeout=inp.get("wait_ms", 15000))
                text = await page.inner_text("body")
            finally:
                await browser.close()
        return {"output": text[:20000]}


if __name__ == "__main__":
    run(BrowserTools())

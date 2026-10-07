"""Capture the two local API scenarios used in the README screenshots.

One-time setup: python3 -m pip install playwright && python3 -m playwright install chromium
Run from the project root: python3 scripts/capture_screenshots.py
"""
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import start_servers


def main():
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Install the optional capture tool: python3 -m pip install playwright && "
            "python3 -m playwright install chromium"
        ) from exc

    gateway, downstream, _ = start_servers(0, 0)
    thread = Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    output = ROOT / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{gateway.server_address[1]}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1350, "height": 900}, device_scale_factor=1)
            for scenario, filename in [
                ("database", "database-failure.png"),
                ("empty", "silent-empty.png"),
            ]:
                page.goto(base, wait_until="networkidle")
                page.locator(f'button[data-fault="{scenario}"]').click()
                page.locator("#run").click()
                expect(page.locator("#traceMode")).to_have_text("RECORDED LOCAL HTTP TRACE")
                page.screenshot(path=str(output / filename), full_page=True, animations="disabled")
                print(output / filename)
            browser.close()
    finally:
        gateway.shutdown()
        downstream.shutdown()
        gateway.server_close()
        downstream.server_close()


if __name__ == "__main__":
    main()

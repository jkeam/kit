"""Fixed helper script for browser_screenshot. Run as a subprocess with
argv = [url, output_path] so values are never interpolated into source code.
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

NAV_TIMEOUT_MS = int(os.environ.get("BROWSER_NAV_TIMEOUT_MS", "30000"))


def main() -> None:
    url = sys.argv[1]
    output_path = sys.argv[2]

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        page.screenshot(path=str(output), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()

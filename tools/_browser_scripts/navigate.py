"""Fixed helper script for browser_navigate. Run as a subprocess with
argv = [url] so values are never interpolated into source code.
"""
import sys

from playwright.sync_api import sync_playwright

from _common import env_int

NAV_TIMEOUT_MS = env_int("BROWSER_NAV_TIMEOUT_MS", 30000)


def main() -> None:
    url = sys.argv[1]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)

        title = page.title()
        url_after = page.url
        text_content = page.inner_text("body")[:1000]

        browser.close()

        print(f"Title: {title}")
        print(f"Final URL: {url_after}")
        print(f"Content: {text_content}")


if __name__ == "__main__":
    main()

"""Fixed helper script for browser_extract. Run as a subprocess with
argv = [url, selector] so values are never interpolated into source code.
"""
import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    url = sys.argv[1]
    selector = sys.argv[2]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)

        elements = page.query_selector_all(selector)

        if not elements:
            print(f"No elements found matching selector: {selector}")
        else:
            results = []
            for i, el in enumerate(elements[:10], 1):
                text = el.inner_text().strip()
                if text:
                    results.append(f"{i}. {text}")

            if results:
                for r in results:
                    print(r)
            else:
                print(f"Found {len(elements)} elements but no text content")

        browser.close()


if __name__ == "__main__":
    main()

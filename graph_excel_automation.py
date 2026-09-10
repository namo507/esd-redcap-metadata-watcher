#!/usr/bin/env python3
"""Microsoft Graph Excel/SharePoint workflow.

This is a practical starter for browser-driven Microsoft file work using the saved
persistent profile from Chrome. It does not attempt to bypass login or SSO requirements;
instead it assumes the browser has already authenticated once and then keeps the session.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GRAPH_EXPLORER_URL = os.getenv("GRAPH_EXPLORER_URL", "https://developer.microsoft.com/en-us/graph/graph-explorer")
PROFILE_DIR = ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")


def open_graph_explorer_and_wait() -> None:
    """Open Graph Explorer and keep the authenticated session alive."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Graph Explorer profile: {PROFILE_DIR}")
    print(f"URL: {GRAPH_EXPLORER_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1200},
        )
        page = browser.new_page()
        page.goto(GRAPH_EXPLORER_URL, wait_until="domcontentloaded")
        print("\nSign in to Microsoft when prompted. Once authenticated, you can use Graph Explorer for Excel or SharePoint workbook updates.\n")
        input("Press Enter when you are done with the browser session.\n")
        browser.close()


if __name__ == "__main__":
    open_graph_explorer_and_wait()

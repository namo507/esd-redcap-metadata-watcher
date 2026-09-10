#!/usr/bin/env python3
"""Persistent Microsoft Graph Explorer workflow for SharePoint/Excel automation.

This script opens Graph Explorer in a dedicated Chrome profile that preserves the user
session between runs. Use it when you need Microsoft sign-in (SSO/MFA) to complete once,
then keep the browser profile for later Excel/SharePoint automations.
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


def launch_graph_explorer(wait_seconds: int = 60) -> None:
    """Open Graph Explorer in a persistent browser profile and keep it alive for sign-in."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Using browser profile: {PROFILE_DIR}")
    print(f"Opening Graph Explorer: {GRAPH_EXPLORER_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1200},
        )
        page = browser.new_page()
        page.goto(GRAPH_EXPLORER_URL, wait_until="domcontentloaded")

        print("\nThe browser is now open in a persistent profile.")
        print("1. Sign in with the Microsoft account if prompted.")
        print("2. Complete SSO / MFA if your tenant requires it.")
        print("3. Click 'Run query' once the Graph Explorer page is authenticated.")
        print(f"4. Leave the browser open while the session is active; it will be reused on future runs.\n")

        if wait_seconds > 0:
            page.wait_for_timeout(wait_seconds * 1000)

        print("The browser session is still active. Close the window when you are finished.")
        try:
            page.wait_for_load_state("networkidle")
        except Exception:
            pass

        input("Press Enter to close the browser session when you are done.\n")
        browser.close()


if __name__ == "__main__":
    launch_graph_explorer()

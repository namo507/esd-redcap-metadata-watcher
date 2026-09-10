#!/usr/bin/env python3
"""Persistent REDCap SSO workflow.

This script opens the REDCap SSO page in a dedicated Chrome profile so the user can
complete USC Shibboleth / Duo sign-in once, then keep the authenticated browser session
for subsequent API access and automation tasks.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

REDCAP_SSO_URL = os.getenv("REDCAP_SSO_URL", "https://redcap.research.sc.edu/")
PROFILE_DIR = ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")


def launch_redcap_sso(wait_seconds: int = 60) -> None:
    """Open REDCap SSO in a persistent browser profile and wait for user sign-in."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Using browser profile: {PROFILE_DIR}")
    print(f"Opening REDCap SSO: {REDCAP_SSO_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1200},
        )
        page = browser.new_page()
        page.goto(REDCAP_SSO_URL, wait_until="domcontentloaded")

        print("\nThe browser is now open in a persistent profile.")
        print("1. Sign in using USC SSO / Duo if prompted.")
        print("2. Complete the login flow once.")
        print("3. Leave the session open; it will be reused by later browser automation.\n")

        if wait_seconds > 0:
            page.wait_for_timeout(wait_seconds * 1000)

        print("The REDCap browser session is still active. Close the window when you are finished.")
        input("Press Enter to close the browser session when you are done.\n")
        browser.close()


if __name__ == "__main__":
    launch_redcap_sso()

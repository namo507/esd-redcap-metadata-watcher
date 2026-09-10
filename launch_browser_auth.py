import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GRAPH_EXPLORER_URL = os.getenv("GRAPH_EXPLORER_URL", "https://developer.microsoft.com/en-us/graph/graph-explorer")
REDCAP_SSO_URL = os.getenv("REDCAP_SSO_URL", "https://redcap.research.sc.edu/")
PROFILE_DIR = ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")


def launch_site(url: str, label: str):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1200},
        )
        page = browser.new_page()
        page.goto(url)
        print(f"Opened {label} in persistent profile: {PROFILE_DIR}")
        print("Keep this browser window open while you complete sign-in, then close it when finished.")
        page.wait_for_timeout(30000)
        browser.close()


if __name__ == "__main__":
    print("Microsoft and REDCap browser automation ready.")
    print("Choose one option:")
    print("1. Graph Explorer")
    print("2. REDCap SSO")
    choice = input("Enter 1 or 2: ").strip()
    if choice == "1":
        launch_site(GRAPH_EXPLORER_URL, "Graph Explorer")
    else:
        launch_site(REDCAP_SSO_URL, "REDCAP SSO")

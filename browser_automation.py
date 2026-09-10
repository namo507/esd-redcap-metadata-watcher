import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GRAPH_EXPLORER_URL = os.getenv("GRAPH_EXPLORER_URL", "https://developer.microsoft.com/en-us/graph/graph-explorer")
REDCAP_SSO_URL = os.getenv("REDCAP_SSO_URL", "https://redcap.research.sc.edu/")
PROFILE_DIR = ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")
HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
MS_GRAPH_TOKEN = os.getenv("MS_GRAPH_ACCESS_TOKEN") or os.getenv("GRAPH_ACCESS_TOKEN")


def open_persistent_site(url: str, label: str):
    """Open a URL in a persistent Chrome profile that keeps sign-in state."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1200},
        )
        page = browser.new_page()
        page.goto(url)
        print(f"Opened {label} in persistent profile: {PROFILE_DIR}")
        print("Complete sign-in once in the browser. The profile retains the session for future runs.")
        page.wait_for_timeout(30000)
        browser.close()


def open_graph_explorer():
    open_persistent_site(GRAPH_EXPLORER_URL, "Graph Explorer")


def open_redcap_sso():
    open_persistent_site(REDCAP_SSO_URL, "REDCAP SSO")


def graph_request(path: str, method: str = "GET", json_body=None, token: str | None = None):
    """Call the Microsoft Graph API and return JSON, using the saved access token by default."""
    if token is None:
        token = MS_GRAPH_TOKEN
    if not token:
        raise RuntimeError("No Microsoft Graph token found. Put MS_GRAPH_ACCESS_TOKEN in .env or sign in in the browser first.")

    url = f"https://graph.microsoft.com/v1.0{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.request(method=method, url=url, headers=headers, json=json_body, timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if response.status_code >= 400:
        raise RuntimeError(f"Graph request failed: {response.status_code} {payload}")
    return payload


def graph_me():
    return graph_request("/me")


def graph_recent_mail(limit: int = 5):
    return graph_request(f"/me/messages?$top={limit}&$select=subject,from,receivedDateTime,isRead")


def graph_drive_files():
    return graph_request("/me/drive/root/children")


def graph_excel_ranges(file_id: str, worksheet_name: str, address: str):
    return graph_request(
        f"/me/drive/items/{file_id}/workbook/worksheets('{worksheet_name}')/range(address='{address}')"
    )


def graph_update_range(file_id: str, worksheet_name: str, address: str, values):
    return graph_request(
        f"/me/drive/items/{file_id}/workbook/worksheets('{worksheet_name}')/range(address='{address}')",
        method="PATCH",
        json_body={"values": values},
    )


def demo_graph_api_checks():
    print("Checking Microsoft Graph token and basic access...")
    me = graph_me()
    print("User:", me.get("userPrincipalName") or me.get("displayName"))
    mail = graph_recent_mail(limit=3)
    print("Recent mail count:", len(mail.get("value", [])))
    drive = graph_drive_files()
    print("Drive items count:", len(drive.get("value", [])))
    print("Example file names:", [item.get("name") for item in drive.get("value", [])[:5]])


if __name__ == "__main__":
    print("Browser automation ready.")
    print(f"Profile directory: {PROFILE_DIR}")
    print(f"Graph Explorer: {GRAPH_EXPLORER_URL}")
    print(f"REDCap SSO: {REDCAP_SSO_URL}")
    print()
    print("Choose an option:")
    print("1. Open Microsoft Graph Explorer")
    print("2. Open REDCap SSO")
    print("3. Run basic Graph API smoke-check")

    choice = input("Enter 1, 2, or 3: ").strip()
    if choice == "1":
        open_graph_explorer()
    elif choice == "2":
        open_redcap_sso()
    elif choice == "3":
        demo_graph_api_checks()
    else:
        print("Invalid selection. Exiting.")

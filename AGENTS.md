# Workspace Agent Guidelines & Browser Automation Rules

## 1. Browser Automation Protocol (Antigravity Browser View)

When performing browser automation tasks (e.g. accessing SharePoint Online, Outlook/email, Excel files, Microsoft Graph Explorer, or REDCap):
- Use the `browser_subagent` tool or Chrome DevTools MCP tools to navigate, inspect, and interact with web pages.
- **Persistent Profile**: The browser runs using the persistent Chrome profile directory at `~/.gemini/antigravity-browser-profile`. Cookies, sessions, and permissions are preserved between runs.
- **SSO & 2FA / MFA Login Flow**:
  - When navigating to Microsoft Online (`login.microsoftonline.com`) or REDCap / USC Shibboleth (`redcap.research.sc.edu`, `login.sc.edu`, Duo Security), an SSO login or Multi-Factor Authentication prompt may appear.
  - **Do NOT abort or loop**. Inform the user via chat that an SSO / MFA prompt requires their approval in the browser view window.
  - Once the user completes SSO / Duo verification, the browser view retains the authenticated session in the persistent profile.
  - Continue immediately with the planned browser automation steps.

---

## 2. Microsoft Graph & Excel Automation

### Browser View (Graph Explorer)
- **Target URL**: `https://developer.microsoft.com/en-us/graph/graph-explorer`
- Used for visual verification, testing queries, and making live Excel workbook modifications.
- User account: `namit507@sc.edu` (USC Office 365).

### Programmatic / REST API Queries
- The Microsoft Graph Access Token is stored in `.env` as `MS_GRAPH_ACCESS_TOKEN`.
- **Base Endpoint**: `https://graph.microsoft.com/v1.0`
- **Common Excel & OneDrive endpoints**:
  - List files: `GET https://graph.microsoft.com/v1.0/me/drive/root/children`
  - Search workbook: `GET https://graph.microsoft.com/v1.0/me/drive/root/search(q='{filename}')`
  - Get worksheets: `GET https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/workbook/worksheets`
  - Read range: `GET https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/workbook/worksheets/{name}/range(address='A1:Z50')`
  - Update range: `PATCH https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/workbook/worksheets/{name}/range(address='A1:B2')`
  - Append rows: `POST https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/workbook/tables/{table_id}/rows/add`

---

## 3. REDCap Automation & API

### Web Interface
- **Target URL**: `https://redcap.research.sc.edu/`
- User logs in via USC Shibboleth SSO + Duo.
- Persistent session allows navigation across project dashboards, data export tools, and designer screens.

### API Exploration (`requests.post`)
- **API Endpoint**: `https://redcap.research.sc.edu/api/` (stored in `REDCAP_API_URL`)
- Tokens are loaded from `.env`:
  - `TOKEN_CLEAN_4797`
  - `TOKEN_DIRTY_4581`
  - `NANO_API_TOKEN`
  - `NICO_API_TOKEN`
  - `IPSA_API_TOKEN`
  - `ACTION_API_TOKEN`
- Pattern for exploring project metadata and records:
  ```python
  import os
  import requests
  from dotenv import load_dotenv

  load_dotenv()
  api_url = os.environ.get("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")
  token = os.environ.get("NANO_API_TOKEN")

  # Example: Fetch Project Metadata (Data Dictionary)
  data = {
      "token": token,
      "content": "metadata",
      "format": "json",
      "returnFormat": "json"
  }
  r = requests.post(api_url, data=data, timeout=30)
  fields = r.json()
  ```

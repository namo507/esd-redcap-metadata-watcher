# Browser automation setup for Microsoft and REDCap

This workspace is configured for persistent browser-based authentication using Playwright + Chrome profile storage.

## Quick start

```bash
cd /Users/namomac/esd-redcap-metadata-watcher
source .venv312/bin/activate
python launch_browser_auth.py
```

Choose:
- `1` for Microsoft Graph Explorer
- `2` for REDCap SSO

## Important

- Keep local secrets in `.env` only.
- The browser profile directory is `.browser-profile` and is git-ignored.
- Do not commit tokens or sign-in cookies.

## Example Graph calls

```python
from browser_automation import graph_me, graph_drive_files, graph_recent_mail

print(graph_me())
print(graph_recent_mail(limit=3))
print(graph_drive_files())
```

## Common automation flow

1. Open Microsoft Graph Explorer in persistent browser profile.
2. Sign in once with USC / Microsoft account.
3. Use the same browser profile for SharePoint or Excel workbook work.
4. Keep the session stored in `.browser-profile` for subsequent automation.
5. Use `.env` to store `MS_GRAPH_ACCESS_TOKEN` and the REDCap API tokens.

## REDCap API flow

```python
import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_url = os.environ['REDCAP_API_URL']
token = os.environ['NANO_API_TOKEN']

data = {
    'token': token,
    'content': 'metadata',
    'format': 'json',
    'returnFormat': 'json',
}
response = requests.post(api_url, data=data, timeout=30)
print(response.status_code)
print(response.json())
```

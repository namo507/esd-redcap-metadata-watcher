---
trigger: always_on
description: Guidelines for browser automation, Microsoft Graph Explorer, and REDCap workflows.
---

# Browser Automation & External Services Guidelines

- **Browser Environment**: Antigravity agents use the `browser_subagent` and Chrome DevTools MCP tools with persistent browser profile at `~/.gemini/antigravity-browser-profile`.
- **Allowed Domains**: Outbound and interactive permissions are granted in `~/.gemini/config/config.json` for Microsoft (`developer.microsoft.com`, `graph.microsoft.com`, `*.sharepoint.com`, `*.office.com`, `login.microsoftonline.com`) and REDCap (`redcap.research.sc.edu`, `*.sc.edu`).
- **SSO Handling**: When encountering Microsoft 365 or USC Shibboleth / Duo authentication, prompt the user to complete the 2FA approval interactively in the browser view window. The resulting session will persist in the browser profile.
- **Tokens & Secrets**: Always source secrets from `.env` (`MS_GRAPH_ACCESS_TOKEN`, `REDCAP_API_URL`, and project tokens). Never hardcode secrets in code or git.

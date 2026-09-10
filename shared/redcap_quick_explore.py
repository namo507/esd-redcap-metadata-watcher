"""Interactive & programmatic REDCap quick explorer using requests.post.

Usage:
    python shared/redcap_quick_explore.py --project NANO --content metadata
    python shared/redcap_quick_explore.py --project IPSA --content project
    python shared/redcap_quick_explore.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_TOKENS: Dict[str, str] = {
    "CLEAN_4797": os.getenv("TOKEN_CLEAN_4797", ""),
    "DIRTY_4581": os.getenv("TOKEN_DIRTY_4581", ""),
    "NANO": os.getenv("NANO_API_TOKEN", ""),
    "NICO": os.getenv("NICO_API_TOKEN", ""),
    "IPSA": os.getenv("IPSA_API_TOKEN", ""),
    "ACTION": os.getenv("ACTION_API_TOKEN", ""),
}

REDCAP_URL: str = os.getenv("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")


def query_redcap(
    token: str,
    content: str = "project",
    format_type: str = "json",
    extra_params: Dict[str, Any] | None = None,
) -> Any:
    """Execute a requests.post query against REDCap API."""
    data = {
        "token": token,
        "content": content,
        "format": format_type,
        "returnFormat": "json",
    }
    if extra_params:
        data.update(extra_params)

    response = requests.post(REDCAP_URL, data=data, timeout=30)
    response.raise_for_status()

    if format_type == "json":
        return response.json()
    return response.text


def list_projects() -> None:
    """List all available project tokens and their status."""
    print("=== Configured REDCap Projects ===")
    for name, token in PROJECT_TOKENS.items():
        if not token:
            print(f"  [--] {name:12}: Token missing from .env")
            continue
        try:
            info = query_redcap(token, content="project")
            title = info.get("project_title", "Unknown")
            pid = info.get("project_id", "Unknown")
            print(f"  [OK] {name:12}: PID {pid} — \"{title}\"")
        except Exception as err:
            print(f"  [ERR] {name:12}: {err}")


def main() -> None:
    parser = argparse.ArgumentParser(description="REDCap Quick Explorer")
    parser.add_argument(
        "--list", action="store_true", help="List all configured projects and check connection"
    )
    parser.add_argument(
        "--project",
        choices=list(PROJECT_TOKENS.keys()),
        default="NANO",
        help="Project nickname (default: NANO)",
    )
    parser.add_argument(
        "--content",
        default="project",
        choices=["project", "metadata", "record", "instrument", "fieldNames", "event", "arm"],
        help="REDCap content type to fetch (default: project)",
    )
    parser.add_argument(
        "--records", nargs="*", help="Specific record IDs to fetch (when content=record)"
    )
    parser.add_argument(
        "--fields", nargs="*", help="Specific field names to fetch"
    )
    parser.add_argument(
        "--out", help="Optional output JSON file path"
    )

    args = parser.parse_args()

    if args.list:
        list_projects()
        return

    token = PROJECT_TOKENS.get(args.project)
    if not token:
        print(f"Error: Token for '{args.project}' not found in .env", file=sys.stderr)
        sys.exit(1)

    extra: Dict[str, Any] = {}
    if args.records:
        for idx, rec_id in enumerate(args.records):
            extra[f"records[{idx}]"] = rec_id
    if args.fields:
        for idx, f_name in enumerate(args.fields):
            extra[f"fields[{idx}]"] = f_name

    print(f"Fetching '{args.content}' from {args.project} via {REDCAP_URL}...")
    try:
        result = query_redcap(token, content=args.content, extra_params=extra)
        if args.out:
            with open(args.out, "w") as out_f:
                json.dump(result, out_f, indent=2)
            print(f"Saved result to {args.out}")
        else:
            if isinstance(result, list):
                print(f"Total entries returned: {len(result)}")
                sample = result[:3] if len(result) > 3 else result
                print("Preview:", json.dumps(sample, indent=2))
            else:
                print(json.dumps(result, indent=2))
    except Exception as err:
        print(f"Request failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

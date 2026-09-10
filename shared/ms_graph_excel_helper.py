"""Microsoft Graph Excel helper for OneDrive and SharePoint workbooks.

Usage:
    python shared/ms_graph_excel_helper.py --list-files
    python shared/ms_graph_excel_helper.py --sheets "All_Studies_DOB_GA_Summary.xlsx"
    python shared/ms_graph_excel_helper.py --read "All_Studies_DOB_GA_Summary.xlsx" --sheet "Sheet1" --range "A1:E10"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://graph.microsoft.com/v1.0"


def get_headers() -> Dict[str, str]:
    token = os.getenv("MS_GRAPH_ACCESS_TOKEN")
    if not token:
        print("Error: MS_GRAPH_ACCESS_TOKEN is missing from .env", file=sys.stderr)
        sys.exit(1)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def list_excel_files() -> List[Dict[str, Any]]:
    headers = get_headers()
    url = f"{BASE_URL}/me/drive/root/search(q='.xlsx')"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return items


def find_file_id(file_name: str) -> Optional[str]:
    headers = get_headers()
    url = f"{BASE_URL}/me/drive/root/search(q='{file_name}')"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    for item in items:
        if item.get("name", "").lower() == file_name.lower():
            return item.get("id")
    # Fallback to prefix matching if exact match not found
    for item in items:
        if file_name.lower() in item.get("name", "").lower():
            return item.get("id")
    return None


def get_worksheets(item_id: str) -> List[Dict[str, Any]]:
    headers = get_headers()
    url = f"{BASE_URL}/me/drive/items/{item_id}/workbook/worksheets"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json().get("value", [])


def read_range(item_id: str, sheet_name: str, cell_range: str) -> Dict[str, Any]:
    headers = get_headers()
    url = f"{BASE_URL}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/range(address='{cell_range}')"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


def update_range(item_id: str, sheet_name: str, cell_range: str, values: List[List[Any]]) -> Dict[str, Any]:
    headers = get_headers()
    url = f"{BASE_URL}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/range(address='{cell_range}')"
    body = {"values": values}
    resp = requests.patch(url, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Microsoft Graph Excel Helper")
    parser.add_argument("--list-files", action="store_true", help="List all Excel (.xlsx) workbooks")
    parser.add_argument("--sheets", help="List sheets in specified workbook filename")
    parser.add_argument("--read", help="Workbook filename to read")
    parser.add_argument("--sheet", default="Sheet1", help="Worksheet name (default: Sheet1)")
    parser.add_argument("--range", default="A1:F10", help="Range address (e.g. A1:D10)")

    args = parser.parse_args()

    if args.list_files:
        print("Searching for Excel files on OneDrive / SharePoint...")
        files = list_excel_files()
        print(f"Found {len(files)} Excel workbook(s):")
        for f in files:
            print(f" - {f.get('name')} (ID: {f.get('id')}, Size: {f.get('size')} bytes)")
        return

    if args.sheets:
        file_id = find_file_id(args.sheets)
        if not file_id:
            print(f"Error: Could not find file '{args.sheets}' on OneDrive", file=sys.stderr)
            sys.exit(1)
        sheets = get_worksheets(file_id)
        print(f"Worksheets in '{args.sheets}':")
        for s in sheets:
            print(f" - {s.get('name')} (ID: {s.get('id')}, Visibility: {s.get('visibility')})")
        return

    if args.read:
        file_id = find_file_id(args.read)
        if not file_id:
            print(f"Error: Could not find file '{args.read}' on OneDrive", file=sys.stderr)
            sys.exit(1)
        data = read_range(file_id, args.sheet, args.range)
        values = data.get("values", [])
        print(f"Data from '{args.read}' [{args.sheet}!{args.range}]:")
        for row in values:
            print(row)
        return

    parser.print_help()


if __name__ == "__main__":
    main()

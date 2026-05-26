#!/usr/bin/env python3
"""Bootstrap Open WebUI: create admin account and register Nautobot tool."""

import json
import os
import sys
import time
import requests

OW_URL = os.environ.get("OW_URL", "http://open-webui:8080")
ADMIN_NAME = os.environ.get("OW_ADMIN_NAME", "Admin")
ADMIN_EMAIL = os.environ.get("OW_ADMIN_EMAIL", "admin@webui.local")
ADMIN_PASSWORD = os.environ.get("OW_ADMIN_PASSWORD", "admin123")

TOOL_FILE = os.path.join(os.path.dirname(__file__), "nautobot_ow_tool.py")


def wait_for_ow(max_wait=300):
    print(f"Waiting for Open WebUI at {OW_URL}...")
    for i in range(max_wait // 5):
        try:
            r = requests.get(f"{OW_URL}/health", timeout=5)
            if r.status_code == 200:
                print(f"Open WebUI ready ({i * 5}s)")
                return True
        except Exception:
            pass
        if i > 0 and i % 12 == 0:
            print(f"  ...still waiting ({i * 5}s elapsed)")
        time.sleep(5)
    return False


def get_token():
    # First signup creates the admin account
    r = requests.post(f"{OW_URL}/api/v1/auths/signup", json={
        "name": ADMIN_NAME,
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    }, timeout=10)
    if r.status_code in (200, 201):
        print(f"Admin account created: {ADMIN_EMAIL}")
        return r.json().get("token")

    # Already exists — sign in
    r = requests.post(f"{OW_URL}/api/v1/auths/signin", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    }, timeout=10)
    if r.status_code == 200:
        print(f"Signed in as: {ADMIN_EMAIL}")
        return r.json().get("token")

    print(f"Auth failed: {r.status_code} {r.text[:200]}")
    return None


def register_tool(token):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Check if already registered
    r = requests.get(f"{OW_URL}/api/v1/tools/", headers=headers, timeout=10)
    if r.status_code == 200:
        for tool in r.json():
            if tool.get("id") == "nautobot":
                print("Nautobot tool already registered — skipping")
                return True

    with open(TOOL_FILE) as f:
        tool_code = f.read()

    r = requests.post(f"{OW_URL}/api/v1/tools/create", headers=headers, json={
        "id": "nautobot",
        "name": "Nautobot",
        "meta": {
            "description": "Query Nautobot network inventory — devices, tenants, locations",
            "tags": [{"name": "networking"}, {"name": "nautobot"}],
        },
        "content": tool_code,
    }, timeout=10)

    if r.status_code in (200, 201):
        print("Nautobot tool registered")
        return True

    print(f"Tool registration failed: {r.status_code} {r.text[:300]}")
    return False


def main():
    if not wait_for_ow():
        print("Timed out waiting for Open WebUI")
        sys.exit(1)

    token = get_token()
    if not token:
        sys.exit(1)

    if not register_tool(token):
        sys.exit(1)

    print(f"\nOpen WebUI:  {OW_URL.replace('open-webui', 'localhost').replace(':8080', ':4000')}")
    print(f"Login:       {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print("Nautobot tool is ready to use in any chat.")


if __name__ == "__main__":
    main()

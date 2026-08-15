#!/usr/bin/env python3
"""Blocking proof that the browser used by UI audits can really launch."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from src.browser_qa_runtime import launch_chromium, resolve_browser_executable

with sync_playwright() as playwright:
    browser = launch_chromium(playwright)
    page = browser.new_page()
    page.goto("data:text/html,<title>browser-qa</title>")
    assert page.title() == "browser-qa"
    evidence = {"ok": True, "executable": str(resolve_browser_executable())}
    print(json.dumps(evidence | {"version": browser.version}))
    browser.close()

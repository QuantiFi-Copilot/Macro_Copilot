#!/usr/bin/env python3
"""
find_frames.py  –  just prints the frame tree and whether each frame
                   contains the From-date input.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


# ─── spin up Chrome visibly so you can watch what happens ────────────
opt = Options()
opt.add_argument("--window-size=1280,900")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                          options=opt)

driver.get("https://www.bseindia.com/corporates/xbrldetails.aspx")

driver.implicitly_wait(5)     # tiny, just so the first iframes load

def walk(frame, path=""):
    """
    Recursively print frame → sub-frame → … hierarchy.
    ‘path’ is a breadcrumb string like “0-2-1” (root→3rd→2nd frame).
    """
    driver.switch_to.default_content()
    for idx in map(int, path.split("-")) if path else []:
        driver.switch_to.frame(idx)

    # does THIS frame contain the From-date box?
    found = driver.find_elements(By.XPATH, "//input[contains(@id,'txtFromDate')]")
    flag  = " 🟢  has From-date" if found else ""

    print(f"{'  ' * path.count('-')}- frame {path or 'root'}{flag}")

    # recurse into children
    children = driver.find_elements(By.TAG_NAME, "iframe")
    for i, _ in enumerate(children):
        walk(frame, f"{path}-{i}" if path else f"{i}")

walk(driver, "")
driver.quit()

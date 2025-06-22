"""
Zerodha token-refresh helper
────────────────────────────────────────────────────────────
• Reads secrets from .env (ZERODHA_* or KITE_* names)
• Works for both “External TOTP” and fixed 6-digit PIN pages
• Submits forms via ENTER so it’s immune to button markup changes
• Avoids stale-element errors by sending ENTER to <body>
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Final, Iterable

import pyotp
from dotenv import find_dotenv, load_dotenv, set_key
from kiteconnect import KiteConnect
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ──────────────── 1. credentials ──────────────────────────
dotenv_path = find_dotenv()
if not dotenv_path:
    raise FileNotFoundError(".env not found")
load_dotenv(dotenv_path)

USER_ID: Final[str] = os.getenv("ZERODHA_USER_ID") or os.getenv("KITE_USER_ID")
PASSWORD: Final[str] = os.getenv("ZERODHA_PASSWORD") or os.getenv("KITE_PASSWORD")
TOTP_SECRET: Final[str] = os.getenv("ZERODHA_TOTP_SECRET") or os.getenv(
    "KITE_TOTP_SECRET"
)
API_KEY: Final[str] = os.getenv("KITE_API_KEY")
API_SECRET: Final[str] = os.getenv("KITE_API_SECRET")

for k, v in {
    "USER_ID": USER_ID,
    "PASSWORD": PASSWORD,
    "TOTP_SECRET": TOTP_SECRET,
    "API_KEY": API_KEY,
    "API_SECRET": API_SECRET,
}.items():
    if not v:
        raise RuntimeError(f"Missing {k} in .env")

LOGIN_URL = (
    f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}"
)

# ──────────────── 2. helpers ──────────────────────────────
def generate_totp(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _first_present(driver, timeout: int, locators: Iterable[tuple[By, str]]):
    """Return the first element located by *locators* within *timeout* seconds."""
    end = time.time() + timeout
    while time.time() < end:
        for how, what in locators:
            elems = driver.find_elements(how, what)
            if elems:
                return elems[0]
        time.sleep(0.25)
    raise TimeoutException("2-FA input field not found")


def _slow_type(elem, text: str, delay: float = 0.15):
    for ch in text:
        elem.send_keys(ch)
        time.sleep(delay)


TOTP_LOCATORS = [
    (By.CSS_SELECTOR, "input#pin[maxlength='6']"),
    (By.CSS_SELECTOR, "form input[maxlength='6'][type='text']"),
    (By.CSS_SELECTOR, "form input[maxlength='6'][type='number']"),
    (
        By.XPATH,
        "//input[@maxlength='6' and (self::input[@type='text'] "
        "or self::input[@type='number'])]",
    ),
]

# ──────────────── 3. core flow ────────────────────────────
def get_request_token() -> str:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    opts.binary_location = shutil.which("chromium")
    driver_path = shutil.which("chromedriver")

    driver = webdriver.Chrome(
        service=webdriver.chrome.service.Service(driver_path), options=opts
    )
    driver.get(LOGIN_URL)

    try:
        # 1️⃣  user ID + password
        uid_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "userid"))
        )
        uid_box.clear()
        uid_box.send_keys(USER_ID)

        pwd_box = driver.find_element(By.ID, "password")
        pwd_box.clear()
        pwd_box.send_keys(PASSWORD + Keys.ENTER)

        # 2️⃣  2-FA page (TOTP / PIN)
        totp_input = _first_present(driver, 20, TOTP_LOCATORS)
        _slow_type(totp_input, generate_totp(TOTP_SECRET))

        # the element often becomes stale after 6th digit → send ENTER to <body>
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ENTER)
        except StaleElementReferenceException:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ENTER)

        # optional click if a Continue button appears
        try:
            WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[normalize-space()='Continue' or contains(.,'Continue')]",
                    )
                )
            ).click()
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
            pass

        # 3️⃣  scrape request_token from network logs
        time.sleep(4)
        for entry in driver.get_log("performance"):
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") == "Network.requestWillBeSent":
                url = msg["params"]["request"]["url"]
                if "request_token=" in url:
                    return url.split("request_token=")[1].split("&")[0]

        # fallback: URL bar
        if "request_token=" in driver.current_url:
            return driver.current_url.split("request_token=")[1].split("&")[0]

        raise RuntimeError("request_token not found")

    finally:
        driver.quit()


def refresh_kite_access_token() -> str:
    rk = get_request_token()
    kite = KiteConnect(api_key=API_KEY)
    access_token = kite.generate_session(rk, api_secret=API_SECRET)[
        "access_token"
    ]
    set_key(dotenv_path, "KITE_ACCESS_TOKEN", access_token)
    return access_token


if __name__ == "__main__":
    print("Refreshing Kite access token…")
    print("New KITE_ACCESS_TOKEN:", refresh_kite_access_token())

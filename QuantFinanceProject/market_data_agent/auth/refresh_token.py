# market_data_agent/auth/refresh_token.py

import os
import time
import json
import pyotp
import shutil

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from kiteconnect import KiteConnect
from dotenv import load_dotenv, find_dotenv, set_key

# ─────────────────────────────────────────────────────────────────────────────
# Locate & load your project’s root .env
dotenv_path = find_dotenv()
if not dotenv_path:
    raise FileNotFoundError("Could not find .env in any parent directories.")
load_dotenv(dotenv_path)

# Pull credentials from .env
USER_ID      = os.getenv("ZERODHA_USER_ID")
PASSWORD     = os.getenv("ZERODHA_PASSWORD")
TOTP_SECRET  = os.getenv("ZERODHA_TOTP_SECRET")
API_KEY      = os.getenv("KITE_API_KEY")
API_SECRET   = os.getenv("KITE_API_SECRET")

LOGIN_URL = f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}"

# ─────────────────────────────────────────────────────────────────────────────
def generate_totp(secret: str) -> str:
    """Generate the current 6-digit TOTP from your TOTP_SECRET."""
    return pyotp.TOTP(secret).now()

def get_request_token() -> str:
    """Automate the Zerodha login flow in headless Chrome and extract the request_token."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # uncomment to run headless
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    # Explicitly wire Chromium & chromedriver paths inside Docker
    chrome_options.binary_location = shutil.which("chromium")  # /usr/bin/chromium in image
    driver_path = shutil.which("chromedriver")                 # /usr/local/bin/chromedriver symlink
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(driver_path),
                              options=chrome_options)
    driver.get(LOGIN_URL)

    try:
        # 1) Login form
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "userid"))
        ).send_keys(USER_ID)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        # 2) Wait for TOTP input (same ID, different type)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@id='userid' and @type='number']")
            )
        )
        totp_code = generate_totp(TOTP_SECRET)
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located(
                (By.XPATH, "//input[@id='userid' and @type='number']")
            )
        ).clear()
        driver.find_element(By.XPATH, "//input[@id='userid' and @type='number']").send_keys(totp_code)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        # 3) Give the redirect a moment, then scan performance logs
        time.sleep(4)
        logs = driver.get_log("performance")

        request_token = None
        for entry in logs:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") == "Network.requestWillBeSent":
                url = msg["params"]["request"]["url"]
                if "request_token=" in url:
                    request_token = url.split("request_token=")[1].split("&")[0]
                    break

        # 4) Fallback: check current_url
        if not request_token:
            current_url = driver.current_url
            if "request_token=" in current_url:
                request_token = current_url.split("request_token=")[1].split("&")[0]

        driver.quit()

        if not request_token:
            raise RuntimeError("Could not extract request_token from logs or URL.")
        return request_token

    except Exception:
        driver.quit()
        raise

def refresh_kite_access_token() -> str:
    """
    Full refresh flow:
      1) get_request_token()
      2) exchange via KiteConnect.generate_session()
      3) update .env with new KITE_ACCESS_TOKEN
      4) return the new access_token
    """
    request_token = get_request_token()
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]

    # Persist the new token back into your root .env
    set_key(dotenv_path, "KITE_ACCESS_TOKEN", access_token)

    return access_token

# If someone runs this file directly, refresh and print:
if __name__ == "__main__":
    print("🔄 Refreshing Kite access token…")
    token = refresh_kite_access_token()
    print("✅ New KITE_ACCESS_TOKEN:", token)

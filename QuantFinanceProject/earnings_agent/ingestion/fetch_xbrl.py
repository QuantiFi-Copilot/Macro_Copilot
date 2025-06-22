import time, os, requests
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# your universe
from earnings_agent.config.universe import COMPANIES

# --- Config ---
NSE_BASE = "https://www.nseindia.com"
NSE_URL  = NSE_BASE + "/companies-listing/corporate-filings-financial-results?equityfndatefilter=1"
DOWNLOAD_ROOT = "xbrl_downloads"

def fetch_xbrl_for_company(ticker, company_name, from_date, to_date):
    # 1) Launch Chrome **with UI** (no headless)
    chrome_opts = Options()
    chrome_opts.add_argument("--start-maximized")
    chrome_opts.add_argument(
      "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=chrome_opts)
    wait   = WebDriverWait(driver, 30)

    try:
        driver.get(NSE_URL)

        # 2) Fill in company; wait for the UL#financials_equities_companyName_listbox to appear
        inp = wait.until(EC.element_to_be_clickable(
          (By.ID, "financials_equities_companyName")
        ))
        inp.clear()
        inp.send_keys(company_name)
        time.sleep(1.5)

        # *** Fix: select items from the autocomplete list by CSS selector ***
        suggestions = wait.until(EC.presence_of_all_elements_located((
          By.CSS_SELECTOR, "ul#financials_equities_companyName_listbox li"
        )))
        # pick the one ending with our ticker
        chosen = None
        for li in suggestions:
            if li.text.strip().endswith(ticker):
                chosen = li
                break
        (chosen or suggestions[0]).click()

        # 3) Period → Quarterly
        sel = Select(wait.until(EC.element_to_be_clickable(
          (By.ID, "financials_equities_period")
        )))
        sel.select_by_visible_text("Quarterly")

        # 4) Click Custom
        wait.until(EC.element_to_be_clickable(
          (By.XPATH, "//a[@data-val='Custom' and text()='Custom']")
        )).click()

        # 5) Remove readonly & fill dates
        for css_cls, date_val in [
            ("startDate-block-deals", from_date),
            ("endDate-block-deals",   to_date)
        ]:
            fld = wait.until(EC.presence_of_element_located((
              By.CSS_SELECTOR, f"input.{css_cls}"
            )))
            driver.execute_script("arguments[0].removeAttribute('readonly')", fld)
            fld.clear()
            fld.send_keys(date_val)

        # 6) Click GO
        wait.until(EC.element_to_be_clickable(
          (By.CSS_SELECTOR, "button.filterbtn")
        )).click()

        # 7) Wait for table rows
        wait.until(EC.presence_of_element_located((
          By.CSS_SELECTOR, "table tbody tr"
        )))

        # grab cookies for requests
        sess = requests.Session()
        for ck in driver.get_cookies():
            sess.cookies.set(ck["name"], ck["value"])
        sess.headers.update({"User-Agent": chrome_opts.arguments[0]})

        # 8) Iterate rows & download XMLs
        quarter_map = {
          "First Quarter":  "Q1",
          "Second Quarter": "Q2",
          "Third Quarter":  "Q3",
          "Fourth Quarter": "Q4"
        }
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        for r in rows:
            # find the XML icon → its parent <a>
            elems = r.find_elements(
              By.XPATH,
              ".//img[contains(@src,'icon-xml.svg')]/ancestor::a[1]"
            )
            if not elems:
                continue
            xml_href = elems[0].get_attribute("href")
            # parse relatingTo & periodEnded columns (#8 and #7)
            relating = r.find_element(By.XPATH, ".//td[8]").text.strip()
            period   = r.find_element(By.XPATH, ".//td[7]").text.strip()  # e.g. "31-Dec-2024"
            year = period.split("-")[-1]
            q    = quarter_map.get(relating, "Q?")

            # prepare folder & filename
            out_dir = Path(DOWNLOAD_ROOT) / ticker / f"{q}-{year}"
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{ticker}_{q}{year}.xml"
            out_path = out_dir / fname

            # download via requests (bypass browser download dialog)
            resp = sess.get(xml_href, timeout=15)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
            print("✔️ Downloaded:", out_path)

    finally:
        driver.quit()


if __name__ == "__main__":
    if not COMPANIES:
        raise RuntimeError("Populate COMPANIES in universe.py first")
    cfg = COMPANIES[0]
    fetch_xbrl_for_company(
      ticker       = cfg["ticker"],
      company_name = cfg["name"],
      from_date    = "15-03-2025",
      to_date      = "15-06-2025"
    )

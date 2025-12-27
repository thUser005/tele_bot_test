import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime

# =====================================================
# CONFIG
# =====================================================
# EXPIRY_DATE = "2026-01-06"     # YYYY-MM-DD
# UNDERLYING = "NIFTY"
def get_data_fun(EXPIRY_DATE,UNDERLYING):
        

    # =====================================================
    # HEADERS
    # =====================================================
    HEADERS_HTML = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    HEADERS_API = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "x-app-id": "growwWeb",
        "x-device-type": "desktop",
        "x-platform": "web",
        "x-device-id": "ecdeabb2-80c4-5fea-9ffc-5b54c2d8abe5",
    }

    # =====================================================
    # HELPERS
    # =====================================================
    def is_weekly_expiry(expiry_date: str) -> bool:
        """
        Weekly expiry = NOT last Thursday of month
        """
        dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        return dt.weekday() != 3   # Thursday = 3


    def build_expiry_code(expiry_date: str) -> str:
        dt = datetime.strptime(expiry_date, "%Y-%m-%d")

        if is_weekly_expiry(expiry_date):
            # WEEKLY FORMAT → YYMDD  (month without leading zero)
            yy = dt.strftime("%y")
            m = str(int(dt.strftime("%m")))
            dd = dt.strftime("%d")
            return f"{yy}{m}{dd}"
        else:
            # MONTHLY FORMAT → YYMON
            return dt.strftime("%y%b").upper()


    def normalize_strike(text: str) -> str:
        return text.replace(",", "")


    def build_symbol(underlying, expiry_code, strike, opt_type):
        return f"{underlying}{expiry_code}{strike}{opt_type}"


    # =====================================================
    # STEP 1: FETCH HTML
    # =====================================================
    html_url = f"https://groww.in/options/nifty?expiry={EXPIRY_DATE}"

    resp = requests.get(html_url, headers=HEADERS_HTML, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    texts = [i.get_text(strip=True) for i in soup.select(".bodyBaseHeavy")]

    # =====================================================
    # STEP 2: EXTRACT STRIKES
    # =====================================================
    strike_texts = [
        t for t in texts
        if re.fullmatch(r"\d{1,3}(,\d{3})*", t)
    ]

    if not strike_texts:
        raise RuntimeError("No strikes found — page structure may have changed")

    strikes = sorted(set(normalize_strike(s) for s in strike_texts), key=int)


    # =====================================================
    # STEP 3: BUILD OPTION SYMBOLS (CORRECT FORMAT)
    # =====================================================
    expiry_code = build_expiry_code(EXPIRY_DATE)

    symbols = []
    for strike in strikes:
        symbols.append(build_symbol(UNDERLYING, expiry_code, strike, "CE"))
        symbols.append(build_symbol(UNDERLYING, expiry_code, strike, "PE"))


    # =====================================================
    # STEP 4: FETCH LIVE PRICES
    # =====================================================
    api_url = (
        "https://groww.in/v1/api/stocks_fo_data/v1/"
        "tr_live_prices/exchange/NSE/segment/FNO/latest_prices_batch"
    )

    HEADERS_API["referer"] = html_url


    response = requests.post(
        api_url,
        headers=HEADERS_API,
        json=symbols,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()
    return data
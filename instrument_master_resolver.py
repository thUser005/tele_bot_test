import gzip
import json
import requests
import os
from datetime import datetime
import pytz

# =================================================
# CONFIG
# =================================================
COMPLETE_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)

CACHE_DIR = "instrument_cache"
IST = pytz.timezone("Asia/Kolkata")


# =================================================
# 1. Get today's cache file path
# =================================================
def get_today_cache_path():
    today = datetime.now(IST).strftime("%Y-%m-%d")
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"complete_{today}.json")


# =================================================
# 2. Download + cache instrument master (daily)
# =================================================
def load_complete_instruments():
    cache_file = get_today_cache_path()

    # ✅ Use cached file if exists
    if os.path.exists(cache_file):
        print(f"📂 Using cached instrument master: {cache_file}")
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # ⬇️ Download if not cached
    print("⬇️ Downloading COMPLETE instrument master (first run today)...")

    resp = requests.get(COMPLETE_MASTER_URL, timeout=60)
    resp.raise_for_status()

    decompressed = gzip.decompress(resp.content)
    data = json.loads(decompressed.decode("utf-8"))

    # 💾 Save cache
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    print(f"✅ Cached {len(data)} instruments → {cache_file}")
    return data


# =================================================
# 3. Normalize user input to trading symbol
# =================================================
def build_trading_symbol(index, expiry_dd_mon, strike, option_type):
    """
    Input:
        SENSEX, 08 JAN, 86000, PE
    Output:
        SENSEX 86000 PE 08 JAN 26
    """
    day, month = expiry_dd_mon.split()
    year = datetime.now(IST).strftime("%y")

    return f"{index} {strike} {option_type} {day} {month} {year}".upper()


# =================================================
# 4. Resolve option from cached master
# =================================================
def resolve_option_from_master(index, expiry, strike, option_type):
    instruments = load_complete_instruments()

    expected_symbol = build_trading_symbol(
        index=index,
        expiry_dd_mon=expiry,
        strike=strike,
        option_type=option_type
    )

    print(f"🔍 Searching for: {expected_symbol}")

    for inst in instruments:
        if (
            inst.get("trading_symbol", "").upper() == expected_symbol
            and inst.get("instrument_type") == option_type
            and inst.get("asset_symbol") == index
        ):
            return inst

    return None

# =================================================
# 6. Public verification helper (used by bot)
# =================================================
def ensure_instrument_master_ready():
    """
    Ensures today's instrument master is available.
    Downloads if missing.
    """
    try:
        load_complete_instruments()
        return True
    except Exception as e:
        print("❌ Failed to prepare instrument master")
        print(str(e))
        return False

# # =================================================
# # 5. Test run
# # =================================================
# if __name__ == "__main__":
#     symbol_data = resolve_option_from_master(
#         index="SENSEX",
#         expiry="08 JAN",
#         strike=86000,
#         option_type="PE"
#     )

#     if symbol_data:
#         print("✅ FOUND FULL OPTION DATA")
#         print(json.dumps(symbol_data, indent=2, ensure_ascii=False))
#     else:
#         print("❌ Instrument not found")

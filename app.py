import os
import time
import threading
import requests
import logging
from datetime import datetime, timedelta, timezone, time as dtime
from pymongo import MongoClient
from flask import Flask, jsonify, render_template
from concurrent.futures import ThreadPoolExecutor, as_completed

# =====================================================
# LOGGING
# =====================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(threadName)s | %(message)s",
)
logger = logging.getLogger("BUY_MONITOR")

# =====================================================
# CONFIG
# =====================================================
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("❌ MONGO_URI not set")

DB = "trading"
COL = "daily_signals"

CAPITAL = 20_000
MARGIN = 5

INTERVAL_SECONDS = 3
MAX_WORKERS = 10
MAX_RETRIES = 3

IST = timezone(timedelta(hours=5, minutes=30))

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

GROWW_URL = (
    "https://groww.in/v1/api/charting_service/v2/chart/"
    "delayed/exchange/NSE/segment/CASH"
)

# =====================================================
# INIT
# =====================================================
app = Flask(__name__)
client = MongoClient(MONGO_URI)
collection = client[DB][COL]

live_table = {}
trade_state = {}
lock = threading.Lock()

# =====================================================
# UTILS
# =====================================================
def today():
    return datetime.now(IST).strftime("%Y-%m-%d")

def now_ms():
    return int(datetime.now(IST).timestamp() * 1000)

def now_str():
    return datetime.now(IST).strftime("%H:%M:%S")

def is_market_open():
    now = datetime.now(IST).time()
    return MARKET_OPEN <= now <= MARKET_CLOSE

def clear_live_data():
    with lock:
        live_table.clear()
        trade_state.clear()
    logger.info("🧹 Cleared live_table & trade_state")

def normalize_symbol(symbol: str) -> str:
    clean = (
        symbol
        .replace("NSE:", "")
        .replace(".NS", "")
        .replace("-EQ", "")
        .strip()
        .upper()
    )
    if clean != symbol:
        logger.debug(f"Symbol normalized: {symbol} → {clean}")
    return clean

# =====================================================
# FETCH LTP (DETAILED LOGGING)
# =====================================================
def fetch_ltp_with_retry(symbol):
    symbol = normalize_symbol(symbol)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            end = now_ms()
            start = end - 5 * 60 * 1000

            logger.debug(
                f"📡 Groww API CALL | {symbol} | Attempt {attempt}"
            )

            r = requests.get(
                f"{GROWW_URL}/{symbol}",
                params={
                    "startTimeInMillis": start,
                    "endTimeInMillis": end,
                    "intervalInMinutes": 3
                },
                timeout=5
            )

            logger.debug(
                f"HTTP {r.status_code} | {symbol}"
            )

            r.raise_for_status()
            data = r.json()

            candles = data.get("candles", [])

            if not candles:
                logger.warning(
                    f"⚠️ No candles returned | {symbol}"
                )
                return None

            last = candles[-1]
            ltp = last[4]

            if ltp is None or ltp <= 0:
                logger.warning(
                    f"⚠️ Invalid LTP {ltp} | {symbol}"
                )
                return None

            logger.debug(
                f"✅ LTP {symbol} = {ltp}"
            )
            return round(ltp, 2)

        except requests.Timeout:
            logger.warning(
                f"⏱ Timeout | {symbol} | Attempt {attempt}"
            )

        except requests.HTTPError as e:
            logger.error(
                f"❌ HTTP Error | {symbol} | {e}"
            )
            return None

        except Exception as e:
            logger.exception(
                f"🔥 Unexpected error | {symbol}"
            )

        time.sleep(0.4)

    logger.error(f"❌ LTP FAILED after retries | {symbol}")
    return None

# =====================================================
# PROCESS SYMBOL
# =====================================================
def process_symbol(signal):
    symbol = normalize_symbol(signal["symbol"])
    entry = signal["entry"]
    target = signal["target"]
    stoploss = signal["stoploss"]
    qty = signal["qty"]

    logger.debug(f"🔄 Processing {symbol}")

    ltp = fetch_ltp_with_retry(symbol)
    if ltp is None:
        logger.warning(f"⛔ Skipping {symbol} — LTP unavailable")
        return None

    with lock:
        state = trade_state.get(symbol, {
            "status": "PENDING",
            "entry_time": None,
            "exit_time": None,
            "exit_price": None
        })

        if state["status"] == "PENDING" and ltp >= entry:
            state["status"] = "ENTERED"
            state["entry_time"] = now_str()
            logger.info(f"🟢 ENTERED | {symbol} @ {ltp}")

        elif state["status"] == "ENTERED" and ltp >= target:
            state["status"] = "EXITED_TARGET"
            state["exit_time"] = now_str()
            state["exit_price"] = target
            logger.info(f"🎯 TARGET HIT | {symbol}")

        elif state["status"] == "ENTERED" and ltp <= stoploss:
            state["status"] = "EXITED_SL"
            state["exit_time"] = now_str()
            state["exit_price"] = stoploss
            logger.info(f"🔴 SL HIT | {symbol}")

        trade_state[symbol] = state

    effective_price = (
        state["exit_price"]
        if state["status"].startswith("EXITED")
        else ltp
    )

    pnl_per_share = round(effective_price - entry, 2)
    pnl_pct = round((pnl_per_share / entry) * 100, 2)
    capital_used = round(entry * qty, 2)
    pnl_capital = round(pnl_per_share * qty, 2)

    return {
        "symbol": symbol,
        "entry": entry,
        "ltp": ltp,
        "status": state["status"],
        "entry_time": state["entry_time"],
        "exit_price": state["exit_price"],
        "exit_time": state["exit_time"],
        "one_share_value": entry,
        "qty": qty,
        "capital_used": capital_used,
        "margin_required": round(capital_used / MARGIN, 2),
        "pnl_pct": pnl_pct,
        "pnl_capital": pnl_capital,
        "pnl_margin": pnl_capital,
        "updated_at": now_str()
    }

# =====================================================
# BACKGROUND MONITOR
# =====================================================
def monitor_worker():
    logger.info("🚀 Monitor thread started")

    while True:
        try:
            if not is_market_open():
                logger.info("⏸ Market closed — sleeping")
                clear_live_data()
                time.sleep(30)
                continue

            doc = collection.find_one({"trade_date": today()})
            if not doc or not doc.get("buy_signals"):
                logger.info("⌛ BUY signals not found")
                clear_live_data()
                time.sleep(5)
                continue

            signals = doc["buy_signals"]
            logger.info(f"📥 Loaded {len(signals)} BUY signals")

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_symbol, s) for s in signals]

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        with lock:
                            live_table[result["symbol"]] = result

            logger.debug(f"📊 Live table size: {len(live_table)}")

        except Exception:
            logger.exception("🔥 Monitor loop crashed")

        time.sleep(INTERVAL_SECONDS)

# =====================================================
# API
# =====================================================
@app.route("/api/monitor")
def api_monitor():
    logger.debug("🌐 /api/monitor called")

    current_time = now_str()

    if not is_market_open():
        logger.info("Market closed API response")
        clear_live_data()
        return f"Present time: {current_time} — Market closed", 200

    doc = collection.find_one({"trade_date": today()})
    if not doc or not doc.get("buy_signals"):
        logger.info("No BUY signals API response")
        clear_live_data()
        return f"Present time: {current_time} — BUY signals not yet saved", 200

    with lock:
        if not live_table:
            logger.info("Waiting for live prices API response")
            return f"Present time: {current_time} — BUY signals loaded, waiting for live prices", 200

        logger.debug("Returning live table JSON")
        return jsonify(list(live_table.values()))

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/")
def index():
    logger.debug("Dashboard loaded")
    return render_template("index.html")

# =====================================================
# START
# =====================================================
if __name__ == "__main__":
    threading.Thread(target=monitor_worker, daemon=True).start()
    logger.info("🖥 Flask server started on :8000")
    app.run(port=8000, debug=False)

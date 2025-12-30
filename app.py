import os
import time
import threading
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
MAX_WORKERS = 20

IST = timezone(timedelta(hours=5, minutes=30))

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# =====================================================
# INIT
# =====================================================
app = Flask(__name__)

client = MongoClient(MONGO_URI)
collection = client[DB][COL]

live_table = {}
trade_state = {}
lock = threading.Lock()

monitor_started = False

# =====================================================
# UTILS
# =====================================================
def today():
    return datetime.now(IST).strftime("%Y-%m-%d")

def now_str():
    return datetime.now(IST).strftime("%H:%M:%S")

def is_market_open():
    return MARKET_OPEN <= datetime.now(IST).time() <= MARKET_CLOSE

def clear_live_data():
    with lock:
        live_table.clear()
        trade_state.clear()
    logger.info("🧹 Cleared live_table & trade_state")

def normalize_symbol(symbol: str) -> str:
    return (
        symbol.replace("NSE:", "")
        .replace(".NS", "")
        .replace("-EQ", "")
        .strip()
        .upper()
    )

# =====================================================
# PROCESS SYMBOL (NO LIVE FETCH)
# =====================================================
def process_symbol(signal):
    symbol = normalize_symbol(signal["symbol"])

    entry = signal["entry"]
    target = signal["target"]
    stoploss = signal["stoploss"]
    qty = signal["qty"]

    # 🚫 NO LIVE FETCH — fallback to entry
    ltp = signal.get("ltp") or entry

    with lock:
        state = trade_state.get(symbol, {
            "status": "PENDING",
            "entry_time": None,
            "exit_time": None,
            "exit_price": None
        })

        # 🚫 NO AUTO TRANSITIONS (Railway is view-only)
        trade_state[symbol] = state

    effective_price = (
        state["exit_price"]
        if state["status"].startswith("EXITED")
        else ltp
    )

    pnl = round((effective_price - entry) * qty, 2)
    pnl_pct = round((effective_price - entry) / entry * 100, 2)
    capital_used = round(entry * qty, 2)

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
        "pnl_capital": pnl,
        "pnl_margin": pnl,
        "updated_at": now_str()
    }

# =====================================================
# BACKGROUND MONITOR (NO EXTERNAL CALLS)
# =====================================================
def monitor_worker():
    logger.info("🚀 Monitor thread started (NO LIVE FETCH)")

    while True:
        try:
            if not is_market_open():
                clear_live_data()
                time.sleep(30)
                continue

            doc = collection.find_one({"trade_date": today()})
            if not doc or not doc.get("buy_signals"):
                clear_live_data()
                time.sleep(5)
                continue

            signals = doc["buy_signals"]

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [
                    executor.submit(process_symbol, s)
                    for s in signals
                ]

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        with lock:
                            live_table[result["symbol"]] = result

        except Exception:
            logger.exception("🔥 Monitor crashed")

        time.sleep(INTERVAL_SECONDS)

# =====================================================
# START BACKGROUND MONITOR
# =====================================================
def start_background_monitor_once():
    global monitor_started
    with lock:
        if monitor_started:
            return
        monitor_started = True

    logger.info("🚀 Starting background monitor (Railway-safe)")
    threading.Thread(
        target=monitor_worker,
        daemon=True,
        name="MonitorThread"
    ).start()

start_background_monitor_once()

# =====================================================
# API
# =====================================================
@app.route("/api/monitor")
def api_monitor():
    current_time = now_str()

    if not is_market_open():
        clear_live_data()
        return f"Present time: {current_time} — Market closed", 200

    doc = collection.find_one({"trade_date": today()})
    if not doc or not doc.get("buy_signals"):
        clear_live_data()
        return f"Present time: {current_time} — BUY signals not yet saved", 200

    with lock:
        if not live_table:
            return (
                f"Present time: {current_time} — "
                "BUY signals loaded, waiting for engine data",
                200
            )

        return jsonify(list(live_table.values()))

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/")
def index():
    return render_template("index.html")

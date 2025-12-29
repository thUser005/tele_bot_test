import os
import time
import threading
import requests
from datetime import datetime, timedelta, timezone, time as dtime
from pymongo import MongoClient
from flask import Flask, jsonify, render_template
from concurrent.futures import ThreadPoolExecutor, as_completed

# =====================================================
# CONFIG
# =====================================================
MONGO_URI = os.getenv("MONGO_URI")
DB = "trading"
COL = "daily_signals"

CAPITAL = 20_000
MARGIN = 5

INTERVAL_SECONDS = 3
MAX_WORKERS = 15
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

# =====================================================
# FETCH LTP WITH RETRY
# =====================================================
def fetch_ltp_with_retry(symbol):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            end = now_ms()
            start = end - 3 * 60 * 1000

            r = requests.get(
                f"{GROWW_URL}/{symbol}",
                params={
                    "startTimeInMillis": start,
                    "endTimeInMillis": end,
                    "intervalInMinutes": 1
                },
                timeout=5
            )
            r.raise_for_status()

            candles = r.json().get("candles", [])
            if candles:
                return candles[-1][4]

        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(0.4)

    return None

# =====================================================
# PROCESS SINGLE STOCK (STATE MACHINE)
# =====================================================
def process_symbol(signal):
    symbol = signal["symbol"]
    entry = signal["entry"]
    target = signal["target"]
    stoploss = signal["stoploss"]
    qty = signal["qty"]

    ltp = fetch_ltp_with_retry(symbol)
    if ltp is None:
        return None

    state = trade_state.get(symbol, {
        "status": "PENDING",
        "entry_time": None,
        "exit_time": None,
        "exit_price": None
    })

    if state["status"] == "PENDING" and ltp >= entry:
        state["status"] = "ENTERED"
        state["entry_time"] = now_str()

    if state["status"] == "ENTERED" and ltp >= target:
        state["status"] = "EXITED_TARGET"
        state["exit_time"] = now_str()
        state["exit_price"] = target

    if state["status"] == "ENTERED" and ltp <= stoploss:
        state["status"] = "EXITED_SL"
        state["exit_time"] = now_str()
        state["exit_price"] = stoploss

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
    pnl_margin = round(pnl_capital * MARGIN, 2)

    return {
        "symbol": symbol,
        "entry": entry,
        "ltp": round(ltp, 2),

        "status": state["status"],
        "entry_time": state["entry_time"],
        "exit_price": state["exit_price"],
        "exit_time": state["exit_time"],

        "one_share_value": entry,
        "qty": qty,
        "capital_used": capital_used,
        "margin_required": round(capital_used / MARGIN, 2),

        "pnl_pct": pnl_pct,
        "pnl_1_share": pnl_per_share,
        "pnl_capital": pnl_capital,
        "pnl_margin": pnl_margin,

        "updated_at": now_str()
    }

# =====================================================
# BACKGROUND MONITOR
# =====================================================
def monitor_worker():
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

        except Exception as e:
            print("Monitor loop error:", e)

        time.sleep(INTERVAL_SECONDS)

# =====================================================
# API
# =====================================================
@app.route("/api/monitor")
def api_monitor():
    current_time = now_str()

    if not is_market_open():
        clear_live_data()
        return (
            f"Present time: {current_time} — Market closed (09:15–15:30 IST)",
            200,
            {"Content-Type": "text/plain; charset=utf-8"}
        )

    doc = collection.find_one({"trade_date": today()})
    if not doc or not doc.get("buy_signals"):
        clear_live_data()
        return (
            f"Present time: {current_time} — BUY signals not yet saved",
            200,
            {"Content-Type": "text/plain; charset=utf-8"}
        )

    with lock:
        if not live_table:
            return (
                f"Present time: {current_time} — BUY signals loaded, waiting for live prices",
                200,
                {"Content-Type": "text/plain; charset=utf-8"}
            )

        return jsonify(list(live_table.values()))

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/")
def index():
    return render_template("index.html")

# =====================================================
# START
# =====================================================
if __name__ == "__main__":
    threading.Thread(target=monitor_worker, daemon=True).start()
    app.run(port=8000)

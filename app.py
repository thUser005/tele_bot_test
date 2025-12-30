# import os
# import time
# import threading
# import logging
# import requests
# from datetime import datetime, timedelta, timezone, time as dtime
# from pymongo import MongoClient
# from flask import Flask, jsonify, render_template
# from concurrent.futures import ThreadPoolExecutor, as_completed

# # =====================================================
# # LOGGING
# # =====================================================
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s | %(levelname)-7s | %(threadName)s | %(message)s",
# )
# logger = logging.getLogger("BUY_MONITOR")

# # =====================================================
# # CONFIG
# # =====================================================
# MONGO_URI = os.getenv("MONGO_URI")
# if not MONGO_URI:
#     raise RuntimeError("❌ MONGO_URI not set")

# DB = "trading"
# COL = "daily_signals"

# CAPITAL = 20_000
# MARGIN = 5

# INTERVAL_SECONDS = 3
# MAX_WORKERS = 20

# IST = timezone(timedelta(hours=5, minutes=30))

# MARKET_OPEN = dtime(9, 15)
# MARKET_CLOSE = dtime(15, 30)

# # ---- COLAB ENGINE (fallback) ----
# ENGINE_BASE_URL = os.getenv(
#     "ENGINE_BASE_URL",
#     "https://visiting-clone-bottom-tanks.trycloudflare.com"
# )

# ENGINE_TIMEOUT = 6  # seconds

# # =====================================================
# # INIT
# # =====================================================
# app = Flask(__name__)

# client = MongoClient(MONGO_URI)
# collection = client[DB][COL]

# live_table = {}
# trade_state = {}
# lock = threading.Lock()

# monitor_started = False

# # =====================================================
# # UTILS
# # =====================================================
# def today():
#     return datetime.now(IST).strftime("%Y-%m-%d")

# def now_str():
#     return datetime.now(IST).strftime("%H:%M:%S")

# def is_market_open():
#     return MARKET_OPEN <= datetime.now(IST).time() <= MARKET_CLOSE

# def clear_live_data():
#     with lock:
#         live_table.clear()
#         trade_state.clear()
#     logger.info("🧹 Cleared live_table & trade_state")

# def normalize_symbol(symbol: str) -> str:
#     return (
#         symbol.replace("NSE:", "")
#         .replace(".NS", "")
#         .replace("-EQ", "")
#         .strip()
#         .upper()
#     )

# # =====================================================
# # COLAB ENGINE FETCH (NEW)
# # =====================================================
# def fetch_from_engine():
#     """
#     Calls Colab engine as fallback
#     """
#     logger.info("🌐 Fetching data from Colab engine")

#     try:
#         r = requests.get(
#             f"{ENGINE_BASE_URL}/engine/health",
#             timeout=ENGINE_TIMEOUT
#         )
#         r.raise_for_status()
#         logger.info("✅ Engine health OK")
#     except Exception as e:
#         logger.warning(f"❌ Engine health failed: {e}")
#         return None

#     # NOTE:
#     # This engine does candle fetch, not BUY signals.
#     # Here we just prove connectivity.
#     return []

# # =====================================================
# # PROCESS SYMBOL (NO LIVE FETCH)
# # =====================================================
# def process_symbol(signal):
#     symbol = normalize_symbol(signal["symbol"])

#     entry = signal["entry"]
#     target = signal["target"]
#     stoploss = signal["stoploss"]
#     qty = signal["qty"]

#     ltp = signal.get("ltp") or entry

#     with lock:
#         state = trade_state.get(symbol, {
#             "status": "PENDING",
#             "entry_time": None,
#             "exit_time": None,
#             "exit_price": None
#         })
#         trade_state[symbol] = state

#     effective_price = (
#         state["exit_price"]
#         if state["status"].startswith("EXITED")
#         else ltp
#     )

#     pnl = round((effective_price - entry) * qty, 2)
#     pnl_pct = round((effective_price - entry) / entry * 100, 2)
#     capital_used = round(entry * qty, 2)

#     return {
#         "symbol": symbol,
#         "entry": entry,
#         "ltp": ltp,
#         "status": state["status"],
#         "entry_time": state["entry_time"],
#         "exit_price": state["exit_price"],
#         "exit_time": state["exit_time"],
#         "one_share_value": entry,
#         "qty": qty,
#         "capital_used": capital_used,
#         "margin_required": round(capital_used / MARGIN, 2),
#         "pnl_pct": pnl_pct,
#         "pnl_capital": pnl,
#         "pnl_margin": pnl,
#         "updated_at": now_str()
#     }

# # =====================================================
# # BACKGROUND MONITOR
# # =====================================================
# def monitor_worker():
#     logger.info("🚀 Monitor thread started (NO LIVE FETCH)")

#     while True:
#         try:
#             if not is_market_open():
#                 clear_live_data()
#                 time.sleep(30)
#                 continue

#             doc = collection.find_one({"trade_date": today()})

#             if not doc or not doc.get("buy_signals"):
#                 logger.info("ℹ️ No BUY signals in Mongo — skipping local processing")
#                 time.sleep(5)
#                 continue

#             signals = doc["buy_signals"]

#             with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
#                 futures = [
#                     executor.submit(process_symbol, s)
#                     for s in signals
#                 ]

#                 for future in as_completed(futures):
#                     result = future.result()
#                     if result:
#                         with lock:
#                             live_table[result["symbol"]] = result

#         except Exception:
#             logger.exception("🔥 Monitor crashed")

#         time.sleep(INTERVAL_SECONDS)

# # =====================================================
# # START BACKGROUND MONITOR
# # =====================================================
# def start_background_monitor_once():
#     global monitor_started
#     with lock:
#         if monitor_started:
#             return
#         monitor_started = True

#     logger.info("🚀 Starting background monitor (Railway-safe)")
#     threading.Thread(
#         target=monitor_worker,
#         daemon=True,
#         name="MonitorThread"
#     ).start()

# start_background_monitor_once()

# # =====================================================
# # API
# # =====================================================
# @app.route("/api/monitor")
# def api_monitor():
#     current_time = now_str()
#     logger.info("📡 /api/monitor called")

#     if not is_market_open():
#         clear_live_data()
#         return f"Present time: {current_time} — Market closed", 200

#     doc = collection.find_one({"trade_date": today()})

#     # -------- PRIMARY: Mongo + local table --------
#     if doc and doc.get("buy_signals"):
#         with lock:
#             if live_table:
#                 logger.info("✅ Serving data from Railway local state")
#                 return jsonify(list(live_table.values()))

#             logger.info("⏳ BUY signals present, waiting for engine data")
#             return (
#                 f"Present time: {current_time} — "
#                 "BUY signals loaded, waiting for engine data",
#                 200
#             )

#     # -------- FALLBACK: Colab engine --------
#     logger.info("⚠️ No BUY signals — falling back to Colab engine")
#     engine_data = fetch_from_engine()

#     if engine_data is None:
#         return f"Present time: {current_time} — Engine unreachable", 200

#     return f"Present time: {current_time} — Engine connected (no BUY data)", 200

# # =====================================================
# # DASHBOARD
# # =====================================================
# @app.route("/")
# def index():
#     return render_template("index.html")

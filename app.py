import os
import json
import time
import threading
import traceback
import requests
from datetime import datetime, timedelta, timezone

from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from telegram.utils.request import Request

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN not set")

CAPITAL = 20_000
RISK_PCT = 0.01
RISK_AMOUNT = CAPITAL * RISK_PCT

ALERT_INTERVAL_SECONDS = 60 * 60

IST = timezone(timedelta(hours=5, minutes=30))

# =====================================================
# LOT SIZES
# =====================================================
LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
    "BANKEX": 15,
}

# =====================================================
# GROWW CHART API
# =====================================================
BASE_CHART_URL = (
    "https://groww.in/v1/api/stocks_fo_data/v1/"
    "charting_service/delayed/chart"
)

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "x-app-id": "growwWeb",
    "x-device-type": "charts",
    "x-platform": "web",
}


def now_millis():
    return int(datetime.now(IST).timestamp() * 1000)


def detect_underlying(symbol: str):
    for key in LOT_SIZES:
        if symbol.startswith(key):
            return key
    return "NIFTY"


def fetch_option_ltp(symbol: str, exchange="NSE"):
    end_ms = now_millis()
    start_ms = end_ms - (5 * 60 * 1000)

    url = (
        f"{BASE_CHART_URL}/exchange/{exchange}/segment/FNO/{symbol}"
        f"?startTimeInMillis={start_ms}"
        f"&endTimeInMillis={end_ms}"
        f"&intervalInMinutes=1"
    )

    r = requests.get(url, headers=HEADERS, timeout=5)
    r.raise_for_status()
    data = r.json()

    candles = data.get("candles", [])
    if not candles:
        return None

    return candles[-1][4]  # close price


# =====================================================
# TELEGRAM BOT
# =====================================================
tg_request = Request(con_pool_size=8, connect_timeout=5, read_timeout=5)
bot = Bot(token=TOKEN, request=tg_request)

app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

# =====================================================
# STATE
# =====================================================
user_state = {}
price_watchers = {}

# =====================================================
# SAFE SEND
# =====================================================
def safe_send(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        print("⚠️ Telegram error:", e)


# =====================================================
# PRICE MONITOR WORKER
# =====================================================
def price_monitor_worker(chat_id, user_id, symbol, entry_price):
    try:
        underlying = detect_underlying(symbol)
        lot_size = LOT_SIZES[underlying]
        exchange = "BSE" if underlying in ("SENSEX", "BANKEX") else "NSE"

        safe_send(
            chat_id,
            f"📡 Monitoring `{symbol}`\nEntry: {entry_price}",
            parse_mode="Markdown"
        )

        while not price_watchers[user_id].is_set():
            ltp = fetch_option_ltp(symbol, exchange)

            if ltp is None:
                time.sleep(2)
                continue

            if ltp >= entry_price:
                risk_per_lot = ltp * lot_size
                max_risk_lots = int(RISK_AMOUNT // risk_per_lot)
                max_capital_lots = int(CAPITAL // (ltp * lot_size))
                final_lots = max(0, min(max_risk_lots, max_capital_lots))

                safe_send(
                    chat_id,
                    f"🚨 *ENTRY HIT*\n\n"
                    f"Symbol: `{symbol}`\n"
                    f"Underlying: {underlying}\n"
                    f"LTP: ₹{ltp:.2f}\n"
                    f"Lot Size: {lot_size}\n\n"
                    f"💰 Capital: ₹{CAPITAL}\n"
                    f"🛑 Risk (1%): ₹{RISK_AMOUNT}\n\n"
                    f"Risk / Lot: ₹{risk_per_lot:.2f}\n"
                    f"Max Risk Lots: {max_risk_lots}\n"
                    f"Max Capital Lots: {max_capital_lots}\n\n"
                    f"✅ *Final Tradable Lots: {final_lots}*",
                    parse_mode="Markdown"
                )
                break

            time.sleep(2)

    except Exception as e:
        safe_send(chat_id, f"⚠️ Monitor error: {e}")

    finally:
        price_watchers[user_id].set()
        del price_watchers[user_id]


# =====================================================
# /start
# =====================================================
def start(update, context):
    user_id = update.effective_user.id
    user_state[user_id] = {"mode": None}

    safe_send(
        update.effective_chat.id,
        "👋 *Welcome*\n\n"
        "1️⃣ Option Chain by Date\n"
        "2️⃣ Add Two Numbers\n"
        "3️⃣ Bot Health Alerts\n"
        "4️⃣ Monitor Option Price\n\n"
        "Reply with *1–4*",
        parse_mode="Markdown"
    )


dispatcher.add_handler(CommandHandler("start", start))


# =====================================================
# MESSAGE HANDLER
# =====================================================
def handle_message(update, context):
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text.strip().upper()

        if user_id not in user_state:
            safe_send(chat_id, "Type /start")
            return

        state = user_state[user_id]

        # MENU
        if state["mode"] is None:
            if text == "4":
                state["mode"] = "MONITOR"
                state["step"] = 1
                safe_send(chat_id, "Send:\n`OPTION NIFTY25DEC25950CE`", parse_mode="Markdown")
                return

            safe_send(chat_id, "Select option 4 for monitoring")
            return

        # MONITOR MODE
        if state["mode"] == "MONITOR":
            if state["step"] == 1:
                if not text.startswith("OPTION"):
                    safe_send(chat_id, "Use:\nOPTION SYMBOL")
                    return
                state["symbol"] = text.split()[1]
                state["step"] = 2
                safe_send(chat_id, "Send:\n`PRICE 120.5`", parse_mode="Markdown")
                return

            if state["step"] == 2:
                if not text.startswith("PRICE"):
                    safe_send(chat_id, "Use:\nPRICE 120.5")
                    return

                entry_price = float(text.split()[1])
                symbol = state["symbol"]

                stop_event = threading.Event()
                price_watchers[user_id] = stop_event

                threading.Thread(
                    target=price_monitor_worker,
                    args=(chat_id, user_id, symbol, entry_price),
                    daemon=True
                ).start()

                user_state[user_id] = {"mode": None}
                return

    except Exception:
        traceback.print_exc()


dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))


# =====================================================
# WEBHOOK
# =====================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200


@app.route("/")
def index():
    return "✅ Option Monitor Bot Running"


if __name__ == "__main__":
    app.run(port=8000)

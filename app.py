import os
import time
import threading
import requests
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, render_template
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

IST = timezone(timedelta(hours=5, minutes=30))

LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
    "BANKEX": 15,
}

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

# =====================================================
# INIT
# =====================================================
bot = Bot(
    token=TOKEN,
    request=Request(con_pool_size=8, connect_timeout=5, read_timeout=5),
)

app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

user_state = {}
price_watchers = {}
active_monitors = {}

# =====================================================
# UTIL
# =====================================================
def now_millis():
    return int(datetime.now(IST).timestamp() * 1000)


def detect_underlying(symbol):
    for k in LOT_SIZES:
        if symbol.startswith(k):
            return k
    return "NIFTY"


def fetch_option_ltp(trade_symbol, exchange):
    end_ms = now_millis()
    start_ms = end_ms - 5 * 60 * 1000

    url = (
        f"{BASE_CHART_URL}/exchange/{exchange}/segment/FNO/{trade_symbol}"
        f"?startTimeInMillis={start_ms}"
        f"&endTimeInMillis={end_ms}"
        f"&intervalInMinutes=1"
    )

    r = requests.get(url, headers=HEADERS, timeout=5)
    r.raise_for_status()
    candles = r.json().get("candles", [])
    return candles[-1][4] if candles else None


def safe_send(chat_id, text, **kw):
    try:
        bot.send_message(chat_id, text, **kw)
    except Exception as e:
        print("Telegram error:", e)

# =====================================================
# OPTION PARSER
# =====================================================
def build_option_symbol_from_human(text):
    """
    Unified expiry format for ALL indices:
    <UNDERLYING><YY><M><DD><STRIKE><CE|PE>

    Examples:
    SENSEX 01 JAN 85400 PE -> SENSEX26010185400PE
    NIFTY 02 FEB 25950 CE  -> NIFTY262025950CE
    BANKNIFTY 18 OCT 44500 PE -> BANKNIFTY26101844500PE
    """

    parts = text.split()
    if len(parts) != 5:
        return None

    underlying, day, mon, strike, opt = parts
    mon = mon.upper()
    opt = opt.upper()

    if opt not in ("CE", "PE"):
        return None

    try:
        day = int(day)
        month_num = datetime.strptime(mon, "%b").month
    except ValueError:
        return None

    today = datetime.now(IST)
    year = today.year % 100

    # roll year if expiry month already passed
    if month_num < today.month:
        year += 1

    # ✅ SAME FORMAT FOR NSE + BSE
    return f"{underlying}{year:02d}{month_num}{day:02d}{strike}{opt}"

# =====================================================
# MONITOR THREAD
# =====================================================
def price_monitor_worker(chat_id, user_id, trade_symbol, display_symbol, entry_price):
    underlying = detect_underlying(trade_symbol)
    lot_size = LOT_SIZES[underlying]
    exchange = "BSE" if underlying in ("SENSEX", "BANKEX") else "NSE"

    active_monitors[user_id] = {
        "symbol": display_symbol,
        "trade_symbol": trade_symbol,
        "entry": entry_price,
        "ltp": 0.0,
        "status": "MONITORING",
        "updated_at": ""
    }

    safe_send(chat_id, f"📡 Monitoring `{display_symbol}` @ {entry_price}", parse_mode="Markdown")

    while not price_watchers[user_id].is_set():
        ltp = fetch_option_ltp(trade_symbol, exchange)
        if ltp:
            active_monitors[user_id]["ltp"] = round(ltp, 2)
            active_monitors[user_id]["updated_at"] = datetime.now(IST).strftime("%H:%M:%S")

            if ltp >= entry_price:
                active_monitors[user_id]["status"] = "TRIGGERED"
                safe_send(
                    chat_id,
                    f"🚨 *ENTRY HIT*\n\n{display_symbol}\nLTP: ₹{ltp:.2f}",
                    parse_mode="Markdown"
                )
                break

        time.sleep(2)

    active_monitors.pop(user_id, None)
    price_watchers.pop(user_id, None)

# =====================================================
# /start
# =====================================================
def start(update, _):
    user_state[update.effective_user.id] = {"mode": None}
    safe_send(
        update.effective_chat.id,
        "👋 *Welcome*\n\n"
        "3️⃣ Monitor Option Price\n\n"
        "Reply with *3*\n"
        "Send *STOP* anytime to cancel",
        parse_mode="Markdown"
    )

dispatcher.add_handler(CommandHandler("start", start))

# =====================================================
# MESSAGE HANDLER
# =====================================================
def handle_message(update, _):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip().upper()

    # STOP
    if txt in ("STOP", "CANCEL"):
        if uid in price_watchers:
            price_watchers[uid].set()
            safe_send(cid, "🛑 Monitoring stopped")
        else:
            safe_send(cid, "ℹ️ No active monitor")
        user_state[uid] = {"mode": None}
        return

    state = user_state.setdefault(uid, {"mode": None})

    if state["mode"] is None:
        if txt == "3":
            state["mode"] = "MONITOR"
            state["step"] = 1
            safe_send(
                cid,
                "Send option:\n"
                "`NIFTY25DEC25950CE`\n"
                "`SENSEX 01 JAN 85400 PE`",
                parse_mode="Markdown"
            )
            return
        safe_send(cid, "Reply with *3* to start monitoring", parse_mode="Markdown")
        return

    if state["mode"] == "MONITOR":

        # STEP 1 — OPTION
        if state["step"] == 1:
            raw = txt

            if " " in raw:
                trade_symbol = build_option_symbol_from_human(raw)
            else:
                trade_symbol = raw

            if not trade_symbol:
                safe_send(cid, "❌ Invalid option format", parse_mode="Markdown")
                return

            state["trade_symbol"] = trade_symbol
            state["display_symbol"] = raw
            state["step"] = 2
            safe_send(cid, "Send entry price (example: 345)", parse_mode="Markdown")
            return

        # STEP 2 — PRICE
        if state["step"] == 2:
            try:
                entry_price = float(txt)
            except ValueError:
                safe_send(cid, "❌ Invalid price. Send number like 345", parse_mode="Markdown")
                return

            price_watchers[uid] = threading.Event()
            threading.Thread(
                target=price_monitor_worker,
                args=(cid, uid, state["trade_symbol"], state["display_symbol"], entry_price),
                daemon=True
            ).start()

            user_state[uid] = {"mode": None}

dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

# =====================================================
# WEBHOOK
# =====================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# =====================================================
# DASHBOARD API
# =====================================================
@app.route("/api/monitors")
def api_monitors():
    return jsonify(list(active_monitors.values()))

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(port=8000)

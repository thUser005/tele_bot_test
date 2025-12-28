import os
import time
import threading
import traceback
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

# =====================================================
# FLASK / TELEGRAM INIT
# =====================================================
bot = Bot(
    token=TOKEN,
    request=Request(con_pool_size=8, connect_timeout=5, read_timeout=5),
)

app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

# =====================================================
# STATE
# =====================================================
user_state = {}
price_watchers = {}
active_monitors = {}   # 🔥 live dashboard data

# =====================================================
# UTIL
# =====================================================
def now_millis():
    return int(datetime.now(IST).timestamp() * 1000)


def detect_underlying(symbol: str):
    for k in LOT_SIZES:
        if symbol.startswith(k):
            return k
    return "NIFTY"


def fetch_option_ltp(symbol: str, exchange: str):
    end_ms = now_millis()
    start_ms = end_ms - 5 * 60 * 1000

    url = (
        f"{BASE_CHART_URL}/exchange/{exchange}/segment/FNO/{symbol}"
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
        print("⚠️ Telegram error:", e)


# =====================================================
# HUMAN OPTION FORMAT PARSER
# =====================================================
def build_option_symbol_from_human(text: str):
    parts = text.split()
    if len(parts) != 5:
        return None

    underlying, day, mon, strike, opt = parts
    mon = mon.upper()
    opt = opt.upper()

    if opt not in ("CE", "PE"):
        return None

    today = datetime.now(IST)
    year = today.year % 100
    month_num = datetime.strptime(mon, "%b").month

    if month_num < today.month:
        year += 1

    return f"{underlying}{year:02d}{mon}{strike}{opt}"


# =====================================================
# PRICE MONITOR THREAD
# =====================================================
def price_monitor_worker(chat_id, user_id, symbol, entry_price):
    try:
        underlying = detect_underlying(symbol)
        lot_size = LOT_SIZES[underlying]
        exchange = "BSE" if underlying in ("SENSEX", "BANKEX") else "NSE"

        active_monitors[user_id] = {
            "symbol": symbol,
            "entry": entry_price,
            "underlying": underlying,
            "lot_size": lot_size,
            "exchange": exchange,
            "ltp": 0.0,
            "final_lots": 0,
            "status": "MONITORING",
            "updated_at": ""
        }

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

            risk_per_lot = ltp * lot_size
            max_risk_lots = int(RISK_AMOUNT // risk_per_lot)
            max_capital_lots = int(CAPITAL // risk_per_lot)
            final_lots = max(0, min(max_risk_lots, max_capital_lots))

            active_monitors[user_id].update({
                "ltp": round(ltp, 2),
                "final_lots": final_lots,
                "updated_at": datetime.now(IST).strftime("%H:%M:%S")
            })

            if ltp >= entry_price:
                active_monitors[user_id]["status"] = "TRIGGERED"

                safe_send(
                    chat_id,
                    f"🚨 *ENTRY HIT*\n\n"
                    f"Symbol: `{symbol}`\n"
                    f"LTP: ₹{ltp:.2f}\n"
                    f"Lots: {final_lots}",
                    parse_mode="Markdown"
                )
                break

            time.sleep(2)

    except Exception as e:
        safe_send(chat_id, f"⚠️ Monitor error: {e}")

    finally:
        price_watchers[user_id].set()


# =====================================================
# /start
# =====================================================
def start(update, context):
    user_state[update.effective_user.id] = {"mode": None}

    safe_send(
        update.effective_chat.id,
        "👋 *Welcome*\n\n"
        "3️⃣ Monitor Option Price\n\n"
        "Reply with *3*",
        parse_mode="Markdown"
    )


dispatcher.add_handler(CommandHandler("start", start))


# =====================================================
# MESSAGE HANDLER
# =====================================================
def handle_message(update, context):
    try:
        uid = update.effective_user.id
        cid = update.effective_chat.id
        txt = update.message.text.strip().upper()

        if uid not in user_state:
            safe_send(cid, "Type /start")
            return

        state = user_state[uid]

        if state["mode"] is None:
            if txt == "3":
                state["mode"] = "MONITOR"
                state["step"] = 1
                safe_send(
                    cid,
                    "Send:\n`OPTION NIFTY25DEC25950CE`\nOR\n`OPTION SENSEX 01 JAN 85400 PE`",
                    parse_mode="Markdown"
                )
                return

            safe_send(cid, "Only option *3* is active now", parse_mode="Markdown")
            return

        if state["mode"] == "MONITOR":
            if state["step"] == 1:
                raw = txt.replace("OPTION", "").strip()
                symbol = raw if raw[-2:] in ("CE", "PE") else build_option_symbol_from_human(raw)

                if not symbol:
                    safe_send(cid, "❌ Invalid option format")
                    return

                state["symbol"] = symbol
                state["step"] = 2
                safe_send(cid, "Send:\n`PRICE 120.5`", parse_mode="Markdown")
                return

            if state["step"] == 2:
                entry_price = float(txt.split()[1])
                price_watchers[uid] = threading.Event()

                threading.Thread(
                    target=price_monitor_worker,
                    args=(cid, uid, state["symbol"], entry_price),
                    daemon=True
                ).start()

                user_state[uid] = {"mode": None}
                return

    except Exception:
        traceback.print_exc()


dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))


# =====================================================
# WEBHOOK (REQUIRED FOR RAILWAY)
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
    rows = []
    for v in active_monitors.values():
        entry = v["entry"]
        ltp = v["ltp"]
        pnl_pct = ((ltp - entry) / entry * 100) if entry else 0
        capital_pnl = pnl_pct / 100 * CAPITAL

        rows.append({
            **v,
            "pnl_pct": round(pnl_pct, 2),
            "capital_pnl": round(capital_pnl, 2)
        })

    return jsonify(rows)


# =====================================================
# ROOT / HEALTH / DASHBOARD
# =====================================================
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(port=8000)

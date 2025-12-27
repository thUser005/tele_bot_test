import os
import json
import re
import requests
from datetime import datetime
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0",
}

HEADERS_API = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "x-app-id": "growwWeb",
    "x-device-type": "desktop",
    "x-platform": "web",
}

# =====================================================
# INIT
# =====================================================
bot = Bot(token=TOKEN)
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

# =====================================================
# EXPIRY + SYMBOL HELPERS (UNCHANGED LOGIC)
# =====================================================
def is_weekly_expiry(expiry_date: str) -> bool:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    return dt.weekday() != 3  # Thursday

def build_expiry_code(expiry_date: str) -> str:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")

    if is_weekly_expiry(expiry_date):
        yy = dt.strftime("%y")
        m = str(int(dt.strftime("%m")))
        dd = dt.strftime("%d")
        return f"{yy}{m}{dd}"
    else:
        return dt.strftime("%y%b").upper()

def build_symbol(underlying, expiry_code, strike, opt_type):
    return f"{underlying}{expiry_code}{strike}{opt_type}"

# =====================================================
# SIGNAL PARSER
# =====================================================
def parse_signal(text: str):
    text = text.upper()
    signal = {}

    # BUY / SELL
    signal["action"] = "BUY" if "BUY" in text else "SELL"

    # UNDERLYING
    m = re.search(r"(NIFTY|BANKNIFTY)", text)
    if not m:
        raise ValueError("Underlying not found")
    signal["underlying"] = m.group(1)

    # STRIKE + CE/PE
    m = re.search(r"(\d{4,5})\s*(CE|PE)", text)
    if not m:
        raise ValueError("Strike / option type not found")
    signal["strike"] = int(m.group(1))
    signal["option_type"] = m.group(2)

    # EXPIRY → (30 DEC EX)
    m = re.search(r"\((\d{1,2})\s*([A-Z]{3})\s*EX", text)
    if not m:
        raise ValueError("Expiry not found")

    day = int(m.group(1))
    month = m.group(2)
    year = datetime.now().year
    expiry = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
    signal["expiry"] = expiry.strftime("%Y-%m-%d")

    # ABOVE
    m = re.search(r"ABOVE\s*[:-]\s*(\d+)", text)
    signal["above"] = int(m.group(1)) if m else None

    # TARGETS
    m = re.search(r"TARGET\s*([\d/ ]+)", text)
    signal["targets"] = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []

    # SL
    m = re.search(r"SL\s*[:\-]?\s*(\d+)", text)
    signal["stoploss"] = int(m.group(1)) if m else None

    return signal

# =====================================================
# FETCH LIVE PRICE
# =====================================================
def fetch_live_price(symbol, referer_url):
    api_url = (
        "https://groww.in/v1/api/stocks_fo_data/v1/"
        "tr_live_prices/exchange/NSE/segment/FNO/latest_prices_batch"
    )

    headers = HEADERS_API.copy()
    headers["referer"] = referer_url

    response = requests.post(
        api_url,
        headers=headers,
        json=[symbol],
        timeout=15
    )
    response.raise_for_status()
    return response.json().get(symbol)

# =====================================================
# /start
# =====================================================
def start(update, context):
    update.message.reply_text(
        "👋 Send a trading signal like:\n\n"
        "💢 BUY NIFTY 26200 CE (30 DEC EX)\n"
        "⬆️ ABOVE :- 45\n"
        "⛳ TARGET 55//75/85/100\n"
        "❌ SL :25"
    )

# =====================================================
# MESSAGE HANDLER (SIGNAL MODE)
# =====================================================
def handle_message(update, context):
    text = update.message.text

    try:
        signal = parse_signal(text)

        expiry_code = build_expiry_code(signal["expiry"])
        symbol = build_symbol(
            signal["underlying"],
            expiry_code,
            signal["strike"],
            signal["option_type"]
        )

        html_url = f"https://groww.in/options/{signal['underlying'].lower()}?expiry={signal['expiry']}"
        market_data = fetch_live_price(symbol, html_url)

        response = {
            "signal": signal,
            "market_data": market_data
        }

        update.message.reply_text(
            json.dumps(response, indent=2),
            parse_mode=None
        )

    except Exception as e:
        update.message.reply_text(f"❌ Error:\n{e}")

# =====================================================
# HANDLERS
# =====================================================
dispatcher.add_handler(CommandHandler("start", start))
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
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "Telegram Signal Bot Running"

# =====================================================
# LOCAL RUN
# =====================================================
if __name__ == "__main__":
    app.run(port=8000)

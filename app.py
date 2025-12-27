import os
import json
import re
import requests
from datetime import datetime
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from bs4 import BeautifulSoup

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
# HELPERS (FROM YOUR WORKING SCRIPT)
# =====================================================
def is_weekly_expiry(expiry_date: str) -> bool:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    return dt.weekday() != 3  # Thursday

def build_expiry_code(expiry_date: str) -> str:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    if is_weekly_expiry(expiry_date):
        return f"{dt.strftime('%y')}{int(dt.strftime('%m'))}{dt.strftime('%d')}"
    else:
        return dt.strftime("%y%b").upper()

def normalize_strike(text: str) -> str:
    return text.replace(",", "")

def build_symbol(underlying, expiry_code, strike, opt_type):
    return f"{underlying}{expiry_code}{strike}{opt_type}"

def validate_expiry(expiry_date: str):
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    if dt.weekday() != 3:
        raise ValueError("Expiry must be a Thursday")

# =====================================================
# CORE: FETCH FULL OPTION CHAIN
# =====================================================
def fetch_option_chain(expiry_date: str, underlying="NIFTY"):
    validate_expiry(expiry_date)

    html_url = f"https://groww.in/options/{underlying.lower()}?expiry={expiry_date}"

    # ---- STEP 1: Fetch HTML ----
    resp = requests.get(html_url, headers=HEADERS_HTML, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    texts = [i.get_text(strip=True) for i in soup.select(".bodyBaseHeavy")]

    strike_texts = [
        t for t in texts if re.fullmatch(r"\d{1,3}(,\d{3})*", t)
    ]

    if not strike_texts:
        raise RuntimeError("No strikes found")

    strikes = sorted(set(normalize_strike(s) for s in strike_texts), key=int)

    # ---- STEP 2: Build Symbols ----
    expiry_code = build_expiry_code(expiry_date)

    symbols = []
    for strike in strikes:
        symbols.append(build_symbol(underlying, expiry_code, strike, "CE"))
        symbols.append(build_symbol(underlying, expiry_code, strike, "PE"))

    # ---- STEP 3: Fetch Live Prices ----
    api_url = (
        "https://groww.in/v1/api/stocks_fo_data/v1/"
        "tr_live_prices/exchange/NSE/segment/FNO/latest_prices_batch"
    )

    headers = HEADERS_API.copy()
    headers["referer"] = html_url

    r = requests.post(api_url, headers=headers, json=symbols, timeout=20)
    r.raise_for_status()

    return r.json()

# =====================================================
# /start
# =====================================================
def start(update, context):
    update.message.reply_text(
        "📊 *Option Chain Bot*\n\n"
        "Send:\n"
        "`DATE YYYY-MM-DD`\n\n"
        "Example:\n"
        "`DATE 2026-01-08`\n\n"
        "⚠️ Date must be a *Thursday*",
        parse_mode="Markdown"
    )

# =====================================================
# TELEGRAM MESSAGE HANDLER
# =====================================================
def handle_message(update, context):
    try:
        text = update.message.text.strip().upper()

        if not text.startswith("DATE"):
            raise ValueError("Use: DATE YYYY-MM-DD")

        expiry = text.split()[1]

        data = fetch_option_chain(expiry)

        filename = f"data_{expiry}.json"
        file_bytes = json.dumps(data, indent=2).encode("utf-8")

        update.message.reply_document(
            document=file_bytes,
            filename=filename,
            caption=f"📄 Option chain for {expiry}"
        )

    except Exception as e:
        update.message.reply_text(
            f"❌ Error:\n{e}\n\nType /start for help"
        )

# =====================================================
# HANDLERS
# =====================================================
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

# =====================================================
# WEBHOOK (POST = Telegram, GET = Browser/API)
# =====================================================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # Telegram webhook
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200

    # Browser/API
    try:
        expiry = request.args.get("expiry")
        underlying = request.args.get("underlying", "NIFTY")

        if not expiry:
            return {"error": "expiry param required"}, 400

        data = fetch_option_chain(expiry, underlying)

        return Response(
            json.dumps(data, indent=2),
            mimetype="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=data_{expiry}.json"
            }
        )

    except Exception as e:
        return {"error": str(e)}, 400

# =====================================================
# HEALTH
# =====================================================
@app.route("/")
def index():
    return "Option Chain Bot Running"

if __name__ == "__main__":
    app.run(port=8000)

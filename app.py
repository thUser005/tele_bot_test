import os
import json
import requests
from datetime import datetime
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

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
# EXPIRY + SYMBOL HELPERS
# =====================================================
def validate_expiry(expiry_date: str):
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    if dt.weekday() != 3:  # Thursday
        raise ValueError(
            f"{expiry_date} is not a valid expiry. "
            "NIFTY/BANKNIFTY expiry must be Thursday."
        )

def is_weekly_expiry(expiry_date: str) -> bool:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    return dt.weekday() != 3

def build_expiry_code(expiry_date: str) -> str:
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    if is_weekly_expiry(expiry_date):
        return f"{dt.strftime('%y')}{int(dt.strftime('%m'))}{dt.strftime('%d')}"
    else:
        return dt.strftime("%y%b").upper()

def build_symbol(underlying, expiry_code, strike, opt_type):
    return f"{underlying}{expiry_code}{strike}{opt_type}"

# =====================================================
# SIGNAL PARSER (TEXT OR JSON)
# =====================================================
def parse_signal(text: str):
    text = text.strip()

    # ---------- JSON FORMAT ----------
    if text.startswith("{"):
        data = json.loads(text)

        required = ["action", "underlying", "strike", "option_type", "expiry"]
        for k in required:
            if k not in data:
                raise ValueError(f"Missing field: {k}")

        return {
            "action": data["action"].upper(),
            "underlying": data["underlying"].upper(),
            "strike": int(data["strike"]),
            "option_type": data["option_type"].upper(),
            "expiry": data["expiry"],
            "above": data.get("above"),
            "targets": data.get("targets", []),
            "stoploss": data.get("sl"),
        }

    # ---------- TEXT FORMAT ----------
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 5:
        raise ValueError("Incomplete signal data")

    signal = {
        "action": lines[0].upper(),
        "underlying": lines[1].upper(),
        "strike": int(lines[2]),
        "option_type": lines[3].upper(),
        "expiry": datetime.strptime(lines[4], "%d-%m-%Y").strftime("%Y-%m-%d"),
        "above": None,
        "targets": [],
        "stoploss": None,
    }

    for line in lines[5:]:
        if line.startswith("ABOVE="):
            signal["above"] = int(line.split("=")[1])
        elif line.startswith("TARGETS="):
            signal["targets"] = [int(x) for x in line.split("=")[1].split(",")]
        elif line.startswith("SL="):
            signal["stoploss"] = int(line.split("=")[1])

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

    r = requests.post(api_url, headers=headers, json=[symbol], timeout=15)
    r.raise_for_status()
    return r.json().get(symbol)

# =====================================================
# /start
# =====================================================
def start(update, context):
    update.message.reply_text(
        "👋 *Welcome to Signal Bot*\n\n"
        "*Send signal in ANY ONE format below:*\n\n"
        "*Text format:*\n"
        "BUY\n"
        "NIFTY\n"
        "26200\n"
        "CE\n"
        "26-12-2025   ← (Must be Thursday)\n"
        "ABOVE=45\n"
        "TARGETS=55,75,85,100\n"
        "SL=25\n\n"
        "*OR JSON format:*\n"
        "{\n"
        "  \"action\": \"BUY\",\n"
        "  \"underlying\": \"NIFTY\",\n"
        "  \"strike\": 26200,\n"
        "  \"option_type\": \"CE\",\n"
        "  \"expiry\": \"2025-12-26\",\n"
        "  \"above\": 45,\n"
        "  \"targets\": [55,75,85,100],\n"
        "  \"sl\": 25\n"
        "}",
        parse_mode="Markdown"
    )

# =====================================================
# MESSAGE HANDLER (TELEGRAM)
# =====================================================
def handle_message(update, context):
    try:
        signal = parse_signal(update.message.text)

        validate_expiry(signal["expiry"])

        expiry_code = build_expiry_code(signal["expiry"])
        symbol = build_symbol(
            signal["underlying"],
            expiry_code,
            signal["strike"],
            signal["option_type"]
        )

        html_url = (
            f"https://groww.in/options/"
            f"{signal['underlying'].lower()}?expiry={signal['expiry']}"
        )

        market_data = fetch_live_price(symbol, html_url)

        update.message.reply_text(
            json.dumps(
                {"signal": signal, "market_data": market_data},
                indent=2
            )
        )

    except Exception as e:
        update.message.reply_text(
            "❌ *Invalid signal*\n\n"
            f"Reason: `{e}`\n\n"
            "Expiry must be a valid Thursday.\n"
            "Type /start to see the correct format.",
            parse_mode="Markdown"
        )

# =====================================================
# HANDLERS
# =====================================================
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

# =====================================================
# WEBHOOK (POST = Telegram, GET = API)
# =====================================================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # -------- Telegram webhook --------
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200

    # -------- URL API trigger --------
    try:
        signal = {
            "action": request.args.get("action", "").upper(),
            "underlying": request.args.get("underlying", "").upper(),
            "strike": request.args.get("strike", type=int),
            "option_type": request.args.get("option_type", "").upper(),
            "expiry": request.args.get("expiry"),
            "above": request.args.get("above", type=int),
            "targets": (
                [int(x) for x in request.args.get("targets", "").split(",")]
                if request.args.get("targets") else []
            ),
            "stoploss": request.args.get("sl", type=int),
        }

        if not all([signal["action"], signal["underlying"],
                    signal["strike"], signal["option_type"],
                    signal["expiry"]]):
            return {"error": "Missing required parameters"}, 400

        validate_expiry(signal["expiry"])

        expiry_code = build_expiry_code(signal["expiry"])
        symbol = build_symbol(
            signal["underlying"],
            expiry_code,
            signal["strike"],
            signal["option_type"]
        )

        html_url = (
            f"https://groww.in/options/"
            f"{signal['underlying'].lower()}?expiry={signal['expiry']}"
        )

        market_data = fetch_live_price(symbol, html_url)

        return {
            "signal": signal,
            "market_data": market_data
        }, 200

    except Exception as e:
        return {
            "error": str(e),
            "hint": "Use a valid Thursday expiry"
        }, 400

# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "Telegram Signal Bot Running"

if __name__ == "__main__":
    app.run(port=8000)

import os
import json
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, MessageHandler, Filters
from get_data import get_data_fun   # your working Groww logic

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

# =====================================================
# INIT
# =====================================================
bot = Bot(token=TOKEN)
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

# =====================================================
# TELEGRAM MESSAGE HANDLER
# =====================================================
def handle_message(update, context):
    try:
        text = update.message.text.strip().upper()

        # Expected: DATE YYYY-MM-DD
        if not text.startswith("DATE"):
            raise ValueError("Use format: DATE YYYY-MM-DD")

        parts = text.split()
        if len(parts) != 2:
            raise ValueError("Use format: DATE YYYY-MM-DD")

        expiry = parts[1]
        underlying = "NIFTY"  # default (can extend later)

        data = get_data_fun(expiry, underlying)

        filename = f"data_{expiry}.json"
        file_bytes = json.dumps(data, indent=2).encode("utf-8")

        update.message.reply_document(
            document=file_bytes,
            filename=filename,
            caption=f"📄 Option chain for {expiry}"
        )

    except Exception as e:
        update.message.reply_text(
            f"❌ Error:\n{e}\n\nExample:\nDATE 2026-01-08"
        )

# Register Telegram handler
dispatcher.add_handler(
    MessageHandler(Filters.text & ~Filters.command, handle_message)
)

# =====================================================
# WEBHOOK (POST = Telegram, GET = Browser / API)
# =====================================================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # -------------------------------
    # Telegram webhook (POST)
    # -------------------------------
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200

    # -------------------------------
    # Browser / API (GET)
    # -------------------------------
    try:
        expiry = request.args.get("expiry")
        underlying = request.args.get("underlying", "NIFTY")

        if not expiry:
            return {
                "error": "expiry query parameter is required",
                "example": "/webhook?expiry=2026-01-08&underlying=NIFTY"
            }, 400

        data = get_data_fun(expiry, underlying)

        # ✅ INLINE JSON RESPONSE (no forced download)
        return Response(
            json.dumps(data, indent=2),
            mimetype="application/json"
        )

    except Exception as e:
        return {
            "error": str(e)
        }, 400

# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "Option Chain Bot Running"

# =====================================================
# LOCAL RUN (optional)
# =====================================================
if __name__ == "__main__":
    app.run(port=8000)

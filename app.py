import os
import json
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, MessageHandler, Filters
from get_data import get_data_fun   # your working logic

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

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

        if not text.startswith("DATE"):
            raise ValueError("Use: DATE YYYY-MM-DD")

        expiry = text.split()[1]
        underlying = "NIFTY"

        data = get_data_fun(expiry, underlying)

        filename = f"data_{expiry}.json"
        file_bytes = json.dumps(data, indent=2).encode("utf-8")

        update.message.reply_document(
            document=file_bytes,
            filename=filename,
            caption=f"📄 Option chain for {expiry}"
        )

    except Exception as e:
        update.message.reply_text(f"❌ Error:\n{e}")

# Register handler
dispatcher.add_handler(
    MessageHandler(Filters.text & ~Filters.command, handle_message)
)

# =====================================================
# WEBHOOK (POST = Telegram, GET = Browser)
# =====================================================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # ---------- Telegram ----------
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200

    # ---------- Browser / API ----------
    try:
        expiry = request.args.get("expiry")
        underlying = request.args.get("underlying", "NIFTY")

        if not expiry:
            return {"error": "expiry param required"}, 400

        data = get_data_fun(expiry, underlying)

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
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "Option Chain Bot Running"

if __name__ == "__main__":
    app.run(port=8000)

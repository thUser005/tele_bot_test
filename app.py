import os
import json
import traceback
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from telegram.utils.request import Request
from get_data import get_data_fun

# =====================================================
# CONFIG
# =====================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN not set")

# =====================================================
# TELEGRAM BOT (Railway-safe)
# =====================================================
tg_request = Request(
    con_pool_size=8,
    connect_timeout=5,
    read_timeout=5
)

bot = Bot(token=TOKEN, request=tg_request)

# =====================================================
# FLASK APP
# =====================================================
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=1)

# =====================================================
# IN-MEMORY USER STATE
# =====================================================
user_state = {}

# =====================================================
# SAFE SEND FUNCTION (CRITICAL)
# =====================================================
def safe_send(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        print("⚠️ Telegram send failed:", e)

def safe_document(chat_id, file_bytes, filename, caption=None):
    try:
        bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=caption
        )
    except Exception as e:
        print("⚠️ Telegram document send failed:", e)

# =====================================================
# /start COMMAND
# =====================================================
def start(update, context):
    user_id = update.effective_user.id
    user_state[user_id] = {"mode": None}

    safe_send(
        update.effective_chat.id,
        "👋 *Welcome*\n\n"
        "Choose an option:\n\n"
        "1️⃣ Option Chain by Date\n"
        "2️⃣ Add Two Numbers\n\n"
        "Reply with *1* or *2*",
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
            safe_send(chat_id, "Type /start to begin.")
            return

        state = user_state[user_id]

        # ============================
        # MENU
        # ============================
        if state["mode"] is None:
            if text == "1":
                state["mode"] = "OPTION_CHAIN"
                safe_send(
                    chat_id,
                    "📊 *Option Chain Mode*\n\n"
                    "Send:\n"
                    "`DATE YYYY-MM-DD`\n\n"
                    "Example:\n"
                    "`DATE 2026-01-08`",
                    parse_mode="Markdown"
                )
                return

            if text == "2":
                state["mode"] = "ADD"
                state["step"] = 1
                safe_send(chat_id, "➕ Send first number")
                return

            safe_send(chat_id, "Please reply with *1* or *2*", parse_mode="Markdown")
            return

        # ============================
        # OPTION CHAIN MODE
        # ============================
        if state["mode"] == "OPTION_CHAIN":
            if not text.startswith("DATE"):
                safe_send(chat_id, "❌ Use format:\nDATE YYYY-MM-DD")
                return

            parts = text.split()
            if len(parts) != 2:
                safe_send(chat_id, "❌ Use format:\nDATE YYYY-MM-DD")
                return

            expiry = parts[1]
            underlying = "NIFTY"

            data = get_data_fun(expiry, underlying)

            file_bytes = json.dumps(data, indent=2).encode("utf-8")
            filename = f"option_chain_{expiry}.json"

            safe_document(
                chat_id,
                file_bytes,
                filename,
                caption=f"📄 Option chain for {expiry}"
            )

            user_state[user_id] = {"mode": None}
            return

        # ============================
        # ADD MODE
        # ============================
        if state["mode"] == "ADD":
            try:
                number = float(text)
            except ValueError:
                safe_send(chat_id, "❌ Please send a valid number")
                return

            if state["step"] == 1:
                state["num1"] = number
                state["step"] = 2
                safe_send(chat_id, "➕ Send second number")
                return

            if state["step"] == 2:
                result = state["num1"] + number
                safe_send(chat_id, f"🧮 Result: {result}")
                user_state[user_id] = {"mode": None}
                return

    except Exception as e:
        print("❌ Message handler error")
        traceback.print_exc()

dispatcher.add_handler(
    MessageHandler(Filters.text & ~Filters.command, handle_message)
)

# =====================================================
# WEBHOOK ENDPOINT
# =====================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    except Exception:
        print("❌ Webhook processing error")
        traceback.print_exc()
    return "OK", 200

# =====================================================
# HTTP API (Browser / Curl)
# =====================================================
@app.route("/api")
def api():
    try:
        expiry = request.args.get("expiry")
        underlying = request.args.get("underlying", "NIFTY")

        if not expiry:
            return {
                "error": "expiry is required",
                "example": "/api?expiry=2026-01-08&underlying=NIFTY"
            }, 400

        data = get_data_fun(expiry, underlying)
        return Response(json.dumps(data, indent=2), mimetype="application/json")

    except Exception as e:
        return {"error": str(e)}, 400

# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "✅ Option Chain Bot Running"

# =====================================================
# LOCAL RUN
# =====================================================
if __name__ == "__main__":
    app.run(port=8000)

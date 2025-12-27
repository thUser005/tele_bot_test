import os
import json
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from get_data import get_data_fun

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
# USER STATE (in-memory)
# =====================================================
user_state = {}

# =====================================================
# /start COMMAND
# =====================================================
def start(update, context):
    user_id = update.effective_user.id
    user_state[user_id] = {"mode": None}

    update.message.reply_text(
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
    user_id = update.effective_user.id
    text = update.message.text.strip().upper()

    # If user has no state
    if user_id not in user_state:
        update.message.reply_text("Type /start to begin.")
        return

    state = user_state[user_id]

    # ----------------------------
    # MENU SELECTION
    # ----------------------------
    if state["mode"] is None:
        if text == "1":
            state["mode"] = "OPTION_CHAIN"
            update.message.reply_text(
                "📊 *Option Chain Mode*\n\n"
                "Send:\n"
                "`DATE YYYY-MM-DD`\n\n"
                "Example:\n"
                "`DATE 2026-01-08`",
                parse_mode="Markdown"
            )
            return

        elif text == "2":
            state["mode"] = "ADD"
            state["step"] = 1
            update.message.reply_text("➕ Send first number")
            return

        else:
            update.message.reply_text("Please reply with *1* or *2*", parse_mode="Markdown")
            return

    # ----------------------------
    # OPTION CHAIN MODE
    # ----------------------------
    if state["mode"] == "OPTION_CHAIN":
        try:
            if not text.startswith("DATE"):
                raise ValueError("Use: DATE YYYY-MM-DD")

            parts = text.split()
            if len(parts) != 2:
                raise ValueError("Use: DATE YYYY-MM-DD")

            expiry = parts[1]
            underlying = "NIFTY"

            data = get_data_fun(expiry, underlying)

            filename = f"data_{expiry}.json"
            file_bytes = json.dumps(data, indent=2).encode("utf-8")

            update.message.reply_document(
                document=file_bytes,
                filename=filename,
                caption=f"📄 Option chain for {expiry}"
            )

            # Reset state after completion
            user_state[user_id] = {"mode": None}

        except Exception as e:
            update.message.reply_text(
                f"❌ Error:\n{e}\n\nExample:\nDATE 2026-01-08"
            )

        return

    # ----------------------------
    # ADDITION MODE
    # ----------------------------
    if state["mode"] == "ADD":
        try:
            number = float(text)

            if state["step"] == 1:
                state["num1"] = number
                state["step"] = 2
                update.message.reply_text("➕ Send second number")

            elif state["step"] == 2:
                result = state["num1"] + number
                update.message.reply_text(f"🧮 Result: {result}")

                # Reset after completion
                user_state[user_id] = {"mode": None}

        except ValueError:
            update.message.reply_text("❌ Please send a valid number")

# Register handler
dispatcher.add_handler(
    MessageHandler(Filters.text & ~Filters.command, handle_message)
)

# =====================================================
# WEBHOOK (POST = Telegram, GET = Browser)
# =====================================================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # Telegram webhook
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200

    # Browser / API
    try:
        expiry = request.args.get("expiry")
        underlying = request.args.get("underlying", "NIFTY")

        if not expiry:
            return {
                "error": "expiry query parameter is required",
                "example": "/webhook?expiry=2026-01-08&underlying=NIFTY"
            }, 400

        data = get_data_fun(expiry, underlying)

        return Response(
            json.dumps(data, indent=2),
            mimetype="application/json"
        )

    except Exception as e:
        return {"error": str(e)}, 400

# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/")
def index():
    return "Option Chain Bot Running"

# =====================================================
# LOCAL RUN
# =====================================================
if __name__ == "__main__":
    app.run(port=8000)

import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

bot = Bot(token=TOKEN)

app = Flask(__name__)

dispatcher = Dispatcher(bot, None, workers=0)

# -----------------------------
# STATE STORAGE (in-memory)
# -----------------------------
user_state = {}

# -----------------------------
# /start handler
# -----------------------------
def start(update, context):
    user_id = update.effective_user.id
    user_state[user_id] = {"step": 1}

    update.message.reply_text(
        "👋 Welcome!\n\nPlease send the *first number*.",
        parse_mode="Markdown"
    )

# -----------------------------
# Message handler
# -----------------------------
def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in user_state:
        update.message.reply_text("Type /start to begin.")
        return

    try:
        number = float(text)
    except ValueError:
        update.message.reply_text("❌ Please send a valid number.")
        return

    state = user_state[user_id]

    if state["step"] == 1:
        state["num1"] = number
        state["step"] = 2
        update.message.reply_text("✅ Got it. Now send the *second number*.", parse_mode="Markdown")

    elif state["step"] == 2:
        result = state["num1"] + number
        update.message.reply_text(f"🧮 *Result:* `{result}`", parse_mode="Markdown")
        del user_state[user_id]

# -----------------------------
# Register handlers
# -----------------------------
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

# -----------------------------
# Webhook endpoint
# -----------------------------
@app.route(f"/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# -----------------------------
# Health check
# -----------------------------
@app.route("/")
def index():
    return "Telegram Bot Running"

# -----------------------------
# Run locally (optional)
# -----------------------------
if __name__ == "__main__":
    app.run(port=8000)

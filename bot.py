import json
import os
import re
import traceback
import platform
import subprocess
import threading
import shutil
from datetime import datetime, time
import pytz

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv

from instrument_master_resolver import (
    resolve_option_from_master,
    ensure_instrument_master_ready,
)

from get_bal import fetch_balance
from fetch_ltp import fetch_ltp
from place_order import place_partial_runner_gtt

# ================================
# CONFIG
# ================================
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
JSON_FILE = "data.json"

IST = pytz.timezone("Asia/Kolkata")
LOCAL_SERVER_PORT = 8000   # for tunnel

# ================================
# ENVIRONMENT DETECTION
# ================================
def is_colab_or_linux():
    """
    Detects Google Colab or Linux cloud environments
    """
    if os.path.exists("/content"):   # Google Colab
        return True

    if platform.system().lower() == "linux":
        return True

    return False

# ================================
# CLOUDFARE TUNNEL (LINUX ONLY)
# ================================
def start_cloudflare_tunnel(local_port=8000):
    """
    Starts Cloudflare tunnel in background (non-blocking)
    """
    if shutil.which("cloudflared") is None:
        print("⏳ Installing cloudflared...")

        subprocess.run(
            [
                "curl", "-fsSL",
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                "-o", "cloudflared"
            ],
            check=True
        )
        subprocess.run(["chmod", "+x", "cloudflared"], check=True)
        print("✅ cloudflared installed")

    def run_tunnel():
        print(f"🌐 Starting Cloudflare Tunnel → http://localhost:{local_port}")
        subprocess.run(
            ["./cloudflared", "tunnel", "--url", f"http://localhost:{local_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

    thread = threading.Thread(target=run_tunnel, daemon=True)
    thread.start()

# ================================
# TIME / SERVICE HELPERS
# ================================
def now_ist():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def is_upstox_service_open():
    """
    Upstox trading + funds services:
    05:30 AM – 11:59 PM IST
    """
    now = datetime.now(IST).time()
    return time(5, 30) <= now <= time(23, 59)

# ================================
# JSON LOG HELPERS
# ================================
def load_all_entries():
    if not os.path.exists(JSON_FILE):
        return []
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_json(entry: dict):
    data = load_all_entries()
    data.append(entry)
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def send_error(update: Update, stage: str, msg: str):
    await update.message.reply_text(
        f"❌ ERROR at stage: {stage}\n\n{msg}"
    )

# ================================
# PARSER
# ================================
def parse_trade_message(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("Empty message")

    parts = lines[0].split()
    if len(parts) < 4:
        raise ValueError("Invalid first line format")

    index = parts[0]
    expiry = " ".join(parts[1:3])
    strike = int(parts[3])
    option_type = parts[4] if len(parts) > 4 else None

    breakout_price = None
    stoploss = None

    for line in lines:
        u = line.upper()
        if "ABOVE" in u:
            m = re.search(r"(\d+)", line)
            if m:
                breakout_price = float(m.group(1))
        if "STOPLOSS" in u:
            stoploss = line.split(":-")[-1].strip()

    if breakout_price is None:
        raise ValueError("ABOVE price missing")

    return {
        "index": index,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "breakout_price": breakout_price,
        "stoploss": stoploss,
    }

# ================================
# COMMANDS
# ================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send trade input\n\n"
        "Example:\n"
        "SENSEX 08 JAN 86000 PE\n"
        "ABOVE :- 360"
    )

# ================================
# MESSAGE HANDLER
# ================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    stage = "INIT"

    try:
        if not is_upstox_service_open():
            await send_error(
                update,
                "UPSTOX_MAINTENANCE",
                "⚠️ Upstox Scheduled Maintenance\n\n"
                "Trading services unavailable.\n"
                "⏰ Try after 5:30 AM IST"
            )
            return

        stage = "PARSING_INPUT"
        parsed = parse_trade_message(text)

        stage = "RESOLVING_INSTRUMENT"
        instrument = resolve_option_from_master(
            index=parsed["index"],
            expiry=parsed["expiry"],
            strike=parsed["strike"],
            option_type=parsed["option_type"],
        )

        if not instrument:
            raise RuntimeError("Instrument not found in master")

        instrument_key = instrument["instrument_key"]
        lot_size = instrument["lot_size"]

        stage = "FETCHING_LTP"
        ltp = fetch_ltp(instrument_key)

        stage = "FETCHING_BALANCE"
        balance = fetch_balance(os.getenv("ACCESS_TOKEN"))

        if balance is None:
            await send_error(
                update,
                stage,
                "Funds service unavailable.\nTry after 5:30 AM IST."
            )
            return

        stage = "CHECKING_BALANCE"
        required = ltp * lot_size

        if balance < required:
            await send_error(
                update,
                stage,
                f"Insufficient Balance\n\n"
                f"LTP: {ltp}\n"
                f"Required: ₹{required:,.2f}\n"
                f"Available: ₹{balance:,.2f}"
            )
            return

        stage = "PLACING_GTT"
        result = place_partial_runner_gtt(
            instrument_token=instrument_key,
            desired_entry_price=parsed["breakout_price"],
            current_ltp=ltp,
            total_quantity=lot_size,
            runner_quantity=10,
            max_slippage=50,
            product="D",
            transaction_type="BUY"
        )

        stage = "SAVING_LOG"
        append_json({
            "timestamp": now_ist(),
            "stage": stage,
            "user_id": user.id,
            "username": user.username,
            **parsed,
            "instrument_key": instrument_key,
            "ltp": ltp,
            "balance": balance,
            "gtt": result
        })

        await update.message.reply_text(
            f"✅ GTT PLACED SUCCESSFULLY\n\n"
            f"{instrument['trading_symbol']}\n"
            f"LTP: {ltp}\n"
            f"Qty: {lot_size}\n"
            f"Entry: {result['entry']}\n"
            f"Target: {result['target']}\n"
            f"SL: {result['stoploss']}"
        )

    except Exception as e:
        append_json({
            "timestamp": now_ist(),
            "stage": stage,
            "user_id": user.id if user else None,
            "raw_message": text,
            "error": str(e),
            "traceback": traceback.format_exc()
        })
        await send_error(update, stage, str(e))

# ================================
# STARTUP INFO
# ================================
async def print_bot_info(app):
    bot = await app.bot.get_me()
    print("========================================")
    print(f"🤖 Bot Name : {bot.first_name}")
    print(f"👤 Username : @{bot.username}")
    print(f"🔗 https://t.me/{bot.username}")
    print("✅ Bot started safely")
    print("========================================")

# ================================
# MAIN (OS AWARE)
# ================================
def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN missing")
        return

    print("🔍 Verifying instrument master...")
    if not ensure_instrument_master_ready():
        print("❌ Instrument master NOT ready. Bot stopped.")
        return
    print("✅ Instrument master ready")

    if is_colab_or_linux():
        print("☁️ Colab / Linux detected → starting Cloudflare tunnel")
        start_cloudflare_tunnel(local_port=LOCAL_SERVER_PORT)
    else:
        print("🪟 Windows detected → running locally")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(print_bot_info)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Telegram bot is running")
    app.run_polling()

if __name__ == "__main__":
    main()

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import os
# Replace with your Bot Token from BotFather
TOKEN = os.getenv("BOT_TOKEN")

# Start command handler
async def start(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    print(f"👤 {user.first_name} ({user.id}) started the bot.")
    await update.message.reply_text(
        "Hello! 👋\nSend me two numbers separated by space, and I'll return their sum!\nExample: `10 20`"
    )

# Function to handle text messages (summing two numbers)
async def add_numbers(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    text = update.message.text
    print(f"📩 Received message from {user.first_name} ({user.id}): {text}")

    try:
        # Extract numbers
        numbers = text.split()
        
        if len(numbers) != 2:
            print(f"⚠️ Invalid input from {user.first_name}: {text}")
            await update.message.reply_text("⚠️ Please send exactly *two numbers* separated by space.\nExample: `5 10`")
            return
        
        # Convert to numbers
        num1, num2 = float(numbers[0]), float(numbers[1])
        result = num1 + num2
        
        print(f"✅ Sending sum result to {user.first_name}: {num1} + {num2} = {result}")
        await update.message.reply_text(f"✅ The sum of {num1} and {num2} is: *{result}*")
    
    except ValueError:
        print(f"❌ Error: Invalid input from {user.first_name}: {text}")
        await update.message.reply_text("❌ Invalid input. Please send *two valid numbers*.")

# Main function to set up the bot
def main():
    print("🚀 Starting the Telegram bot...")

    # Create bot application
    app = Application.builder().token(TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start))  # Handles /start command
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_numbers))  # Handles user input

    # Start polling
    print("🤖 Bot is running and waiting for messages...")
    app.run_polling()

# Run the bot
if __name__ == "__main__":
    main()

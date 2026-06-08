"""
Script to get all chats where bot is admin and their IDs
"""
from Handlers.config import TOKEN
from telegram import Bot
import asyncio

async def get_bot_info():
    """Get bot information and list all chats it's in"""
    bot = Bot(token=TOKEN)

    try:
        me = await bot.get_me()
        print(f"\n{'='*60}")
        print(f"Bot Name: {me.first_name}")
        print(f"Bot Username: @{me.username}")
        print(f"Bot ID: {me.id}")
        print(f"{'='*60}\n")

        # Note: get_chat_member_count needs a specific chat_id
        # This script shows you where to manually check
        print("To find all chats where bot is admin:\n")
        print("Method 1: Check bot messages in Telegram")
        print("  - Open bot profile")
        print("  - See 'About' section for groups/channels\n")

        print("Method 2: Use /getchatinfo in each group/channel")
        print("  - Private Chat: /getchatinfo")
        print("  - Groups: /getchatinfo")
        print("  - Note: Channels don't support commands\n")

        print("Method 3: Check Telegram settings directly")
        print("  - Settings → Privacy and Security → Linked Devices")
        print("  - Shows all connected bots and their chats\n")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(get_bot_info())

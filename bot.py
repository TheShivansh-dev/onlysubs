from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from Handlers.config import TOKEN
from Handlers.db import init_schema
from Handlers.subscription import (
    start_command,
    help_command,
    get_user_id_command,
    get_chat_info_command,
    get_details_command,
    log_user_join,
    check_subscription_expiry,
)
from Handlers.admin import handle_expiry_callback
from Handlers.backup import backup_data_files, setallfiles_command
from pytz import timezone
import datetime

ALLOWED_UPDATES = ["message", "callback_query", "chat_member"]


def build_application() -> Application:
    """Build and configure the Telegram Application (handlers + jobs).

    Reusable so the bot can run either standalone (run_polling) or inside the
    web service's event loop (initialize/start/updater.start_polling).
    """
    application = Application.builder().token(TOKEN).build()

    # ── Public commands ──
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('getuserid', get_user_id_command))
    application.add_handler(CommandHandler('getchatinfo', get_chat_info_command))
    application.add_handler(CommandHandler('getdetails', get_details_command))

    # ── Creator-group backup restore ──
    application.add_handler(CommandHandler('setallfiles', setallfiles_command))

    # ── Expiry Save / Remove buttons (posted into the admin group) ──
    application.add_handler(CallbackQueryHandler(handle_expiry_callback, pattern="^exp_"))

    # ── New chat members (console log) ──
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, log_user_join))

    # ── Daily 8 AM IST expiry check ──
    ist = timezone('Asia/Kolkata')
    application.job_queue.run_daily(
        check_subscription_expiry,
        time=datetime.time(hour=8, minute=0, tzinfo=ist)
    )

    # ── Data backup every 30 minutes ──
    application.job_queue.run_repeating(backup_data_files, interval=1800, first=60)

    return application


def main():
    init_schema()
    application = build_application()
    application.run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    print("Starting OnlySubscriber...\n")
    main()

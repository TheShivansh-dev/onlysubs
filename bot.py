from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatJoinRequestHandler, filters,
)
from Handlers.config import TOKEN
from Handlers.db import init_schema, db_get_setting
from Handlers.subscription import (
    start_command,
    help_command,
    get_user_id_command,
    get_chat_info_command,
    get_details_command,
    log_user_join,
    handle_join_request,
    check_subscription_expiry,
)
from Handlers.admin import handle_expiry_callback
from Handlers.backup import backup_data_files, setallfiles_command
from pytz import timezone
import datetime

ALLOWED_UPDATES = ["message", "callback_query", "chat_member", "chat_join_request"]

EXPIRY_JOB_NAME = "expiry_check"
DEFAULT_EXPIRY_TIME = "08:00"   # IST, used if the DB setting is missing/invalid
IST = timezone('Asia/Kolkata')


def _ist_time(hhmm: str) -> datetime.time:
    """Parse 'HH:MM' into an IST-aware time; falls back to 08:00 IST."""
    try:
        hh, mm = (int(x) for x in hhmm.strip().split(':'))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return datetime.time(hour=hh, minute=mm, tzinfo=IST)
    except Exception:
        pass
    return datetime.time(hour=8, minute=0, tzinfo=IST)


def reschedule_expiry_job(application: Application, hhmm: str) -> str:
    """
    (Re)schedule the daily expiry check at the given 'HH:MM' IST time.
    Removes any existing expiry job first. Returns the HH:MM actually used.
    Reused by the web panel so the owner can change the run time live.
    """
    for job in application.job_queue.get_jobs_by_name(EXPIRY_JOB_NAME):
        job.schedule_removal()
    t = _ist_time(hhmm)
    application.job_queue.run_daily(
        check_subscription_expiry, time=t, name=EXPIRY_JOB_NAME,
    )
    return f"{t.hour:02d}:{t.minute:02d}"


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

    # ── Rejoin/recovery join requests — approve only active subscribers ──
    application.add_handler(ChatJoinRequestHandler(handle_join_request))

    # ── New chat members (console log) ──
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, log_user_join))

    # ── Daily expiry check (time configurable by the owner from the panel) ──
    reschedule_expiry_job(application, db_get_setting('EXPIRY_CHECK_TIME', DEFAULT_EXPIRY_TIME))

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

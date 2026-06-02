from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ChatMemberHandler, filters
from Handlers.config import TOKEN
from Handlers.db import init_schema
from Handlers.subscription import (
    start_command,
    help_command,
    admin_stats_command,
    handle_subscription_callback,
    handle_payment_callback,
    check_subscription_expiry,
    get_user_id_command,
    get_chat_info_command,
    log_user_join,
    add_paid_user_command,
    list_paid_users_command,
    remove_paid_user_command,
    view_all_users_command,
    edit_user_command,
    user_info_command,
    gen_guid_command,
    handle_member_join_tracking,
)
from Handlers.admin import (
    startadmin_command,
    handle_admin_callback,
    handle_admin_message,
    handle_group_setup,
    webadmin_list_command,
    webadmin_add_command,
    webadmin_remove_command,
    webadmin_changepass_command,
)
from Handlers.backup import backup_data_files, setallfiles_command
from pytz import timezone
import datetime

def main():
    init_schema()
    application = Application.builder().token(TOKEN).build()

    # ── Group setup — must be first so setadmin/setsubgroup payloads are caught ──
    application.add_handler(
        CommandHandler('start', handle_group_setup, filters=filters.ChatType.GROUPS)
    )

    # ── Public commands ──
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('getuserid', get_user_id_command))
    application.add_handler(CommandHandler('getchatinfo', get_chat_info_command))

    # ── Admin commands ──
    application.add_handler(CommandHandler('startadmin', startadmin_command))
    application.add_handler(CommandHandler('stats',    admin_stats_command))
    application.add_handler(CommandHandler('adduser',  add_paid_user_command))
    application.add_handler(CommandHandler('listpaidusers', list_paid_users_command))
    application.add_handler(CommandHandler('removeuser',    remove_paid_user_command))
    application.add_handler(CommandHandler('viewall',  view_all_users_command))
    application.add_handler(CommandHandler('edituser', edit_user_command))
    application.add_handler(CommandHandler('userinfo', user_info_command))
    application.add_handler(CommandHandler('genguid',         gen_guid_command))
    application.add_handler(CommandHandler('setallfiles',     setallfiles_command))
    # ── SuperAdmin web-panel management ──
    application.add_handler(CommandHandler('webadmins',       webadmin_list_command))
    application.add_handler(CommandHandler('addwebadmin',     webadmin_add_command))
    application.add_handler(CommandHandler('removewebadmin',  webadmin_remove_command))
    application.add_handler(CommandHandler('changewebpass',   webadmin_changepass_command))

    # ── Callback routers ──
    application.add_handler(CallbackQueryHandler(handle_admin_callback,        pattern="^adm_"))
    application.add_handler(CallbackQueryHandler(handle_subscription_callback, pattern="^sub_"))
    application.add_handler(CallbackQueryHandler(handle_payment_callback,      pattern="^pay_"))

    # ── Message handlers ──
    # Admin conversation messages (must be before other message handlers)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_message
        )
    )
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, log_user_join))
    application.add_handler(ChatMemberHandler(handle_member_join_tracking, ChatMemberHandler.CHAT_MEMBER))

    # ── Daily 8 AM IST expiry check ──
    ist = timezone('Asia/Kolkata')
    application.job_queue.run_daily(
        check_subscription_expiry,
        time=datetime.time(hour=8, minute=0, tzinfo=ist)
    )

    # ── Data backup every 30 minutes ──
    application.job_queue.run_repeating(
        backup_data_files,
        interval=1800,
        first=60,
    )
    application.run_polling(allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    print("Starting OnlySubscriber...\n")
    main()
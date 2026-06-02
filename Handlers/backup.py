import asyncio
import io
import os
import zipfile
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from .config import BOT_CREATOR_GROUP_ID

DATA_DIR = "Data"


def _build_zip_bytes() -> tuple[bytes, list[str]]:
    """Zip every file in Data/ and return (bytes, list_of_filenames). Runs in thread."""
    buf = io.BytesIO()
    names: list[str] = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(DATA_DIR)):
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, fname)
                names.append(fname)
    return buf.getvalue(), names


def _extract_zip_bytes(data: bytes) -> tuple[list[str], list[str]]:
    """
    Extract zip bytes into Data/. Returns (replaced, not_found).
    Runs in thread — no async I/O inside.
    """
    replaced: list[str] = []
    not_found: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            base = os.path.basename(name)
            if not base:
                continue
            target = os.path.join(DATA_DIR, base)
            if os.path.exists(target):
                with zf.open(name) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                replaced.append(base)
            else:
                not_found.append(base)
    return replaced, not_found


# ─────────────────────────────────────────────
#  JOB — runs every 30 minutes via job_queue
# ─────────────────────────────────────────────

async def backup_data_files(context: ContextTypes.DEFAULT_TYPE):
    try:
        if not os.path.exists(DATA_DIR):
            return
        raw, names = await asyncio.to_thread(_build_zip_bytes)
        if not names:
            return
        now      = datetime.now().strftime('%Y-%m-%d_%H-%M')
        filename = f"backup_{now}.zip"
        await context.bot.send_document(
            chat_id=BOT_CREATOR_GROUP_ID,
            document=io.BytesIO(raw),
            filename=filename,
            caption=f"📦 Auto-backup {now} — {len(names)} files",
        )
    except Exception as e:
        print(f"[BACKUP] Error: {e}")


# ─────────────────────────────────────────────
#  COMMAND — /setallfiles (reply to a zip)
# ─────────────────────────────────────────────

async def setallfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != BOT_CREATOR_GROUP_ID:
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "Reply to a <code>.zip</code> file with /setallfiles", parse_mode="HTML"
        )
        return

    doc = update.message.reply_to_message.document
    if not (doc.file_name or '').lower().endswith('.zip'):
        await update.message.reply_text("Only <code>.zip</code> files are supported.", parse_mode="HTML")
        return

    msg = await update.message.reply_text("⏳ Downloading and extracting…")

    try:
        tg_file  = await context.bot.get_file(doc.file_id)
        buf      = io.BytesIO()
        await tg_file.download_to_memory(buf)
        raw      = buf.getvalue()

        replaced, not_found = await asyncio.to_thread(_extract_zip_bytes, raw)

        lines = [f"✅ <b>Replaced {len(replaced)} file(s):</b>"]
        lines += [f"  • {f}" for f in replaced] or ["  <i>none</i>"]
        if not_found:
            lines.append(f"\n⚠️ <b>Not found in Data/ ({len(not_found)}):</b>")
            lines += [f"  • {f}" for f in not_found]

        await msg.edit_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

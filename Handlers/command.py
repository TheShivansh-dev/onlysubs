from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import   ContextTypes
import os
import json
import pandas as pd
import asyncio,re
from .config import BOT_USERNAME


ALLOWED_GROUP_IDS = [-1001817635995, -1002114430690,-1001817635995]
groupsendid = -1002114430690
botManagementGroupId =  -1002359766306
CHANNEL_ID = "-1002234035497"
registered_groups = set()
broadcast_url = f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=start"
RegisteredGroupfile = "UserScore/RegisteredGroups.xlsx"
translation_excel_path = "UserScore/.xlsx"
GROUPS_FILE = "groups.json"
group_data_file = "group_data.json"


if os.path.exists(RegisteredGroupfile):
    df = pd.read_excel(RegisteredGroupfile)
    registered_groups = set(df["groupid"].dropna().astype(str).tolist())
else:
    registered_groups = set()

if os.path.exists(group_data_file):
    with open(group_data_file, "r") as f:
        group_data = json.load(f)
else:
    group_data = {}

def updateandaddgroups():
    existing_groups = set()

    if os.path.exists(RegisteredGroupfile):
        df = pd.read_excel(RegisteredGroupfile)
        existing_groups = set(df["groupid"].astype(str).tolist())
    else:
        df = pd.DataFrame(columns=["srno", "groupid", "last_message_id", "isupscmainstopicallowed"])

    for gid in registered_groups:
        if str(gid) not in existing_groups:
            new_row = {
                "srno": len(df) + 1,
                "groupid": str(gid),
                "last_message_id": None,
                "isupscmainstopicallowed": 1  
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    df.reset_index(drop=True, inplace=True)
    df["srno"] = df.index + 1

    df.to_excel(RegisteredGroupfile, index=False)

async def save_groups():
    global registered_groups
    def process_file():
        if os.path.exists(RegisteredGroupfile):
            df = pd.read_excel(RegisteredGroupfile)
            excel_groups = set(df["groupid"].dropna().astype(str))
        else:
            excel_groups = set()

        all_groups = excel_groups.union(set(map(str, registered_groups)))

        new_df = pd.DataFrame({"groupid": list(all_groups)})
        new_df.to_excel(RegisteredGroupfile, index=False)

        

    await asyncio.to_thread(process_file)

def load_groups():
    global registered_groups
    if os.path.exists(RegisteredGroupfile):
        df = pd.read_excel(RegisteredGroupfile)
        registered_groups = set(df["groupid"].dropna().astype(str).tolist())
    else:
        registered_groups = set()

def save_group_data():
    with open(group_data_file, "w") as f:
        json.dump(group_data, f)

def escape_markdown(text: str) -> str:
    return re.sub(r'([_\*\[\]\(\)~`>#+\-=|{}.!])', r'\\\1', text)

async def forceregister(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global registered_groups
        chat_id = str(update.effective_chat.id)
        
        if chat_id not in map(str, registered_groups):
            if isinstance(registered_groups, set):
                registered_groups.add(chat_id)
            else:
                registered_groups.append(chat_id)
        await save_groups()
        await update.message.reply_text("Group has been force registered successfully.")
    except Exception as e:
        await update.message.reply_text("Error while force registering the group.")
       
async def start_Command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = (
        "👋 Welcome to the Approval Bot!\n\n"
    )
    await context.bot.send_message(chat_id, text=text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = (
        "📌 Available Commands\n\n"
        "/start – Start The Bot\n"
        "/startTranslation – To Start Translation Bot\n"
        "/next – Show Next Sentence\n"
        "/forceregister – Register Forcely To Include yourself in Translation of The day Group List\n\n"
        "📝 Notes\n"
        "• Translation of the day will be sent at 10:00 AM\n"
        "📨 For any help message: @O000000000O00000000O"
    )
    await context.bot.send_message(chat_id, text=text, parse_mode="Markdown")

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global registered_groups,broadcast_url
    if not os.path.exists(RegisteredGroupfile):
        await update.message.reply_text("❗️ No registered groups found.")
        return
    df = pd.read_excel(RegisteredGroupfile)
    registered_groups = set(df["groupid"].dropna().astype(str).tolist())
    last_message_map = dict(zip(df["groupid"].astype(str), df.get("last_message_id", "")))

    chat_id = update.effective_chat.id
    if chat_id != botManagementGroupId:
        return
    if not context.args:
        await update.message.reply_text(
            "❗️ Please provide a message.\nUsage:\n/broadcastmessage Hello! Click below to start."
        )
        return
    message = update.message.text.split(" ", 1)[1]
    start_url = broadcast_url 
    keyboard = [[InlineKeyboardButton("Send Now", url=start_url)]]
    async def send_to_group(gid):
        gid_str = str(gid)
        try:
            previous_id = last_message_map.get(gid_str)
            if previous_id:
                try:
                    await context.bot.delete_message(chat_id=gid, message_id=int(previous_id))
                except Exception as e:
                    print(f"Failed to delete old message in {gid}: {e}")

            sent_msg = await context.bot.send_message(
                chat_id=gid,
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            last_message_map[gid_str] = sent_msg.message_id
            return True, gid
        except Exception as e:
            print(f"Error sending to group {gid}: {e}")
            if "bot was kicked" in str(e) or "chat not found" in str(e).lower():
                return False, gid
            return None, gid
    
    tasks = [asyncio.create_task(send_to_group(gid)) for gid in registered_groups]
    results = await asyncio.gather(*tasks)

    count = 0
    to_remove = set()
    for success, gid in results:
        if success is True:
            count += 1
            # ✅ Update last_message_id only for this group
            df.loc[df["groupid"].astype(str) == str(gid), "last_message_id"] = last_message_map[str(gid)]
        elif success is False:
            to_remove.add(str(gid))

    registered_groups -= to_remove

    # ✅ Save dataframe back with all columns preserved
    df.to_excel(RegisteredGroupfile, index=False)

    await update.message.reply_text(f"✅ Custom message sent to {count} groups.")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    global broadcast_url
    if chat_id != botManagementGroupId:
        return
    if not context.args:
        await update.message.reply_text("Send a URL.\nExample:\n/addUrl https://t.me/yourbot")
        return

    newUrl = context.args[0]
    broadcast_url = newUrl
    await update.message.reply_text(f"✅ Broadcast URL updated:\n{newUrl}")
 
async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global registered_groups
    chat = update.effective_chat
    chat_id_str = str(chat.id)
    if chat.type in ["group", "supergroup"]:
        if chat_id_str not in registered_groups:
            registered_groups.add(chat.id)
            await save_groups()

def stringify(value):
    if value is None or value == "":
        return "None"
    return str(value)


def mark_translation_sent(srno, df):
    df.loc[df['srno'] == srno, 'issent'] = 1
    df.to_excel(translation_excel_path, index=False)


def get_next_translation():
    df = pd.read_excel(translation_excel_path)

    next_row = df[df['issent'] != 1].head(1)

    if next_row.empty:
        return None, None

    row = next_row.iloc[0]

    data = {
        "srno": row["srno"],
        "hindi": row["hindi_sentence"],
        "english": row["english_translation"]
    }

    return data, df


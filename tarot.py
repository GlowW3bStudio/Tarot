import logging
import json
import os
import sys
import asyncio
import re
import random
import psycopg2
import urllib.parse
from psycopg2 import pool
from flask import Flask
from threading import Thread
from datetime import datetime, time
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, 
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, 
    filters, ConversationHandler, CallbackQueryHandler
)


# Render ရဲ့ Port Scan ကို ကျော်ဖြတ်ရန် Web Server သေးသေးလေး တည်ဆောက်ခြင်း
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Alive!"

def run_web():
    # Render က ပေးတဲ့ PORT (Default 10000) ကို သုံးမည်
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host='0.0.0.0', port=port)

def keep_alive():
    # Bot အလုပ်လုပ်နေစဉ် နောက်ကွယ်မှာ Web Server ကို Thread အနေနဲ့ Run ထားမည်
    t = Thread(target=run_web)
    t.daemon = True
    t.start()


# --- ၁။ Global Configuration ---
# Render Environment Variables မှ ဆွဲယူမည်။ မရှိပါက ပေးထားသော Default (သို့မဟုတ်) Empty သုံးမည်။
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID_STR = os.environ.get("ADMIN_ID", "7051052390")
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR.isdigit() else 7051052390
DATABASE_URL = os.environ.get("DATABASE_URL")
WELCOME_PHOTO_ID = None
DAILY_PHOTO_ID = None
# --- Credit ဆိုင်ရာ Constants များ ---
GIFT_CREDITS_LIMIT = 7
DAILY_CREDITS_LIMIT = 3
NO_CREDIT_TEXT = "စိတ်မကောင်းပါဘူးရှင်။ ဒီနေ့အတွက် အခမဲ့မေးမြန်းခွင့်လေးတွေ ကုန်ဆုံးသွားပါပြီ။ မနက်ဖြန် နေ့သစ်မှာ မေးမြန်းခွင့်အသစ်လေးတွေနဲ့အတူ ပြန်လည်ဆုံတွေ့ကြရအောင်နော်။ ✨"
# User တွေရဲ့ နှိပ်ခဲ့တဲ့ အချိန်ကို မှတ်ထားရန်
user_last_click = {}
COOLDOWN_SECONDS = 2 # ၂ စက္ကန့်ခြားမှ ဒုတိယတစ်ချက်ကို လက်ခံမည်
# Tarot ကတ် ၇၈ ချပ်ရဲ့ File ID များကို မှတ်ထားရန်
card_file_cache = {}



# Logging Setup (bot_errors.log ထဲသို့ အသေးစိတ် သိမ်းဆည်းမည်)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler("bot_errors.log"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- ၂။ Database Manager Class (Pool 1, 50) ---
class DatabaseManager:
    def __init__(self):
        self.pool = None
        self.connect()

    def connect(self):
        try:
            # URI Link ကို အသုံးပြု၍ ချိတ်ဆက်ခြင်း
            self.pool = psycopg2.pool.SimpleConnectionPool(1, 50, dsn=DATABASE_URL, sslmode='require')
            logger.info("✅ PostgreSQL Connection Pool Established.")
        except Exception as e:
            logger.critical(f"❌ DB Connection Error: {e}")
            sys.exit(1)

    def get_conn(self):
        return self.pool.getconn()

    def put_conn(self, conn):
        self.pool.putconn(conn)

# DB Manager ကို တစ်ခါတည်း သတ်မှတ်ထားခြင်း
db_mgr = DatabaseManager()

# --- ၃။ Data Loader & Validation ---
def load_assets():
    try:
        # JSON Validation
        if not os.path.exists('tarot_data.json'):
            raise FileNotFoundError("tarot_data.json file is missing!")
        with open('tarot_data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"✅ Tarot Data Loaded. Cards Count: {len([k for k in data.keys() if k.isdigit()])}")
    except Exception as e:
        logger.error(f"❌ JSON Load Error: {e}")
        sys.exit(1)

    # Image Folder Validation
    if not os.path.exists('cards/'):
        logger.warning("⚠️ 'cards/' folder not found. Image sending will fail.")
    
    return data

TAROT_DATA = load_assets()
# --- Daily Tarot Data Loader (JSON အသစ်အတွက်) ---
def load_daily_assets():
    if os.path.exists('daily_tarot.json'):
        with open('daily_tarot.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

DAILY_TAROT_DATA = load_daily_assets()

# --- Fate Guide Data Loader (Feature အသစ်အတွက်) ---
def load_guide_assets():
    if os.path.exists('guide_data.json'):
        with open('guide_data.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

GUIDE_DATA = load_guide_assets()

# --- Love Calculator Setup ---
def load_love_data():
    tarot_love_data = []
    zodiac_love_data = {}
    try:
        if os.path.exists('tarot_love.json'):
            with open('tarot_love.json', 'r', encoding='utf-8') as f:
                tarot_love_data = json.load(f)
        if os.path.exists('zodiac_love.json'):
            with open('zodiac_love.json', 'r', encoding='utf-8') as f:
                zodiac_love_data = json.load(f)
    except Exception as e:
        logger.error(f"Error loading love JSON: {e}")
    return tarot_love_data, zodiac_love_data

TAROT_LOVE_DATA, ZODIAC_LOVE_DATA = load_love_data()

ZODIAC_SIGNS = [
    "Aries", "Taurus", "Gemini", 
    "Cancer", "Leo", "Virgo", 
    "Libra", "Scorpio", "Sagittarius", 
    "Capricorn", "Aquarius", "Pisces"
]

def get_zodiac_keyboard(step="male"):
    keyboard = []
    for i in range(0, 12, 3):
        row = [InlineKeyboardButton(ZODIAC_SIGNS[i], callback_data=f"zlove_{ZODIAC_SIGNS[i]}"),
               InlineKeyboardButton(ZODIAC_SIGNS[i+1], callback_data=f"zlove_{ZODIAC_SIGNS[i+1]}"),
               InlineKeyboardButton(ZODIAC_SIGNS[i+2], callback_data=f"zlove_{ZODIAC_SIGNS[i+2]}")]
        keyboard.append(row)
    if step == "female":
        keyboard.append([InlineKeyboardButton("🔙 ယောကျ်ားလေး ရာသီခွင် ပြန်ရွေးမည်", callback_data="zlove_back_male")])
    keyboard.append([InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="love_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_review_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Review ပေးမည်", callback_data="give_review"),
         InlineKeyboardButton("✖️ မပေးဘူး", callback_data="skip_review")]
    ])
# -------------------------------
# --- ၄။ Utility & Static Functions (ဒီအောက်မှာ Logic တွေ Paste လုပ်ပါ) ---
# JSON Key များနှင့် UI မြန်မာစာသား ချိတ်ဆက်မှု (အရေအတွက် အတိအကျ)
CATEGORY_MAP = {
    "အချစ်ရေး": "love",
    "စီးပွားရေး": "work",
    "ပညာရေး": "education",
    "ကျန်းမာရေး": "health",
    "ခရီးသွားလာရေး": "travel",
    "လူမှုရေး": "social",
    "မိသားစုရေး": "family",
    "ယေဘုယျ": "general"
}

SUB_CAT_MAP = {
    # အချစ်ရေး (love)
    "Crush": "crush", "ချစ်သူ/စုံတွဲ": "couple", "ရည်းစားဟောင်း": "ex", "လူပျို/လူလွတ်": "single",
    # စီးပွားရေး (work)
    "ဝန်ထမ်း": "employee", "လုပ်ငန်းရှင်": "entrepreneur", "ငွေကြေး": "finance",
    # ပညာရေး (education)
    "စာမေးပွဲ": "exam", "ဘာသာရပ်သစ်": "new_subject", "ပညာသင်ဆု": "scholarship",
    # ကျန်းမာရေး (health)
    "ကိုယ်ကာယ": "physical", "စိတ်ကျန်းမာရေး": "mental", "ခွဲစိတ်ကုသမှု": "surgery",
    # ခရီးသွား (travel)
    "အပန်းဖြေခရီး": "vacation", "အလုပ်ကိစ္စ": "business", "ဗီဇာ/စာရွက်စာတမ်း": "visa",
    # လူမှုရေး (social)
    "မိတ်ဆွေသစ်": "new_friend", "ဆက်ဆံရေး": "harmony", "သတိထားရန်": "betrayal",
    # မိသားစု (family)
    "မိသားစုအရေး": "harmony", "အိုးအိမ်": "home", "ရင်သွေးရတနာ": "baby"
}

def calculate_zodiac(day, month):
    # Zodiac Calculation Logic (Section 4)
    zodiac_map = [
        (21, 3, 19, 4, "Aries"), (20, 4, 20, 5, "Taurus"),
        (21, 5, 20, 6, "Gemini"), (21, 6, 22, 7, "Cancer"),
        (23, 7, 22, 8, "Leo"), (23, 8, 22, 9, "Virgo"),
        (23, 9, 22, 10, "Libra"), (23, 10, 21, 11, "Scorpio"),
        (22, 11, 21, 12, "Sagittarius"), (22, 12, 19, 1, "Capricorn"),
        (20, 1, 18, 2, "Aquarius"), (19, 2, 20, 3, "Pisces")
    ]
    for d1, m1, d2, m2, name in zodiac_map:
        if (month == m1 and day >= d1) or (month == m2 and day <= d2):
            return name
    return "Pisces"

# Keyboard ကို Global အနေနဲ့ သတ်မှတ်လိုက်ခြင်း (ဒါမှ function အားလုံးက လှမ်းသုံးလို့ရမှာပါ)
main_kb = ReplyKeyboardMarkup([
    [KeyboardButton("🔮 Tarot မေးမည်"), KeyboardButton("❤️ ချစ်သူနဲ့ကိုက်ညီမှု(RS)")],
    [KeyboardButton("🎯 ကံကြမ္မာ လမ်းညွှန်"), KeyboardButton("♈ ရာသီခွင်ပင်ကိုယ်စရိုက်")],
    [KeyboardButton("🎂 မွေးနေ့မှတ်မည်"), KeyboardButton("❓ အသုံးပြုနည်း")],
    [KeyboardButton("✍️ အကြံပြုစာပို့ရန်"), KeyboardButton("📤 Share မည်")]
], resize_keyboard=True, is_persistent=True)

async def send_typing(update, context):
    """စာပြန်ခါနီးတိုင်း 'is typing...' ပေါ်စေရန်"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    
# --- ၅။ Database Initialization ---
def init_db():
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            # ၁။ Table မရှိရင် အရင်ဆောက်မည်
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                gift_credits INTEGER DEFAULT 7,
                daily_credits INTEGER DEFAULT 3,
                birthday TEXT DEFAULT '',
                zodiac TEXT DEFAULT '',
                bday_status BOOLEAN DEFAULT TRUE,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_banned BOOLEAN DEFAULT FALSE
            )''')
            
            # ၂။ Column အသစ်များကို တစ်ခုချင်းစီ စစ်ဆေးပြီး ထည့်မည် (Indentation အမှန်)
            columns_to_add = [
                ("gift_credits", "INTEGER DEFAULT 7"),
                ("daily_credits", "INTEGER DEFAULT 3"),
                ("bday_status", "BOOLEAN DEFAULT TRUE")
            ]
            for col_name, col_type in columns_to_add:
                try:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                except Exception:
                    conn.rollback() # ရှိပြီးသားဆိုရင် rollback လုပ်ပြီး ကျော်သွားမည်

            # ၃။ အခြား Table များ ဆောက်ခြင်း
            c.execute('''CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY, user_id BIGINT, rating TEXT, 
                comment TEXT, submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                
            c.execute('''CREATE TABLE IF NOT EXISTS broadcast_logs (
                id SERIAL PRIMARY KEY, user_id BIGINT, message_id BIGINT, 
                batch_id TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            conn.commit()
    finally:
        db_mgr.put_conn(conn)

init_db()

# --- ၆။ Conversation States ---
WAIT_BDAY, WAIT_CAT, WAIT_SUB, WAIT_NUMS, WAIT_FEEDBACK, WAIT_REVIEW_TEXT, WAIT_SUGGESTION_TEXT, WAIT_PERMANENT_BDAY, WAIT_ZODIAC_PERS, WAIT_GENDER_PERS, SELECT_GUIDE_TYPE, GET_GUIDE_NUM = range(12)

# --- States for Love Calculator ---
(CHOOSE_LOVE_METHOD, TAROT_LOVE_INPUT, 
 ZODIAC_MALE, ZODIAC_FEMALE, ZODIAC_CONFIRM) = range(20, 25)

async def check_ban_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """User နှိပ်လိုက်တိုင်း Ban ခံထားရခြင်း ရှိမရှိ စစ်ဆေးမည် (True=Banned, False=Normal)"""
    user_id = update.effective_user.id
    # Admin ကို ဘယ်တော့မှ မပိတ်ပါ
    if user_id == ADMIN_ID:
        return False
        
    conn = db_mgr.get_conn()
    is_banned = False
    try:
        with conn.cursor() as c:
            c.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
            res = c.fetchone()
            if res and res[0] is True:
                is_banned = True
    except Exception as e:
        logger.error(f"Ban Check Error: {e}")
    finally:
        db_mgr.put_conn(conn)
        
    if is_banned:
        # Ban ခံရသူဆိုလျှင် ဤစာသာ ပြမည်
        if update.message:
            await update.message.reply_text("❌ လူကြီးမင်းသည် စည်းကမ်းဖောက်ဖျက်မှုကြောင့် အသုံးပြုခွင့် ခေတ္တပိတ်ပင်ခံထားရပါသည်ရှင်။")
        elif update.callback_query:
            await update.callback_query.answer("❌ အသုံးပြုခွင့် ပိတ်ပင်ခံထားရပါသည်။", show_alert=True)
        return True
        
    return False


# 🌟 အသစ်ထပ်ထည့်ထားသော Cancel Function 🌟
async def cancel_tarot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_text = " မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ သာယာပျော်ရွှင်တဲ့ နေ့လေးတစ်နေ့ ဖြစ်ပါစေရှင်။ ✨"
    try: await query.message.delete()
    except: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text=cancel_text, reply_markup=main_kb)
    return ConversationHandler.END


#Creditစစ်တဲ့အပိုင်း
async def check_user_credits(user_id):
    if user_id == ADMIN_ID: return True
        
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            # ၁။ အရင်ဆုံး User ရှိမရှိ စစ်မယ်၊ မရှိရင် အသစ်ထည့်ပြီး Credit 7, 3 တန်းပေးမယ်
            c.execute("""
                INSERT INTO users (user_id, gift_credits, daily_credits) 
                VALUES (%s, %s, %s) 
                ON CONFLICT (user_id) DO NOTHING
            """, (user_id, GIFT_CREDITS_LIMIT, DAILY_CREDITS_LIMIT))
            conn.commit()

            # ၂။ ပြီးမှ Credit ကို ပြန်စစ်မယ်
            c.execute("SELECT gift_credits, daily_credits FROM users WHERE user_id = %s", (user_id,))
            res = c.fetchone()
            if res and (res[0] > 0 or res[1] > 0):
                return True
    except Exception as e:
        logger.error(f"Credit Check Error: {e}")
    finally:
        db_mgr.put_conn(conn)
    return False

async def deduct_credit(user_id):
    if user_id == ADMIN_ID: return
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT gift_credits, daily_credits FROM users WHERE user_id = %s", (user_id,))
            res = c.fetchone()
            if res:
                gift, daily = res[0], res[1]
                if gift > 0:
                    c.execute("UPDATE users SET gift_credits = gift_credits - 1 WHERE user_id = %s", (user_id,))
                elif daily > 0:
                    c.execute("UPDATE users SET daily_credits = daily_credits - 1 WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Credit Deduction Error: {e}")
    finally:
        db_mgr.put_conn(conn)

async def manage_bday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """မွေးနေ့ မှတ်သား/ဖျက်သိမ်းရန် ခလုတ်ပြသခြင်း"""
    if await check_ban_status(update, context): return ConversationHandler.END
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ မွေးနေ့အသစ် မှတ်သားမည်", callback_data="save_bday_start")],
        [InlineKeyboardButton("🗑 မှတ်ထားတာ ပြန်ဖျက်မည်", callback_data="delete_bday_data")]
    ])
    
    await update.message.reply_text(
        "လူကြီးမင်းရဲ့ မွေးနေ့ကို မှတ်ထားပေးစေချင်ပါသလားရှင်။\n"
        "မှတ်ထားပါက Tarot မေးတဲ့အခါတိုင်း မွေးနေ့ကို ပြန်ရိုက်စရာ မလိုတော့ပါဘူးရှင်။ ✨",
        reply_markup=keyboard
    )
    return WAIT_PERMANENT_BDAY

async def zodiac_personality_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ရာသီခွင် ရွေးချယ်ခိုင်းသည့် Entry Point Function"""
    if await check_ban_status(update, context): return ConversationHandler.END
    await send_typing(update, context)
    
    keyboard = []
    # ZODIAC_SIGNS စာရင်းမှတစ်ဆင့် ၃ ခုတစ်တန်း Inline Button စီခြင်း
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(ZODIAC_SIGNS[i], callback_data=f"pers_{ZODIAC_SIGNS[i]}"),
            InlineKeyboardButton(ZODIAC_SIGNS[i+1], callback_data=f"pers_{ZODIAC_SIGNS[i+1]}"),
            InlineKeyboardButton(ZODIAC_SIGNS[i+2], callback_data=f"pers_{ZODIAC_SIGNS[i+2]}")
        ]
        keyboard.append(row)
    # Cancel Button ထည့်သွင်းခြင်း
    keyboard.append([InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")])
    
    await update.message.reply_text(
        "🔮 လူကြီးမင်းရဲ့ ထူးခြားလှတဲ့ ပင်ကိုယ်စရိုက်လေးတွေကို သိရှိနိုင်ဖို့အတွက် ရာသီခွင်လေးကို အရင်ဆုံး ရွေးချယ်ပေးပါဦးရှင်။ ✨",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAIT_ZODIAC_PERS

async def handle_zodiac_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ရွေးလိုက်သည့် ရာသီခွင်ကို မှတ်သားပြီး ကျား/မ ဆက်မေးခြင်း"""
    query = update.callback_query
    await query.answer()
    
    # Callback data မှ ရာသီခွင်အမည် (Aries, Taurus, etc.) ကို ယူခြင်း
    zodiac_name = query.data.split('_')[1]
    context.user_data['selected_pers_zodiac'] = zodiac_name
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👦🏻 ကျား", callback_data="pers_gender_male"),
         InlineKeyboardButton("👧🏻 မ", callback_data="pers_gender_female")],
        [InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")]
    ])
    
    # Thura ရွေးချယ်ထားသော Option 2 စာသားကို အသုံးပြုခြင်း
    await query.edit_message_text(
        f"✨ {zodiac_name} ရာသီဖွား လူကြီးမင်းရဲ့ စိတ်နေစိတ်ထားလေးတွေကို အသေးစိတ် ဖော်ပြပေးနိုင်ဖို့အတွက် လိင် (Gender) လေးကို အောက်မှာ ရွေးချယ်ပေးပါဦးရှင်။ ✨",
        reply_markup=keyboard
    )
    return WAIT_GENDER_PERS

async def handle_gender_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """JSON မှ Data ကိုဆွဲထုတ်ပြီး ချိုချိုသာသာဖြင့် ဟောစာတမ်းပြသခြင်း"""
    query = update.callback_query
    await query.answer()
    
    # Gender နှင့် Zodiac ကို User Data မှ ပြန်ယူခြင်း
    gender = "male" if query.data == "pers_gender_male" else "female"
    zodiac = context.user_data.get('selected_pers_zodiac')
    
    # zodiac_personality.json ဖိုင်မှ data ကို ဖတ်ခြင်း
    if os.path.exists('zodiac_personality.json'):
        with open('zodiac_personality.json', 'r', encoding='utf-8') as f:
            all_data = json.load(f)
    else:
        await query.edit_message_text("❌ ဟောစာတမ်းဖိုင်ကို ရှာမတွေ့ပါသဖြင့် ခဏစောင့်ပေးပါရှင်။")
        return ConversationHandler.END

    # JSON Key များနှင့် တိုက်စစ်၍ Data ဆွဲထုတ်ခြင်း
    z_info = all_data.get(zodiac, {}).get(gender, {})
    
    if not z_info:
        await query.edit_message_text("❌ စာသားများ ပြင်ဆင်နေဆဲ ဖြစ်ပါသည်ရှင်။")
        return ConversationHandler.END

    # ဟောစာတမ်းစာသားကို ပေါင်းစပ်ခြင်း (Key များ: personality, speech, relationship, single)
    result_text = (
        f"♈ *{zodiac} ရာသီဖွား ({'အမျိုးသား' if gender == 'male' else 'အမျိုးသမီး'}) ၏ ပင်ကိုယ်စရိုက်*\n\n"
        f"{z_info.get('personality', '')}\n\n"
        f"🗣️ *အပြောအဆိုနှင့် ဆက်ဆံရေး*\n{z_info.get('speech', '')}\n\n"
        f"💖 *အချစ်ရေးနှင့် လိုက်ဖက်သူ*\n{z_info.get('relationship', '')}\n\n"
        f"🤘 *{'လူပျိုဘဝ' if gender == 'male' else 'အပျိုဘဝ'} အမြင်*\n{z_info.get('single', '')}\n\n"
        f"✨ လူကြီးမင်းရဲ့ ဘဝခရီးလမ်းမှာ ပျော်ရွှင်ချမ်းမြေ့ပါစေရှင်။ ✨"
    )
    
    await query.edit_message_text(result_text, parse_mode="Markdown")
    
    # Review တောင်းခြင်း
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="ဟောစာတမ်းလေးကို သဘောကျတယ်ဆိုရင် Review လေး ပေးခဲ့ပါဦးရှင်။ ✨",
        reply_markup=get_review_keyboard()
    )
    return WAIT_FEEDBACK

async def bday_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """မှတ်မည် သို့မဟုတ် ဖျက်မည်ကို ကိုင်တွယ်ခြင်း"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "delete_bday_data":
        conn = db_mgr.get_conn()
        try:
            with conn.cursor() as c:
                c.execute("UPDATE users SET bday_status = FALSE WHERE user_id = %s", (user_id,))
            conn.commit()
            await query.edit_message_text("🗑 လူကြီးမင်း မှတ်သားထားတဲ့ မွေးနေ့ဒေတာတွေကို အောင်မြင်စွာ ဖျက်သိမ်းလိုက်ပါပြီရှင်။")
        finally:
            db_mgr.put_conn(conn)
        return ConversationHandler.END
        
    elif query.data == "save_bday_start":
        await query.edit_message_text(
            "မှတ်သားထားမည့် မွေးသက္ကရာဇ်ကို *(နေ့/လ/ခုနှစ်)* ပုံစံအတိုင်း ရိုက်ပို့ပေးပါဦးရှင်။ ✨\nဥပမာ - *25/12/1995*",
            parse_mode="Markdown"
        )
        return WAIT_PERMANENT_BDAY

async def handle_permanent_bday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ရိုက်လိုက်တဲ့ မွေးနေ့ကို အသေးစိတ်စစ်ဆေးပြီး Database မှာ အမြဲတမ်း သိမ်းခြင်း"""
    await send_typing(update, context)
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # (၁) Format နှင့် စာသားပါဝင်မှု စစ်ဆေးခြင်း (Regex ဖြင့် သန့်စင်ခြင်း)
    clean_text = re.sub(r'[/,\-၊\.]', ' ', text).strip()
    parts = clean_text.split()
    
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        await update.message.reply_text("❌ မွေးသက္ကရာဇ်ကို စာသားများဖြင့် ရေးသား၍မရပါ။ (ဥပမာ - 15/8/1998) ပုံစံအတိုင်း မှန်ကန်စွာ ပြန်လည်ရိုက်နှိပ်ပေးပါရှင်။")
        return WAIT_PERMANENT_BDAY
        
    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    
    # Year က ရှေ့ရောက်နေရင် ပြန်စီပေးခြင်း
    if year < 100 and day > 1900:
        day, year = year, day

    # (၂) လ (Month) မှန်ကန်မှု စစ်ဆေးခြင်း
    if month < 1 or month > 12:
        await update.message.reply_text("❌ လ (Month) မှားယွင်းနေပါသည်။ လသည် ၁ မှ ၁၂ အတွင်းသာ ဖြစ်ရပါမည်။")
        return WAIT_PERMANENT_BDAY

    # (၃) ရက် (Day) နှင့် Leap Year စစ်ဆေးခြင်း
    days_in_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    is_leap_year = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
    if is_leap_year:
        days_in_month[2] = 29

    max_days = days_in_month.get(month, 31)
    if day < 1 or day > max_days:
        if month == 2:
            err_msg = f"❌ ရက် (Day) မှားနေပါသည်။ {year} ခုနှစ်သည် ရက်ထပ်နှစ်ဖြစ်၍ ၂ လပိုင်းတွင် ၂၉ ရက်သာ အများဆုံး ရှိပါသည်ရှင်。" if is_leap_year else f"❌ ရက် (Day) မှားနေပါသည်။ {year} ခုနှစ်သည် ရက်ထပ်နှစ်မဟုတ်သဖြင့် ၂ လပိုင်းတွင် ၂၈ ရက်သာ အများဆုံး ရှိပါသည်ရှင်။"
        else:
            err_msg = f"❌ ရက် (Day) မှားနေပါသည်။ {month} လပိုင်းတွင် {max_days} ရက်သာ အများဆုံး ရှိပါသည်ရှင်။"
        await update.message.reply_text(err_msg)
        return WAIT_PERMANENT_BDAY

    # (၄) ခုနှစ် (Year) နှင့် အနာဂတ်ရက်စွဲ စစ်ဆေးခြင်း
    current_date = datetime.now()
    try:
        input_date = datetime(year, month, day)
    except ValueError:
        await update.message.reply_text("❌ မှားယွင်းသော ရက်စွဲဖြစ်ပါသည်။ ပြန်လည်စစ်ဆေးပေးပါရှင်။")
        return WAIT_PERMANENT_BDAY

    if year < 1926 or input_date > current_date:
        await update.message.reply_text("❌ ခုနှစ်မှားယွင်းနေပါသည်။ ၁၉၂၆ အထက် သို့မဟုတ် လက်ရှိအချိန်ထက် မကျော်လွန်သော ရက်စွဲဖြစ်ရပါမည်။")
        return WAIT_PERMANENT_BDAY

    # (၅) Zodiac တွက်ချက်ခြင်း
    zodiac = calculate_zodiac(day, month)
    context.user_data['zodiac'] = zodiac # Session ထဲ ခေတ္တသိမ်းသည်

    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE users SET birthday = %s, zodiac = %s, bday_status = TRUE WHERE user_id = %s", (text, zodiac, user_id))
        conn.commit()
        
        success_text = (
            f"✅ လူကြီးမင်း၏ မွေးနေ့နှင့် {zodiac} ရာသီဖွားဖြစ်ကြောင်း အောင်မြင်စွာ မှတ်သားပြီးပါပြီရှင်။ ✨\n\n"
            f"ယခုမှစ၍ Tarot မေးတဲ့အခါတိုင်း မွေးနေ့ကို ထပ်ခါထပ်ခါ ရိုက်စရာ မလိုတော့ပါဘူးရှင်။"
        )
        await update.message.reply_text(success_text, reply_markup=main_kb)
    except Exception as e:
        logger.error(f"Database error in handle_permanent_bday: {e}")
        await update.message.reply_text("❌ အချက်အလက်သိမ်းဆည်းရာတွင် အခက်အခဲရှိနေပါသည်။")
    finally:
        db_mgr.put_conn(conn)

    return ConversationHandler.END

async def guide_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_ban_status(update, context): return ConversationHandler.END
    await send_typing(update, context)
    keyboard = [
        [InlineKeyboardButton("✅ လုပ်ပြီးသားကိစ္စရလဒ်", callback_data="guide_outcome"),
         InlineKeyboardButton("💡 ရှေ့ဆက်လမ်းညွှန်ချက်", callback_data="guide_advice")],
        [InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")]
    ]
    await update.message.reply_text(
        "လူကြီးမင်း သိလိုတဲ့ အကြောင်းအရာအတွက် ဘယ်လိုမျိုး လမ်းညွှန်ပေးရမလဲရှင်? ✨\n\n"
        "လုပ်ဆောင်ပြီးသား ကိစ္စတစ်ခုအတွက် 'ရလဒ်' ကို သိချင်တာလား၊ "
        "ဒါမှမဟုတ် ရှေ့ဆက်လုပ်ဆောင်မယ့် အရာအတွက် 'လမ်းညွှန်ချက်' ကို သိချင်တာလားရှင်?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_GUIDE_TYPE

async def handle_guide_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['guide_mode'] = query.data 
    
    # User အာရုံစိုက်မှု ရရှိစေမည့် Psychology Logic ပါဝင်သော စာသား
    await query.edit_message_text(
        "ကောင်းပါပြီရှင်။ ✨ လူကြီးမင်း သိလိုတဲ့ အကြောင်းအရာကို စိတ်ထဲမှာ အေးအေးဆေးဆေး အာရုံပြုလိုက်ပါရှင်။ "
        "ပြီးရင်တော့ လူကြီးမင်းရဲ့ ပင်ကိုယ်သိစိတ်က ပထမဆုံး ရွေးချယ်လိုက်တဲ့ ၁ မှ ၇၈ ကြား ဂဏန်း (၁) ခု ကို ရိုက်ပို့ပေးပါဦးရှင်။"
    )
    return GET_GUIDE_NUM

async def process_guide_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    num_text = update.message.text.strip()
    
    if not num_text.isdigit() or not (1 <= int(num_text) <= 78):
        await update.message.reply_text("❌ ကျေးဇူးပြု၍ ၁ မှ ၇၈ ကြား ဂဏန်းတစ်ခုကိုပဲ ရိုက်ပို့ပေးပါရှင်။")
        return GET_GUIDE_NUM

    wait_msg = await update.message.reply_text("⏳ စွမ်းအင်များကို ချိန်ညှိတွက်ချက်နေပါပြီရှင်...")

    # Logic: User ဘာရိုက်ရိုက် ကတ်ကို Random ရွေးမည်
    selected_card_id = str(random.randint(1, 78)) 
    
    mode = context.user_data.get('guide_mode')
    card_info = GUIDE_DATA.get(selected_card_id, {})
    tarot_info = TAROT_DATA.get(selected_card_id, {})
    card_name = tarot_info.get("name", f"Card {selected_card_id}")
    img_path = f"cards/{selected_card_id}.jpg"

    # % ကို JSON ထဲကအတိုင်း တိုက်ရိုက်ယူမည်
    energy = card_info.get("energy_score", "??")

    if mode == "guide_outcome":
        result_msg = card_info.get("outcome_msg", "ရလဒ်စာသား ပြင်ဆင်နေဆဲဖြစ်ပါတယ်ရှင်။")
        header = "🎯 **ရလဒ်**"
    else:
        result_msg = card_info.get("advice_msg", "လမ်းညွှန်ချက်စာသား ပြင်ဆင်နေဆဲဖြစ်ပါတယ်ရှင်။")
        header = "💡 **ရှေ့ဆက်လမ်းညွှန်ချက်**"

    await wait_msg.delete()
    if os.path.exists(img_path):
        caption = f"✨ {update.effective_user.full_name} ရွေးချယ်လိုက်သောကတ် - **{card_name}**"
        await update.message.reply_photo(
            photo=open(img_path, 'rb'), 
            caption=caption, 
            parse_mode="Markdown",
            write_timeout=60 # Timeout ထည့်သွင်းခြင်း
        )
    
    await asyncio.sleep(1)

    # Thura ရဲ့ မူရင်း မှတ်ချက်စာသား
    note_text = (
        f"*(မှတ်ချက် - ဤလမ်းညွှန်ချက်သည် လက်ရှိအခြေအနေအပေါ် အခြေခံထားသော အကြံပြုချက်သာ ဖြစ်ပါသည်။ "
        f"လူကြီးမင်း၏ ရှေ့ဆက်ရမည့် ကြိုးစားအားထုတ်မှုနှင့် မှန်ကန်သော ဆုံးဖြတ်ချက်များကသာ "
        f"ပိုမိုကောင်းမွန်သော အနာဂတ်ရလဒ်များကို ဖန်တီးပေးနိုင်မှာ ဖြစ်ပါတယ်ရှင်)*"
    )

    final_text = (f"{header}\n\n📝 {result_msg}\n\n✨ စွမ်းအင်အဆင့်: {energy}%\n\n{note_text}")
    
    await update.message.reply_text(
        final_text, 
        parse_mode="Markdown", 
        reply_markup=main_kb,
        write_timeout=60 # Timeout ထည့်သွင်းခြင်း
    )
    return ConversationHandler.END
    

async def tarot_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_ban_status(update, context): return ConversationHandler.END
    user_id = update.effective_user.id


    # --- 💡 Silent Cooldown Logic ---
    now = datetime.now()
    if user_id in user_last_click:
        if (now - user_last_click[user_id]).total_seconds() < COOLDOWN_SECONDS:
            return # ဘာစာမှမပို့ဘဲ တိတ်တဆိတ် ရပ်လိုက်ခြင်း
    user_last_click[user_id] = now
    # -------------------------------
    
    
    # အဆင့် ၁: Credit ရှိ၊ မရှိ Check Only လုပ်ခြင်း
    if not await check_user_credits(user_id):
        await update.message.reply_text(NO_CREDIT_TEXT)
        return ConversationHandler.END

    await send_typing(update, context)

  
    # --- 💡 ဤနေရာတွင် Database မှ မွေးနေ့ရှိမရှိ စစ်ဆေးမည် ---
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            # bday_status ကိုပါ ဆွဲယူစစ်ဆေးပါ
            c.execute("SELECT birthday, zodiac, bday_status FROM users WHERE user_id = %s", (user_id,))
            res = c.fetchone()
            # res[2] (bday_status) က TRUE ဖြစ်မှသာ မွေးနေ့မတောင်းဘဲ ကျော်ခိုင်းမည်
            if res and res[0] and res[1] and res[2] is True:
                context.user_data['birthday'] = res[0]
                context.user_data['zodiac'] = res[1] 
                return await show_categories(update, context)
    finally:
        db_mgr.put_conn(conn)
        
        # 🌟 Cancel Button ထည့်သွင်းထားပါသည် 🌟
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")]])
    await update.message.reply_text(
            "🔮 ဟောစာတမ်း တွက်ချက်ပေးဖို့အတွက် လူကြီးမင်းရဲ့ မွေးသက္ကရာဇ်လေး သိပါရစေရှင်။ \n"
            "ကျေးဇူးပြုပြီး *(နေ့/လ/ခုနှစ်)* ပုံစံလေးအတိုင်း မှန်ကန်အောင် ရိုက်ပို့ပေးပါဦးနော်။\n"
            "ဥပမာ - *25/12/1995*",
                parse_mode="Markdown", reply_markup=cancel_markup
    )
    return WAIT_BDAY
        
        
async def love_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """❤️ ချစ်သူနဲ့ကိုက်ညီမှု(RS) ခလုတ် နှိပ်လျှင် စတင်မည့်နေရာ"""
    # Ban ခံထားရသူဆိုလျှင် ဝင်ခွင့်မပေးပါ
    if await check_ban_status(update, context): return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("🃏 Tarot ကဒ်ဖြင့်", callback_data="love_method_tarot"),
         InlineKeyboardButton("♈ ရာသီခွင်ဖြင့်", callback_data="love_method_zodiac")],
        [InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="love_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "လူကြီးမင်းတို့ရဲ့ အချစ်ရေးကံကြမ္မာကို ဘယ်နည်းလမ်းနဲ့ တွက်ချက်စစ်ဆေးချင်ပါသလဲရှင်။ နှစ်သက်ရာ နည်းလမ်းတစ်ခုကို အောက်မှာ ရွေးချယ်ပေးပါရှင်。"
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return CHOOSE_LOVE_METHOD

async def love_method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "love_cancel":
        await query.edit_message_text("ဟုတ်ကဲ့ပါရှင်။ မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ သာယာပျော်ရွှင်တဲ့ နေ့လေးတစ်နေ့ ဖြစ်ပါစေရှင်။ ✨")
        return ConversationHandler.END

        
    if query.data == "love_method_tarot":
        keyboard = [[InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="love_cancel")]]
        await query.edit_message_text("လူကြီးမင်းတို့ နှစ်ဦး၏ အချစ်ရေးခရီးလမ်းအတွက် ၁ မှ ၇၈ အတွင်းရှိ ဂဏန်း (၁) ခုကို ရိုက်ထည့်ပေးပါရှင်", reply_markup=InlineKeyboardMarkup(keyboard))
        return TAROT_LOVE_INPUT
        
    elif query.data == "love_method_zodiac":
        await query.edit_message_text("ကျေးဇူးပြု၍ ယောကျ်ားလေး၏ ရာသီခွင်ကို အောက်ပါစာရင်းမှ ရွေးချယ်ပေးပါရှင် 👦🏻", reply_markup=get_zodiac_keyboard("male"))
        return ZODIAC_MALE

async def love_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("ဟုတ်ကဲ့ပါရှင်။ မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ သာယာပျော်ရွှင်တဲ့ နေ့လေးတစ်နေ့ ဖြစ်ပါစေရှင်။ ✨")
    return ConversationHandler.END

# ==========================================
# 🃏 TAROT PATH
# ==========================================
async def tarot_love_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    # Validation စစ်ဆေးခြင်း
    if not text.isdigit() or not (1 <= int(text) <= 78):
        await update.message.reply_text("❌ မှားယွင်းနေပါသည်။ ကျေးဇူးပြု၍ ၁ မှ ၇၈ အတွင်းရှိ ဂဏန်းတစ်ခုကိုသာ ရိုက်ထည့်ပေးပါရှင်")
        return TAROT_LOVE_INPUT

    if not await check_user_credits(user_id):
        await update.message.reply_text(NO_CREDIT_TEXT)
        return ConversationHandler.END
        
    # ၃။ Credit ၁ ခု ဖြတ်ခြင်း
    await deduct_credit(user_id)
    

    # ၄။ Typing နှင့် ⏳ Wait Message ပြခြင်း
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    wait_msg = await update.message.reply_text("⏳ အချစ်ရေး ဟောကိန်း တွက်ချက်ပေးနေပါသည်... ခေတ္တစောင့်ဆိုင်းပေးပါရှင်...")
    await asyncio.sleep(2) 
    
# ၅။ Random ကဒ်ရွေးချယ်ခြင်း (Data ရှိမရှိ အရင်စစ်မည်)
    if TAROT_LOVE_DATA:
        card = random.choice(TAROT_LOVE_DATA)
    else:
        # Data မရှိသေးလျှင် ပြမည့် Default
        card = {
            "name": "The Lovers (ချစ်သူများ)", 
            "image_url": "https://upload.wikimedia.org/wikipedia/en/d/de/RWS_Tarot_06_Lovers.jpg", 
            "love_meaning": "💖 ကောင်းမွန်သော အချစ်ရေးကံကြမ္မာ ဖြစ်ပါသည်။"
        }

    # ၆။ Wait Message ဖျက်ခြင်း
    await wait_msg.delete()
    
    # ၇။ ပုံနှင့် Caption (User ၏ Full Name) ကို အရင်ပို့ခြင်း
    caption = f"✨ {update.effective_user.full_name} ရရှိသောကဒ်"
    await update.message.reply_photo(photo=card['image_url'], caption=caption, write_timeout=60, read_timeout=60)
    
# ၈။ ဟောစာတမ်းနှင့် Review ခလုတ် သီးသန့်ပို့ခြင်း
    pred_text = f"🃏 ကဒ်အမည် - *{card['name']}*\n\n📜 *ဟောစာတမ်း*\n{card['love_meaning']}"
    await update.message.reply_text(pred_text, parse_mode="Markdown", write_timeout=60, read_timeout=60)
    
    # Review တောင်းခြင်း (မူလ handle_feedback_choice ဆီသို့ ပို့မည်)
    await update.message.reply_text("Review ပေးလိုပါသလားရှင်?", reply_markup=get_review_keyboard())
    return WAIT_FEEDBACK

# ==========================================
# ♈ ZODIAC PATH
# ==========================================
async def zodiac_male_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "love_cancel":
        await query.edit_message_text("ဟုတ်ကဲ့ပါရှင်။ မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ သာယာပျော်ရွှင်တဲ့ နေ့လေးတစ်နေ့ ဖြစ်ပါစေရှင်။ ✨")
        return ConversationHandler.END

        
    sign = query.data.split('_')[1]
    context.user_data['male_zodiac'] = sign
    
    # မိန်းကလေး ဆက်ရွေးခိုင်းမည်
    await query.edit_message_text("ကျေးဇူးပြု၍ မိန်းကလေး၏ ရာသီခွင်ကို ဆက်လက် ရွေးချယ်ပေးပါရှင် 👧🏻", reply_markup=get_zodiac_keyboard("female"))
    return ZODIAC_FEMALE

async def zodiac_female_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "love_cancel":
        await query.edit_message_text("ဟုတ်ကဲ့ပါရှင်။ မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ သာယာပျော်ရွှင်တဲ့ နေ့လေးတစ်နေ့ ဖြစ်ပါစေရှင်။ ✨")
        return ConversationHandler.END

    elif query.data == "zlove_back_male":
        await query.edit_message_text("ကျေးဇူးပြု၍ ယောကျ်ားလေး၏ ရာသီခွင်ကို အောက်ပါစာရင်းမှ ရွေးချယ်ပေးပါရှင် 👦🏻", reply_markup=get_zodiac_keyboard("male"))
        return ZODIAC_MALE
        
    sign = query.data.split('_')[1]
    context.user_data['female_zodiac'] = sign
    
    # အတည်ပြုချက် တောင်းခံခြင်း (Confirmation)
    male = context.user_data['male_zodiac']
    female = sign
    text = (f"လူကြီးမင်း ရွေးချယ်ထားသော ရာသီခွင်များမှာ အောက်ပါအတိုင်း ဖြစ်ပါသည် -\n\n"
            f"👦🏻 ယောကျ်ားလေး: {male}\n"
            f"👧🏻 မိန်းကလေး: {female}\n\n"
            f"မှန်ကန်ပါသလားရှင်?")
            
    keyboard = [
        [InlineKeyboardButton("✅ မှန်ကန်ပါသည်", callback_data="zlove_confirm_yes")],
        [InlineKeyboardButton("🔙 ရာသီခွင် ပြန်ရွေးမည်", callback_data="zlove_back_male")],
        [InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="love_cancel")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ZODIAC_CONFIRM

async def zodiac_confirm_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    if query.data == "zlove_confirm_yes":
        if not await check_user_credits(user_id):
            await query.edit_message_text(NO_CREDIT_TEXT)
            return ConversationHandler.END

        await deduct_credit(user_id)
        await query.edit_message_text("⏳ အချစ်ရေး ဟောကိန်း တွက်ချက်ပေးနေပါသည်... ခေတ္တစောင့်ဆိုင်းပေးပါရှင်...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        await asyncio.sleep(2)
        
        male = context.user_data.get('male_zodiac')
        female = context.user_data.get('female_zodiac')
        result_data = ZODIAC_LOVE_DATA.get(f"{male}_{female}")
        
        if not result_data:
            result_data = {"percent": "၈၀%", "strength": "နားလည်မှုရှိသည်။", "weakness": "သည်းခံရန်လိုသည်။", "advice": "ပွင့်လင်းစွာ တိုင်ပင်ပါ။"}

        final_text = (f"✨ *{male} နှင့် {female} တွဲဖက်ညီမှု ဟောစာတမ်း*\n\n💘 ကိုက်ညီမှု ရာခိုင်နှုန်း: {result_data['percent']}\n\n💪 အားသာချက်: {result_data['strength']}\n⚠️ သတိထားရန်: {result_data['weakness']}\n💡 အကြံပြုချက်: {result_data['advice']}")
        await query.edit_message_text(final_text, parse_mode="Markdown")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Review ပေးလိုပါသလားရှင်?", reply_markup=get_review_keyboard())
        return WAIT_FEEDBACK

    elif query.data == "love_cancel":
        await query.edit_message_text("ဟုတ်ကဲ့ပါရှင်။ မေးမြန်းမှုကို ရပ်နားလိုက်ပါပြီ။ ✨")
        return ConversationHandler.END
    elif query.data == "zlove_back_male":
        await query.edit_message_text("ကျေးဇူးပြု၍ ယောကျ်ားလေး၏ ရာသီခွင်ကို ရွေးချယ်ပေးပါရှင် 👦🏻", reply_markup=get_zodiac_keyboard("male"))
        return ZODIAC_MALE

        

async def handle_bday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    """User ထံမှ မွေးသက္ကရာဇ်ကို လက်ခံပြီး အသေးစိတ် စစ်ဆေးမည့်အပိုင်း (Error ၆ မျိုး)"""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # (၁) စာသားပါဝင်မှုနှင့် Format စစ်ဆေးခြင်း
    clean_text = re.sub(r'[/,\-၊\.]', ' ', text).strip()
    parts = clean_text.split()

    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        await update.message.reply_text("❌ မွေးသက္ကရာဇ်ကို စာသားများဖြင့် ရေးသား၍မရပါ။ ဂဏန်း ၃ ခုဖြင့်သာ (ဥပမာ - 15/8/1998) မှန်ကန်စွာ ရိုက်ထည့်ပေးပါရှင်။")
        return WAIT_BDAY

    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    if year < 100 and day > 1900:
        day, year = year, day

    if month < 1 or month > 12:
        await update.message.reply_text("❌ လ (Month) မှားယွင်းနေပါသည်။ လသည် ၁ မှ ၁၂ အတွင်းသာ ဖြစ်ရပါမည်ရှင်။")
        return WAIT_BDAY

    days_in_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    is_leap_year = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
    if is_leap_year:
        days_in_month[2] = 29

    max_days = days_in_month[month]
    if day < 1 or day > max_days:
        if month == 2:
            err_msg = f"❌ ရက် (Day) မှားယွင်းနေပါသည်။ {year} ခုနှစ်သည် ရက်ထပ်နှစ်ဖြစ်သဖြင့် ၂ လပိုင်းတွင် ၂၉ ရက်သာ အများဆုံး ရှိပါသည်ရှင်。" if is_leap_year else f"❌ ရက် (Day) မှားယွင်းနေပါသည်။ {year} ခုနှစ်သည် ရက်ထပ်နှစ်မဟုတ်သဖြင့် ၂ လပိုင်းတွင် ၂၈ ရက်သာ အများဆုံး ရှိပါသည်ရှင်။"
        else:
            err_msg = f"❌ ရက် (Day) မှားယွင်းနေပါသည်။ သင်ရွေးချယ်သော {month} လပိုင်းတွင် {max_days} ရက်သာ အများဆုံး ရှိပါသည်ရှင်။"
        await update.message.reply_text(err_msg)
        return WAIT_BDAY

    current_date = datetime.now()
    try:
        input_date = datetime(year, month, day)
    except ValueError:
        await update.message.reply_text("❌ မှားယွင်းသော ရက်စွဲဖြစ်ပါသည်။ ကျေးဇူးပြု၍ ပြန်လည်စစ်ဆေးပေးပါရှင်။")
        return WAIT_BDAY

    if year < 1926 or input_date > current_date:
        await update.message.reply_text("❌ ခုနှစ် (Year) မှားယွင်းနေပါသည်။ ၁၉၂၆ ခုနှစ်အောက် သို့မဟုတ် လက်ရှိအချိန်ထက် ကျော်လွန်နေသော အနာဂတ်ရက်စွဲများကို လက်မခံပါရှင်။")
        return WAIT_BDAY

    # Zodiac တွက်ချက်ခြင်း
    zodiac = calculate_zodiac(day, month)
    context.user_data['zodiac'] = zodiac # Session (user_data) ထဲမှာပဲ သိမ်းမည်

    # အောင်မြင်ကြောင်းစာပို့ပြီး Category ဆီ တန်းသွားမည်
    # 💡 ၂။ handle_bday Function ရဲ့ အောက်ဆုံးနားက Keyboard နေရာကိုလည်း အောက်ပါအတိုင်း ပြင်ပါ
    # (မွေးနေ့ရိုက်ပြီးသွားရင် ပေါ်လာမယ့် ခလုတ်များ)

    await update.message.reply_text(
        f"မွေးသက္ကရာဇ်လေး မှတ်သားပေးထားပါတယ်ရှင်။ 😊\n"
        f"လူကြီးမင်းဟာ {zodiac} ရာသီဖွား တစ်ဦး ဖြစ်ပါတယ်ရှင်။ ✨\n\n"
        f"အောက်ပါစာရင်းလေးကနေ သိလိုတဲ့ကဏ္ဍကို ဆက်လက် ရွေးချယ်ပေးပါဦးနော်။ 👇",
        reply_markup=main_kb
    )
    context.user_data['birthday'] = text
    # Category ရွေးခိုင်းသည့် function ကို လှမ်းခေါ်လိုက်ခြင်း
    return await show_categories(update, context)
    
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Category များကို စာသားအောက်တွင် Inline Button အနေဖြင့် ပြသခြင်း"""
    # 🌟 နောက်ဆုံး Row အဖြစ် Cancel Button ထည့်သွင်းထားပါသည် 🌟
    keyboard = [
        [InlineKeyboardButton("အချစ်ရေး", callback_data="အချစ်ရေး"), InlineKeyboardButton("စီးပွားရေး", callback_data="စီးပွားရေး")],
        [InlineKeyboardButton("ပညာရေး", callback_data="ပညာရေး"), InlineKeyboardButton("ကျန်းမာရေး", callback_data="ကျန်းမာရေး")],
        [InlineKeyboardButton("ခရီးသွားလာရေး", callback_data="ခရီးသွားလာရေး"), InlineKeyboardButton("လူမှုရေး", callback_data="လူမှုရေး")],
        [InlineKeyboardButton("မိသားစုရေး", callback_data="မိသားစုရေး"), InlineKeyboardButton("ယေဘုယျ", callback_data="ယေဘုယျ")],
        [InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Message သို့မဟုတ် Callback ကနေလာတာကို ခွဲခြားကိုင်တွယ်ခြင်း
    if update.message:
        await update.message.reply_text("🔮 သိလိုသော ကဏ္ဍကို ရွေးချယ်ပေးပါရှင် -", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text("🔮 သိလိုသော ကဏ္ဍကို ရွေးချယ်ပေးပါရှင် -", reply_markup=reply_markup)
    
    return WAIT_CAT

async def handle_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    query = update.callback_query
    await query.answer()
    cat_text = query.data 
    user_id = update.effective_user.id
    # Silent Cooldown Check
    now = datetime.now()
    if user_id in user_last_click:
        if (now - user_last_click[user_id]).total_seconds() < 1:
            await query.answer()
            return
    user_last_click[user_id] = now
    # နှိပ်လိုက်တဲ့ category စာသား
    # --- ဒီအပိုင်းလေး ထပ်ဖြည့်ပါ ---
    if cat_text == "cancel_tarot":
        return await cancel_tarot(update, context)
    # -------------------------
    context.user_data['main_cat_key'] = CATEGORY_MAP.get(cat_text, "general")
    context.user_data['main_cat_text'] = cat_text

    options = {
        "အချစ်ရေး": ["Crush", "ချစ်သူ/စုံတွဲ", "ရည်းစားဟောင်း", "လူပျို/လူလွတ်"],
        "စီးပွားရေး": ["ဝန်ထမ်း", "လုပ်ငန်းရှင်", "ငွေကြေး"],
        "ပညာရေး": ["စာမေးပွဲ", "ဘာသာရပ်သစ်", "ပညာသင်ဆု"],
        "ကျန်းမာရေး": ["ကိုယ်ကာယ", "စိတ်ကျန်းမာရေး", "ခွဲစိတ်ကုသမှု"],
        "ခရီးသွားလာရေး": ["အပန်းဖြေခရီး", "အလုပ်ကိစ္စ", "ဗီဇာ/စာရွက်စာတမ်း"],
        "လူမှုရေး": ["မိတ်ဆွေသစ်", "ဆက်ဆံရေး", "သတိထားရန်"],
        "မိသားစုရေး": ["မိသားစုအရေး", "အိုးအိမ်", "ရင်သွေးရတနာ"],
        "ယေဘုယျ": ["ကံကြမ္မာ"]
    }
    
    sub_list = options.get(cat_text, ["ကံကြမ္မာ"])
    keyboard = []
    for i in range(0, len(sub_list), 2):
        row = [InlineKeyboardButton(sub_list[i], callback_data=sub_list[i])]
        if i + 1 < len(sub_list):
            row.append(InlineKeyboardButton(sub_list[i+1], callback_data=sub_list[i+1]))
        keyboard.append(row)
    
    keyboard = [[btn for btn in row if btn] for row in keyboard]
    
    # 🌟 နောက်ဆုံး Row အဖြစ် Cancel Button ထည့်သွင်းထားပါသည် 🌟
    keyboard.append([InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")])
    
    await query.edit_message_text(text=f"*{cat_text}* ထဲမှ ပိုမိုသိရှိလိုသည့်အချက်ကို ရွေးချယ်ပါ -", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return WAIT_SUB


# --- ၈။ Reading Engine (Shuffle & Extraction Logic) ---

async def handle_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    """Sub-category ခလုတ်ကို နှိပ်လိုက်လျှင် ဂဏန်း ၃ ခု တောင်းခြင်း"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    # Silent Cooldown Check
    now = datetime.now()
    if user_id in user_last_click:
        if (now - user_last_click[user_id]).total_seconds() < 1:
            await query.answer()
            return
    user_last_click[user_id] = now
    sub_text = query.data
    
    # --- ဒီအပိုင်းလေး ထပ်ဖြည့်ပါ ---
    if sub_text == "cancel_tarot":
        return await cancel_tarot(update, context)
    # -------------------------
    
    context.user_data['sub_cat'] = sub_text
    
    # ခလုတ်အဟောင်းကို ဖျက်ပြီး ဂဏန်းတောင်းခြင်း
    # 🌟 Cancel Button ထည့်သွင်းထားပါသည် 🌟
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ မမေးတော့ပါ", callback_data="cancel_tarot")]])
    await query.edit_message_text(
        text=f"ရွေးချယ်ထားတဲ့ *{sub_text}* အတွက် လမ်းညွှန်ချက်လေးတွေ ရယူဖို့ ၁ ကနေ ၇၈ အထိ ဂဏန်း (၃) ခုကို ရိုက်ပို့ပေးပါဦးရှင်။ ✨\n\n(ဥပမာ - *7, 24, 55* လို့ ရိုက်ပေးပါနော်)", 
        parse_mode="Markdown",
        reply_markup=cancel_markup
    )
    return WAIT_NUMS


async def process_reading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    """
    ၁။ Loading Message ပို့မည် (Timeout 60s)
    ၂။ Deck ကို Shuffle လုပ်ပြီး User ပေးသော ဂဏန်းကို Index အဖြစ်သုံး၍ ကဒ်ရွေးမည်
    ၃။ ပုံများကို User Name Caption ဖြင့် ပို့မည် (Timeout 60s)
    ၄။ ၄ စက္ကန့်စောင့်မည်
    ၅။ ဟောစာတမ်းစာသားကို ပို့မည် (အတိတ်၊ ပစ္စုပ္ပန်၊ အနာဂတ်)
    ၆။ တတိယမြောက်ကဒ်အတွက် ကဒ်အတွင်းရှိ zodiac_rules မှ အကြံပြုချက်ကို ယူပြမည်
    ၇။ Credit နှုတ်မည်၊ Loading Message ကို ပြန်ဖျက်မည်
    """
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    text = update.message.text
    
    # ဂဏန်း ၃ ခု Validation စစ်ဆေးခြင်း
    # ၁။ ဂဏန်း ၃ ခု Validation စစ်ဆေးခြင်း
    try:
        clean_text = text.replace('၊', ',') 
        nums = [int(n.strip()) for n in re.findall(r'\d+', clean_text)]
        
        # (က) အရေအတွက် ၃ ခု မဟုတ်လျှင် (သို့မဟုတ်) ၁ မှ ၇၈ အပြင်ဘက် ရောက်နေလျှင်
        if len(nums) != 3 or any(n < 1 or n > 78 for n in nums):
            raise ValueError
            
        # (ခ) 💡 ကဒ်များ ထပ်နေခြင်း ရှိ/မရှိ စစ်ဆေးခြင်း (၂ ကဒ်တူတူ၊ ၃ ကဒ်တူတူ လက်မခံပါ)
        if len(set(nums)) != 3:
            await update.message.reply_text(
                "❌ ကဒ်တစ်ခုတည်းကို ထပ်၍ ရွေးချယ်၍မရပါရှင်။\n\n"
                "Tarot ဟောစာတမ်းတွက်ချက်ရာတွင် မတူညီသော ကဒ် ၃ ကဒ် လိုအပ်သောကြောင့် "
                "ကျေးဇူးပြု၍ မတူညီသော ဂဏန်း ၃ ခုကို ပြန်လည်ရိုက်နှိပ်ပေးပါရှင်။ 🙏"
            )
            return WAIT_NUMS
            
    except:
        await update.message.reply_text("❌ ကျေးဇူးပြု၍ ၁ မှ ၇၈ အကြား ဂဏန်း ၃ ခုကို မှန်ကန်စွာ (ဥပမာ - 7, 24, 55) ရိုက်ပေးပါရှင်။")
        return WAIT_NUMS

    # ၁။ Loading Message (Timeout 60s)
    loading_msg = await update.message.reply_text(
        "⏳ လူကြီးမင်းအတွက် ဟောစာတမ်းကို တွက်ချက်နေပါတယ်ရှင်...",
        write_timeout=60, read_timeout=60
    )

    # Data ပြင်ဆင်ခြင်း
    main_key = context.user_data.get('main_cat_key', 'general')
    sub_text = context.user_data.get('sub_cat', 'ယေဘုယျ')
    sub_key = SUB_CAT_MAP.get(sub_text, "general")
    
    # Session ထဲမှာရှိတဲ့ (ခေတ္တရိုက်ထားတဲ့) Zodiac ကို အရင်ယူမည်
    user_zodiac = context.user_data.get('zodiac')
    
    # Session မှာ မရှိမှသာ Database ထဲကဟာကို သွားယူမည် (မှတ်ထားသူများအတွက်)
    if not user_zodiac:
        conn = db_mgr.get_conn(); c = conn.cursor()
        c.execute("SELECT zodiac FROM users WHERE user_id = %s", (user_id,))
        res = c.fetchone()
        user_zodiac = (res[0] if (res and res[0]) else "Aries").strip().capitalize()
        db_mgr.put_conn(conn)
    else:
        user_zodiac = user_zodiac.strip().capitalize()

    # ၂။ Shuffle Deck & Selection
    all_cards = [str(i) for i in range(1, 79)]
    random.shuffle(all_cards) 
    selected = [all_cards[n-1] for n in nums]

    # ၃။ ပုံများပို့ခြင်း (Timeout 60s)
    media_group = []
    caption_text = f"{first_name} ရွေးလိုက်သောကဒ်များ"
    for i, card_id in enumerate(selected):
        img_path = f"cards/{card_id}.jpg"
        if os.path.exists(img_path):
            media_group.append(InputMediaPhoto(open(img_path, 'rb'), caption=caption_text if i == 0 else ""))
    
    if media_group:
        try:
            await send_typing(update, context)
            await update.message.reply_media_group(media=media_group, write_timeout=60, read_timeout=60)
            await send_typing(update, context)
        except Exception as e:
            logger.error(f"⚠️ Image Timeout: {e}")

    # ၄။ ၄ စက္ကန့် Delay
    await asyncio.sleep(2)
    await send_typing(update, context)

    # ၅။ ဟောစာတမ်းစာသား ပေါင်းစပ်ခြင်း
    timeframes = ["အတိတ် (သို့မဟုတ်) အကြောင်းရင်းခံ", "ပစ္စုပ္ပန် (သို့မဟုတ်) လက်ရှိအခြေအနေ", "အနာဂတ် (သို့မဟုတ်) ရှေ့ဆက်ဖြစ်လာနိုင်ခြေ"]
    final_report = f"🔮 *{sub_text} ဟောစာတမ်း*\n\n"
    
    for i, card_id in enumerate(selected):
        card_data = TAROT_DATA.get(card_id, {})
        card_name = card_data.get("name", f"Card {card_id}")
        
        # ယေဘုယျ သို့မဟုတ် အခြား Category ဟောကိန်းဆွဲယူခြင်း
        if main_key == "general":
            meaning = card_data.get("general", "ပြင်ဆင်နေဆဲ")
        else:
            meaning = card_data.get(main_key, {}).get(sub_key, "ပြင်ဆင်နေဆဲ")
            
        final_report += f"📅 *{timeframes[i]}*\n🃏 *ကဒ် {i+1}: {card_name}*\n📝 {meaning}\n\n"

        # ၆။ Zodiac Advice Logic (တတိယမြောက်ကဒ်အတွက်သာ)
        if i == 2 and main_key != "general":
            # 💡 ပြင်ဆင်ချက် - လက်ရှိ ကဒ် (card_data) အတွင်းမှ zodiac_rules ကို တိုက်ရိုက်ဆွဲယူခြင်း
            card_zodiac_rules = card_data.get("zodiac_rules", {})
            user_rules = card_zodiac_rules.get(user_zodiac, {})
            
            advice = ""
            if isinstance(user_rules, dict):
                # အကယ်၍ Category အလိုက် ထပ်ခွဲထားခဲ့လျှင် (ဥပမာ love -> crush)
                cat_rules = user_rules.get(main_key, {})
                if isinstance(cat_rules, dict):
                    advice = cat_rules.get(sub_key, "")
                elif isinstance(cat_rules, str):
                    advice = cat_rules
            elif isinstance(user_rules, str):
              # တိုက်ရိုက် စာသားအနေဖြင့် ရှိနေခဲ့လျှင်
                advice = user_rules
            
            # အကြံပြုချက် ရှိခဲ့လျှင် ထည့်သွင်းပေးမည်
            if advice:
                final_report += f"🌟 *{user_zodiac} ရာသီဖွားများအတွက် အထူးအကြံပြုချက်:*\n{advice}\n\n"

    user_id = update.effective_user.id

    # အဆင့် ၁: ဟောစာတမ်း မပြခင်လေးမှာမှ Credit ကို တကယ်နှုတ်ခြင်း
    await deduct_credit(user_id)


    # ဟောစာတမ်းစာသားပို့ခြင်း (Timeout 60s)
    try:
        await update.message.reply_text(final_report, parse_mode="Markdown", write_timeout=60, read_timeout=60)
    except:
        await update.message.reply_text("❌ ဟောစာတမ်းပို့ရန် အခက်အခဲရှိနေပါတယ်ရှင်။")

    # Review Buttons & Cleanup
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📝 Review ပေးမည်", callback_data="give_review"), InlineKeyboardButton("✖️ မပေးဘူး", callback_data="skip_review")]])
    await send_typing(update, context)
    await update.message.reply_text("Review ပေးလိုပါသလားရှင်?", reply_markup=keyboard, write_timeout=60)

    if loading_msg:
        try: await loading_msg.delete()
        except: pass

    return WAIT_FEEDBACK
              
              
    

# --- ၉။ Growth & Feedback (Share & Review Logic) ---

async def handle_feedback_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(update, context)
    query = update.callback_query
    await query.answer()

    if query.data == "give_review":
        # စာသားတောင်းသည့်စာကို Edit လုပ်ပြီး ယင်း၏ ID ကို မှတ်ထားခြင်း
        instr_msg = await query.edit_message_text(
            "ဟုတ်ကဲ့ပါရှင်။ လူကြီးမင်းရဲ့ Review လေးကို အောက်မှာ စာသားအတိုင်း ရိုက်ပြီး အခုပဲ ပေးပို့ပေးလို့ ရပါပြီရှင်။"
        )
        context.user_data['instr_msg_id'] = instr_msg.message_id
        return WAIT_REVIEW_TEXT
    else:
        try: await query.message.delete()
        except: pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="ဟုတ်ကဲ့ပါရှင်။ နောက်မှပဲ ပေးလည်း ရပါတယ်နော်။ အခုလို အသုံးပြုပေးတာကိုပဲ ကျေးဇူးတင်လှပါပြီရှင်။",
            reply_markup=main_kb
        )
        return ConversationHandler.END

async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    review_text = update.message.text
    # (Menu Logic များ ဤနေရာတွင် ရှိမည်...)

    # ၁။ စာသားတောင်းခဲ့သည့် Bot ၏ စာဟောင်းကို ချက်ချင်းဖျက်ခြင်း
    instr_id = context.user_data.get('instr_msg_id')
    if instr_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=instr_id)
        except: pass

    # Admin ထံ ပို့ဆောင်ခြင်း
    user = update.effective_user
    try:
        user_link = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"⭐ <b>NEW USER REVIEW</b> ⭐\n\n"
                  f"👤 <b>From:</b> {user_link} (@{user.username if user.username else 'N/A'})\n"
                  f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
                  f"💬 <b>Review:</b> <i>{review_text}</i>"),
            parse_mode="HTML"
        )
    except: pass

    # ၂။ ကျေးဇူးတင်လွှာ ပို့ခြင်း (User စာကို မဖျက်ပါ)
    confirm_msg = await update.message.reply_text(
        "Review ပေးပို့ပေးတဲ့အတွက် ကျေးဇူးတင်ပါတယ်ရှင်။ လူကြီးမင်းရဲ့ စာကို Admin ဆီကို သေချာ ပေးပို့ထားလိုက်ပါပြီရှင်။",
        reply_markup=main_kb
    )

    # ၃။ ကျေးဇူးတင်လွှာကို ၁ မိနစ် (၆၀ စက္ကန့်) အကြာတွင် ဖျက်ခြင်း
    context.job_queue.run_once(delete_old_message, 60, data={'chat_id': update.effective_chat.id, 'message_id': confirm_msg.message_id})
    return ConversationHandler.END


# --- handle_suggestion နှင့် handle_suggestion_text ကို တစ်စုတည်း ပြန်ပြင်ထားပါသည် ---

async def handle_suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_ban_status(update, context): return ConversationHandler.END
    await send_typing(update, context)
    # စာသားတောင်းသည့်စာကို ပို့ပြီး ID ကို မှတ်ထားခြင်း
    msg = await update.message.reply_text(
        "Bot လေး ပိုကောင်းလာအောင် လူကြီးမင်းရဲ့ အကြံပြုချက်လေးတွေကို အောက်မှာ ရိုက်ပို့ပေးနိုင်ပါတယ်ရှင်။",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data['suggest_instr_id'] = msg.message_id
    return WAIT_SUGGESTION_TEXT

async def handle_suggestion_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    suggestion_text = update.message.text
    # (Menu Logic များ ဤနေရာတွင် ရှိမည်...)

    # ၁။ စာသားတောင်းခဲ့သည့် Bot ၏ စာဟောင်းကို ချက်ချင်းဖျက်ခြင်း
    instr_id = context.user_data.get('suggest_instr_id')
    if instr_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=instr_id)
        except: pass

    # Admin ထံ ပို့ဆောင်ခြင်း
    user = update.effective_user
    try:
        user_link = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=(f"💡 <b>NEW SUGGESTION</b> 💡\n\n"
                  f"👤 <b>From:</b> {user_link} (@{user.username if user.username else 'N/A'})\n"
                  f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
                  f"🛠️ <b>Content:</b> <i>{suggestion_text}</i>"),
            parse_mode="HTML"
        )
    except: pass

    # ၂။ ကျေးဇူးတင်လွှာ ပို့ခြင်း (User စာကို မဖျက်ပါ)
    confirm_msg = await update.message.reply_text(
        "အကြံပြုချက်အတွက် ကျေးဇူးတင်ပါတယ်ရှင်။ လူကြီးမင်းရဲ့ စာကို Admin ဆီသို့ သေချာ ပေးပို့ထားလိုက်ပါပြီရှင်။",
        reply_markup=main_kb 
    )

    # ၃။ ကျေးဇူးတင်လွှာကို ၁ မိနစ်အကြာတွင် ဖျက်ခြင်း
    context.job_queue.run_once(delete_old_message, 60, data={'chat_id': update.effective_chat.id, 'message_id': confirm_msg.message_id})
    return ConversationHandler.END
    
    
async def share_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_ban_status(update, context): return ConversationHandler.END
    await send_typing(update, context)
    
    full_message = "ဒီ tarot botလေးသုံးကြည့် မိုက်တယ်\n@TarotBayDinBot"
    encoded_message = urllib.parse.quote(full_message)
    share_url = f"https://t.me/share/url?url=&text={encoded_message}"
    
    status_msg = "✨ *Tarot BayDin ကို မျှဝေလိုက်ပါ*\n\nဒီ Bot လေးက လူကြီးမင်းအတွက် အကျိုးရှိတယ်ဆိုရင် သူငယ်ချင်းတွေလည်း ကံကြမ္မာအဖြေရှာနိုင်ဖို့ အောက်ကခလုတ်လေးကို နှိပ်ပြီး မျှဝေပေးပါဦးနော်။ 👇"
    
    await update.message.reply_text(
        status_msg, 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 သူငယ်ချင်းများထံ Share မည်", url=share_url)]]),
        parse_mode="Markdown"
    )
    
    

# ==========================================================


# --- ၁၀။ Admin Commands (Admin သီးသန့် အသုံးပြုရန်) ---

# ၁။ နောက်ကွယ်မှာ စာပို့ပေးမယ့် Function (မူလ Logic များ အားလုံး ပါဝင်သည်)
async def do_broadcast(context: ContextTypes.DEFAULT_TYPE, users, has_photo, file_id, content_text, batch_time, admin_id):
    success = 0
    fail = 0
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            for u in users:
                user_id = u[0]
                try:
                    if has_photo:
                        # ပုံနှင့်ပို့ခြင်း (Keyboard Refresh ပါဝင်သည်)
                        sent_msg = await context.bot.send_photo(
                            chat_id=user_id, photo=file_id, 
                            caption=content_text, reply_markup=main_kb
                        )
                    else:
                        # စာသားနှင့်ပို့ခြင်း (Keyboard Refresh ပါဝင်သည်)
                        sent_msg = await context.bot.send_message(
                            chat_id=user_id, text=content_text, reply_markup=main_kb
                        )
                    
                    # --- မူလအတိုင်း Database မှာ ID သိမ်းဆည်းခြင်း (unpost အတွက် အရေးကြီးသည်) ---
                    c.execute("INSERT INTO broadcast_logs (user_id, message_id, batch_id) VALUES (%s, %s, %s)", 
                              (user_id, sent_msg.message_id, batch_time))
                    
                    success += 1
                    await asyncio.sleep(0.1) # Rate Limit အတွက် 0.1s နားခြင်း
                except Exception:
                    fail += 1
        conn.commit()
    finally:
        db_mgr.put_conn(conn)
    
    # ပို့ဆောင်မှု ပြီးဆုံးသွားမှ Admin ဆီကို မူလအတိုင်း Report ပြန်ပို့ပေးမည်
    await context.bot.send_message(
        chat_id=admin_id, 
        text=f"✅ ကြေညာချက် ပို့ဆောင်မှု ပြီးဆုံးပါပြီ။\n\nအောင်မြင်စွာ ပို့ပြီးသူ: {success} ဦး\nပို့မရသူ: {fail} ဦး"
    )

# ၂။ Admin Command ကို လက်ခံပြီး Background အလုပ် စတင်ပေးမည့် Function
async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    message = update.message
    has_photo = bool(message.photo)
    is_post_command = False
    raw_text = ""

    # မူလအတိုင်း /post command ကို စစ်ဆေးခြင်း
    if has_photo and message.caption and message.caption.lower().startswith('/post'):
        is_post_command = True
        raw_text = message.caption
    elif message.text and message.text.lower().startswith('/post'):
        is_post_command = True
        raw_text = message.text

    if not is_post_command: return

    # မူလအတိုင်း /post စာသားကို ဖယ်ရှားခြင်း
    content_text = re.sub(r'(?i)^/post\s*', '', raw_text).strip()

    if not content_text and not has_photo:
         await update.message.reply_text("❌ ပို့ချင်သော စာသားကို ရိုက်ထည့်ပါ။")
         return

    # Admin ကို ချက်ချင်း အသိပေးခြင်း (Multitasking စတင်ပြီ)
    await update.message.reply_text("⏳ ကြေညာချက်ကို နောက်ကွယ်မှ စတင်ပို့ဆောင်နေပါပြီ။ Bot ကို ပုံမှန်အတိုင်း ဆက်လက်အသုံးပြုနိုင်ပါတယ်ရှင်။")

    # User အားလုံးကို ဆွဲထုတ်ခြင်း
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM users")
            users = c.fetchall()
    finally:
        db_mgr.put_conn(conn)

    file_id = message.photo[-1].file_id if has_photo else None
    batch_time = datetime.now()

    # --- အလုပ်ကို Background Task အဖြစ် လွှဲပြောင်းပေးလိုက်ခြင်း ---
    asyncio.create_task(do_broadcast(context, users, has_photo, file_id, content_text, batch_time, ADMIN_ID))

async def unpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT MAX(batch_id) FROM broadcast_logs")
            last_batch = c.fetchone()[0]
            if not last_batch: return await update.message.reply_text("❌ ဖျက်စရာ Post မရှိပါဘူးရှင်။")
            
            c.execute("SELECT user_id, message_id FROM broadcast_logs WHERE batch_id = %s", (last_batch,))
            logs = c.fetchall()
            
            deleted = 0
            for u_id, m_id in logs:
                try:
                    await context.bot.delete_message(chat_id=u_id, message_id=m_id)
                    deleted += 1
                except: continue
            c.execute("DELETE FROM broadcast_logs WHERE batch_id = %s", (last_batch,))
        conn.commit()
        await update.message.reply_text(f"✅ နောက်ဆုံးပို့ထားတဲ့ Post {deleted} ခုကို ပြန်ဖျက်လိုက်ပါပြီရှင်။")
    finally: db_mgr.put_conn(conn)
    

async def send_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ User တစ်ဦးတည်းကိုသာ User ID သုံးပြီး စာ သို့မဟုတ် ပုံ ပို့ရန်"""
    
    # Admin ဟုတ်မဟုတ် အရင်စစ်မည်
    if update.effective_user.id != ADMIN_ID:
        return

    # အသုံးပြုပုံ မှန်မမှန်စစ်ခြင်း (/send [ID] [စာသား])
    if not context.args:
        await update.message.reply_text("❌ အသုံးပြုပုံ - `/send [USER_ID] [စာသား]`")
        return

    try:
        # ပထမဆုံး argument ကို User ID အဖြစ် ယူမည်
        target_id = int(context.args[0])
        # ကျန်တဲ့စာသားများကို စုစည်းပြီး ပို့မည့်စာသားအဖြစ် ယူမည်
        message_to_send = " ".join(context.args[1:])
        
        # ပုံပါရင် ပုံနဲ့ Caption ကို တွဲပို့မည်
        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            await context.bot.send_photo(
                chat_id=target_id, 
                photo=photo_file_id, 
                caption=message_to_send,
                reply_markup=main_kb 
            )
        else:
            # စာသားပဲပါရင် စာပဲပို့မည်
            if not message_to_send:
                await update.message.reply_text("❌ ပို့မည့် စာသားထည့်ပေးပါရှင်။")
                return
            await context.bot.send_message(
              chat_id=target_id,
              text=message_to_send,
              reply_markup=main_kb 
              )
            
        await update.message.reply_text(f"✅ User `{target_id}` ထံသို့ ပေးပို့ပြီးပါပြီ။")

    except ValueError:
        await update.message.reply_text("❌ User ID သည် ကိန်းဂဏန်း (Number) ဖြစ်ရပါမည်။")
    except Exception as e:
        await update.message.reply_text(f"❌ ပို့၍မရပါ (User က Bot ကို Block ထားခြင်း ဖြစ်နိုင်သည်)။\nError: {e}")


async def add_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ User တစ်ဦးကို Credit ပေးခြင်း/နှုတ်ခြင်း"""
    if update.effective_user.id != ADMIN_ID: return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        
        conn = db_mgr.get_conn(); c = conn.cursor()
        # GREATEST(0, ...) ကို သုံးထားလို့ ၀ အောက် လုံးဝမကျသွားပါ
        c.execute("UPDATE users SET gift_credits = GREATEST(0, gift_credits + %s) WHERE user_id = %s", (amount, target_id))
        conn.commit(); db_mgr.put_conn(conn)
        
        await update.message.reply_text(f"✅ User {target_id} ၏ Credit ကို ပြင်ဆင်ပြီးပါပြီ။")

        # --- User ထံသို့ အသိပေးစာ ပို့သည့်အပိုင်း ---
        if amount > 0:
            # Credit တိုးပေးသည့်အခါ ပြမည့်စာသား
            user_msg = f"🎁 လူကြီးမင်းအတွက် အခမဲ့မေးမြန်းခွင့် ({amount}) ကြိမ် ထည့်သွင်းပေးလိုက်ပါပြီရှင်။ ✨"
        else:
            # Credit နှုတ်ယူသည့်အခါ ပြမည့်စာသား (abs သုံးထားသဖြင့် အနှုတ်လက္ခဏာ မပါတော့ပါ)
            user_msg = f"ℹ️ လူကြီးမင်း၏ အခမဲ့မေးမြန်းခွင့်ထဲမှ ({abs(amount)}) ကြိမ်ကို ပြန်လည်နှုတ်ယူလိုက်ပါပြီရှင်။"
            
        await context.bot.send_message(chat_id=target_id, text=user_msg)

    except (IndexError, ValueError):
        await update.message.reply_text("❌ အသုံးပြုပုံ - /add_credit USER_ID AMOUNT (နှုတ်လိုပါက အနှုတ်ဂဏန်းထည့်ပါ)")

async def gift_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ User အားလုံးကို Credit လက်ဆောင် တစ်ပြိုင်နက် ပေးခြင်း"""
    if update.effective_user.id != ADMIN_ID: # Admin ဟုတ်မှသာ ခွင့်ပြုမည်
        return

    try:
        # ပေးလိုသော Credit ပမာဏကို ယူခြင်း
        amount = int(context.args[0])
        
        conn = db_mgr.get_conn()
        try:
            with conn.cursor() as c:
                # User အားလုံး၏ gift_credits ကို တစ်ပြိုင်နက်တည်း Update လုပ်ခြင်း
                c.execute("UPDATE users SET gift_credits = gift_credits + %s", (amount,))
            conn.commit()
            
            await update.message.reply_text(f"✅ User အားလုံးထံသို့ Credit ({amount}) ခုစီ လက်ဆောင်ပေးပြီးပါပြီရှင်။")
            
        except Exception as e:
            logger.error(f"Error in gift_all_users: {e}")
            await update.message.reply_text("❌ Credit ပေးရာတွင် အမှားအယွင်း ဖြစ်ပေါ်ခဲ့ပါသည်။")
        finally:
            db_mgr.put_conn(conn)

    except (IndexError, ValueError):
        await update.message.reply_text("❌ အသုံးပြုပုံ - /giftall [Credit_ဂဏန်း]`\nဥပမာ - `/giftall 5 (အားလုံးကို ၅ ကြိမ်စီ ပေးမည်)")

async def check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User တစ်ဦး၏ အသေးစိတ် အချက်အလက်ကို စစ်ဆေးခြင်း"""
    if update.effective_user.id != ADMIN_ID: return

    try:
        target_id = int(context.args[0])
        conn = db_mgr.get_conn(); c = conn.cursor()
        c.execute("SELECT gift_credits + daily_credits, birthday, zodiac, join_date FROM users WHERE user_id = %s", (target_id,))
        res = c.fetchone(); db_mgr.put_conn(conn)

        if res:
            msg = (f"👤 *User Info: {target_id}*\n"
                   f"💳 Credit: {res[0]}\n"
                   f"🎂 Birthday: {res[1]}\n"
                   f"♈ Zodiac: {res[2]}\n"
                   f"📅 Joined: {res[3].strftime('%Y-%m-%d')}")
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ အဲ့ဒီ User ကို Database ထဲမှာ ရှာမတွေ့ပါဘူးရှင်။")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ အသုံးပြုပုံ - `/check USER_ID`")
        
# အဆင့် (၁) - အလိုအလျောက်ဖျက်မည့် Function အသစ်ထည့်ရန်
# (send_daily_message function ၏ အပေါ်နား သို့မဟုတ် အပြင်ဘက် တစ်နေရာရာတွင် ထည့်ပါ)
# =====================================================================

async def delete_old_message(context: ContextTypes.DEFAULT_TYPE):
    """၁၂ နာရီပြည့်လျှင် စာဟောင်းကို လိုက်ဖျက်မည့် Function"""
    job = context.job
    try:
        await context.bot.delete_message(
            chat_id=job.data['chat_id'], 
            message_id=job.data['message_id']
        )
    except Exception as e:
        # User က နှိပ်ပြီးသားမို့လို့ ဖျက်ပြီးသားဖြစ်နေရင် (သို့) Error တက်ရင် ကျော်သွားမည်
        pass




# --- ၁၁။ Automation Jobs (Daily Notifications & Reset) ---
async def send_morning_quote(context: ContextTypes.DEFAULT_TYPE):
    """မနက်ခင်း Noti ပို့ခြင်း + Daily Card ရွေးရန် ခလုတ်ပါဝင်ခြင်း"""
    day_messages = {
        0: "🌞 မင်္ဂလာရှိသော တနင်္လာနေ့ပါ။ အလုပ်အကိုင်တွေ အဆင်ပြေချောမွေ့ပါစေ။",
        1: "✨ အင်္ဂါနေ့မှာ စိတ်သစ်ကိုယ်သစ်နဲ့ ရှေ့ဆက်နိုင်ပါစေရှင်။",
        2: "🍀 ဗုဒ္ဓဟူးနေ့မှာ ကံကောင်းခြင်းတွေ ဆုံစည်းပါစေ။",
        3: "📚 ကြာသပတေးနေ့မှာ ပညာထူးတွေ၊ အကြံကောင်းတွေ ရရှိပါစေ။",
        4: "💎 သောကြာနေ့မှာ စီးပွားလာဘ်လာဘတွေ ဒီရေအလား တိုးပွားပါစေ။",
        5: "🎉 ပျော်ရွှင်စရာ စနေနေ့လေးမှာ စိတ်အပန်းပြေပါစေရှင်။",
        6: "🙏 တနင်္ဂနွေနေ့မှာ မိသားစုနဲ့အတူ အေးချမ်းသာယာပါစေ။"
    }
    current_day = datetime.now().weekday()
    quote = day_messages.get(current_day, "မင်္ဂလာရှိသော နေ့သစ်လေး ဖြစ်ပါစေ။")
    
    # ခလုတ်အသစ် ထည့်သွင်းခြင်း
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔮 ယနေ့အတွက် ကဒ်ရွေးမည်", callback_data="daily_random_card")]])

    conn = db_mgr.get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM users"); users = c.fetchall(); db_mgr.put_conn(conn)
    
    global DAILY_PHOTO_ID
    photo_path = "cards/daily.jpg" # ဤနေရာတွင် မိမိပုံအမည်ကို ထည့်ပါ
    has_photo = os.path.exists(photo_path)
    
    for user in users:
        try:
            # စာသားကို caption အနေဖြင့် ပြင်ဆင်ခြင်း
            caption_text = f"{quote}\n\nဒီနေ့အတွက် လူကြီးမင်းကို လမ်းညွှန်ပေးမယ့် Tarot ကတ်လေးက ဘာဖြစ်မလဲ? ကံဇာတာကို သိရှိနိုင်ဖို့ အောက်ကခလုတ်လေးကို နှိပ်ပြီး အခမဲ့ ကတ်နှိုက်ကြည့်လိုက်ပါဦးနော်။ 🃏👇"
            
            # ပုံရှိမရှိ စစ်ဆေးပြီး ပို့ခြင်း
            if DAILY_PHOTO_ID:
                # ဒုတိယအကြိမ်နှင့် နောက်ပိုင်းအတွက် ID ကိုသုံး၍ အမြန်ပို့မည်
                msg = await context.bot.send_photo(
                    chat_id=user[0], 
                    photo=DAILY_PHOTO_ID,
                    caption=caption_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            elif has_photo:
                # ပထမဆုံးအကြိမ် Upload တင်မည်
                msg = await context.bot.send_photo(
                    chat_id=user[0], 
                    photo=open(photo_path, 'rb'),
                    caption=caption_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                DAILY_PHOTO_ID = msg.photo[-1].file_id # ID ကို မှတ်ထားမည်
            else:
                # ပုံမရှိပါက စာသက်သက်သာ ပို့မည် (Fallback)
                msg = await context.bot.send_message(
                    chat_id=user[0], 
                    text=caption_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            
            # ၁၂ နာရီပြည့်လျှင် ဖျက်မည့် Timer (မူလအတိုင်း)
            context.job_queue.run_once(
                delete_old_message,
                43200, 
                data={'chat_id': user[0], 'message_id': msg.message_id}
            )
            await asyncio.sleep(0.5)
        except Exception: continue

async def send_daily_stats_to_admin(context: ContextTypes.DEFAULT_TYPE):
    """ညတိုင်း Admin ဆီကို အသေးစိတ် Report ပို့ပေးမည်"""
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            # ၁။ စုစုပေါင်း User
            c.execute("SELECT COUNT(*) FROM users")
            total_users = c.fetchone()[0]
            
            # ၂။ ယနေ့ အသစ်ဝင်လာသော User
            c.execute("SELECT COUNT(*) FROM users WHERE DATE(join_date) = CURRENT_DATE")
            new_users_today = c.fetchone()[0]
            
        report_msg = (
            "📊 *Daily Bot Report*\n\n"
            f"👥 စုစုပေါင်း User: {total_users} ဦး\n"
            f"📈 ယနေ့ အသစ်ဝင်သူ: {new_users_today} ဦး\n"
            "--------------------------\n"
            "Status: All systems running normally ✅"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=report_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send daily stats to admin: {e}")
    finally:
        db_mgr.put_conn(conn)
        
        
async def handle_daily_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Daily Card ခလုတ်နှိပ်လျှင် JSON အသစ်မှ တစ်နေ့တာ ဟောကိန်းပြခြင်း"""
    query = update.callback_query
    await query.answer()
    
    try:
        await query.message.delete()
    except Exception:
        pass

    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=user_id, action='typing')
    
    # Random ကဒ်တစ်ခု ရွေးခြင်း (၁ မှ ၇၈)
    card_id = str(random.randint(1, 78))
    
    # ပုံရှိမရှိ စစ်ဆေးရန်အတွက် မူလ TAROT_DATA မှ ကဒ်အမည်ကို ယူမည်
    card_info = TAROT_DATA.get(card_id, {})
    card_name = card_info.get('name', f"Card {card_id}")
    
    # 💡 ပြင်ဆင်လိုက်သည့်နေရာ - ဟောစာတမ်းကို DAILY_TAROT_DATA မှ ဆွဲယူခြင်း
    daily_prediction = DAILY_TAROT_DATA.get(card_id, "ဒီနေ့အတွက် ဟောစာတမ်း ပြင်ဆင်နေဆဲ ဖြစ်ပါတယ်ရှင်။")
    
    img_path = f"cards/{card_id}.jpg"
    caption = f"🌟 ယနေ့အတွက် လမ်းညွှန်ကဒ်: *{card_name}*"
    
    if os.path.exists(img_path):
        await context.bot.send_photo(
            chat_id=user_id,
            photo=open(img_path, 'rb'),
            caption=f"{caption}\n\n📝 {daily_prediction}", # JSON အသစ်မှ စာသား
            parse_mode="Markdown"
        )
    else:
        await query.message.reply_text(f"{caption}\n\n📝 {daily_prediction}", parse_mode="Markdown")


# ၂။ reset_daily_credits function ကို အစားထိုးရန်
# (သတိပြုရန် - main() ထဲရှိ job_queue တွင် hour=17, minute=30 ဟု ပြင်ပေးပါ)
async def reset_daily_credits(context: ContextTypes.DEFAULT_TYPE):
    """ညသန်းခေါင် (UTC 17:30) ရောက်ပါက Daily Credit ကို ၃ ခု ပြန်ညှိမည် (Gift ကို မထိပါ)"""
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            # daily_credits ကို ၃ ခုသို့ Reset လုပ်သည် (Rollover မရှိပါ)
            c.execute("UPDATE users SET daily_credits = %s", (DAILY_CREDITS_LIMIT,))
        conn.commit()
        logger.info("✅ Daily Credits Reset to 3 for all users.")
    finally:
        db_mgr.put_conn(conn)



# ၁။ start function ကို အစားထိုးရန်
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    await send_typing(update, context)

    # Database ထဲသို့ User အသစ်ထည့်ခြင်း (Gift 7, Daily 3)
    conn = db_mgr.get_conn()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO users (user_id, gift_credits, daily_credits) 
                VALUES (%s, %s, %s) 
                ON CONFLICT (user_id) DO NOTHING
            """, (user_id, GIFT_CREDITS_LIMIT, DAILY_CREDITS_LIMIT))
        conn.commit()
    finally:
        db_mgr.put_conn(conn)

    welcome_text = (
        f"🌟 *မင်္ဂလာပါ {full_name} ရေ...*\n\n"
        "လူကြီးမင်းရဲ့ လက်ရှိအခြေအနေနဲ့ အနာဂတ်လမ်းစတွေကို ရှေးဟောင်း Tarot ပညာရပ်နဲ့အတူ အဖြေရှာကြည့်ဖို့ ဖိတ်ခေါ်ပါတယ်ရှင်။\n\n"
        "ကျွန်မတို့ရဲ့ Bot လေးဟာ လူကြီးမင်းကိုယ်တိုင်ရွေးချယ်လိုက်တဲ့ "
        "ကဒ်တွေရဲ့ စွမ်းအင် (Energy) အပေါ် မူတည်ပြီး အနီးစပ်ဆုံးနဲ့ အမှန်ကန်ဆုံး လမ်းညွှန်ချက်တွေကို ဖော်ထုတ်ပေးသွားမှာပါ။\n\n"
        "ကိုယ့်ရဲ့ ကံကြမ္မာနဲ့ စိတ်ခွန်အားအတွက် ဘယ်လိုထူးခြားတဲ့ သတင်းစကားတွေ ရှိနေမလဲဆိုတာကို အခုပဲ အခမဲ့ စမ်းသပ်ကြည့်လိုက်ပါဦးနော်။ ✨\n\n"
        "အောက်က *'🔮 Tarot မေးမည်'* ခလုတ်ကို နှိပ်ပြီး စတင်နိုင်ပါပြီရှင်။"
    )

    global WELCOME_PHOTO_ID
    photo_path = "cards/welcome.jpg" 
    if WELCOME_PHOTO_ID:
        await update.message.reply_photo(photo=WELCOME_PHOTO_ID, caption=welcome_text, parse_mode="Markdown", reply_markup=main_kb)
    elif os.path.exists(photo_path):
        sent_msg = await update.message.reply_photo(photo=open(photo_path, 'rb'), caption=welcome_text, parse_mode="Markdown", reply_markup=main_kb)
        WELCOME_PHOTO_ID = sent_msg.photo[-1].file_id 
    else:
        await update.message.reply_text(welcome_text, reply_markup=main_kb, parse_mode="Markdown")
    
    return ConversationHandler.END
    
    
# --- တခြား function တွေရဲ့ အောက်နားမှာ သွားကပ်ပေးပါ ---

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း လမ်းညွှန်ချက်ကို ပြသရန်"""
    full_name = update.effective_user.full_name
    
    # Typing status ပြခြင်း
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    help_text = (
        f"🌟 *မင်္ဂလာပါ {full_name} ရေ...*\n\n"
        "ကျွန်မတို့ရဲ့ Tarot BayDin Bot ကို အသုံးပြုပြီး လူကြီးမင်းရဲ့ ကံကြမ္မာတွေကို အခုလို လွယ်လွယ်ကူကူ မေးမြန်းနိုင်ပါတယ်ရှင် -\n\n"
        "🔮 *Tarot မေးမည်*\n"
        "အတိတ်၊ ပစ္စုပ္ပန်နဲ့ အနာဂတ် ကံကြမ္မာတွေကို ကတ် (၃) ကတ်စနစ်နဲ့ အသေးစိတ် တွက်ချက်ဟောကြားပေးမှာ ဖြစ်ပါတယ်ရှင်။\n\n"
        "🎯 *ကံကြမ္မာ လမ်းညွှန်*\n"
        "ကတ် (၁) ကတ်တည်းနဲ့ လက်ရှိကိစ္စတစ်ခုရဲ့ 'ရလဒ်' ဒါမှမဟုတ် ရှေ့ဆက်ရမယ့် 'လမ်းညွှန်ချက်' ကို တိတိကျကျ သိလိုတဲ့အခါ အသုံးပြုနိုင်ပါတယ်ရှင်။\n\n"
        "❤️ *ချစ်သူနဲ့ကိုက်ညီမှု(RS)*\n"
        "နှစ်ဦးသားရဲ့ တွဲဖက်ညီမှုကို Tarot ကတ် ဒါမှမဟုတ် ရာသီခွင်နည်းလမ်းတွေနဲ့ စစ်ဆေးပေးသွားမှာပါရှင်။\n\n"
        "♈ *ရာသီခွင်ပင်ကိုယ်စရိုက်*\n"
        "ရာသီခွင်အလိုက် လူကြီးမင်းရဲ့ ထူးခြားလှတဲ့ ပင်ကိုယ်စရိုက်နဲ့ စိတ်နေစိတ်ထားတွေကို ဖတ်ရှုနိုင်ပါတယ်ရှင်။\n\n"
        "🎂 *မွေးနေ့မှတ်မည်*\n"
        "မွေးနေ့ကို မှတ်သားထားလိုက်ပါက နောက်ပိုင်းမှာ မွေးနေ့ကို ထပ်ခါထပ်ခါ ရိုက်စရာမလိုတော့ဘဲ အချိန်ကုန်သက်သာစေမှာပါရှင်။\n\n"
        "✍️ *အကြံပြုစာပို့ရန်*\n"
        "ဘော့တ်နဲ့ပတ်သက်တဲ့ ထင်မြင်ချက်တွေ ဒါမှမဟုတ် လိုအပ်ချက်တွေကို Admin ထံ တိုက်ရိုက် ပေးပို့နိုင်ပါတယ်ရှင်။\n\n"
        "📤 *Share မည်*\n"
        "ဒီဘော့တ်လေးကို အသုံးပြုရတာ သဘောကျလို့ သူငယ်ချင်းတွေကိုလည်း မျှဝေပေးချင်တယ်ဆိုရင် ဒီခလုတ်လေးကနေတစ်ဆင့် ပြန်လည်မျှဝေပေးနိုင်ပါတယ်ရှင်။\n\n"
        "✨ လူကြီးမင်းရဲ့ ဘဝခရီးလမ်းမှာ Tarot ကဒ်လေးတွေက အကောင်းဆုံး အလင်းပြပေးနိုင်ပါစေရှင်။ ✨"
    )
    
    await update.message.reply_text(
        help_text, 
        parse_mode="Markdown", 
        reply_markup=main_kb
    )

# Admin Panel Command
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    conn = db_mgr.get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users"); total = c.fetchone()[0]; db_mgr.put_conn(conn)
    await update.message.reply_text(f"🛡 *Admin Panel*\n\nUser: {total} ဦး\nStatus: Running ✅", parse_mode="Markdown")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ User ကို ပိတ်ပင်ခြင်း"""
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        conn = db_mgr.get_conn(); c = conn.cursor()
        c.execute("UPDATE users SET is_banned = TRUE WHERE user_id = %s", (target_id,))
        conn.commit(); db_mgr.put_conn(conn)
        await update.message.reply_text(f"🚫 User `{target_id}` ကို အသုံးပြုခွင့် ပိတ်လိုက်ပါပြီ။")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ အသုံးပြုပုံ - `/ban USER_ID`")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ User ကို ပြန်ဖွင့်ပေးခြင်း"""
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        conn = db_mgr.get_conn(); c = conn.cursor()
        c.execute("UPDATE users SET is_banned = FALSE WHERE user_id = %s", (target_id,))
        conn.commit(); db_mgr.put_conn(conn)
        await update.message.reply_text(f"✅ User `{target_id}` ကို အသုံးပြုခွင့် ပြန်ဖွင့်ပေးလိုက်ပါပြီ။")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ အသုံးပြုပုံ - `/unban USER_ID`")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")

async def get_banned_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban ထားသော User အရေအတွက် ကြည့်ခြင်း"""
    if update.effective_user.id != ADMIN_ID: return
    conn = db_mgr.get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE is_banned = TRUE")
    count = c.fetchone()[0]
    db_mgr.put_conn(conn)
    await update.message.reply_text(f"🚫 လက်ရှိ အသုံးပြုခွင့် ပိတ်ပင်ထားသော User စုစုပေါင်း - ({count}) ဦး ရှိပါသည်။")
    

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin သီးသန့် Help Command (Click-to-Copy နှင့် /giftall ပါဝင်သည်)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return
        
    help_text = (
        "✨ *Tarot BayDin Admin Control Panel* ✨\n"
        "(_Command များကို နှိပ်၍ Copy ကူးယူနိုင်ပါသည်_)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 *ကြေညာချက်ထုတ်ပြန်ရန် (Broadcast)*\n"
        "• `/post` [စာသား/ပုံ] \n"
        "  User အားလုံးထံ စာသား (သို့မဟုတ်) ပုံနှင့်စာတွဲလျက် ပို့ရန်။\n"
        "• `/unpost` \n"
        "  နောက်ဆုံးပို့ခဲ့သော Post ကို User အားလုံးဆီမှ ပြန်ဖျက်ရန်။\n\n"
        
        "🔍 *User အချက်အလက် စစ်ဆေးရန်*\n"
        "• `/check` [USER_ID] \n"
        "  User တစ်ဦး၏ လက်ကျန် Credit၊ မွေးနေ့၊ ရာသီခွင်နှင့် စတင်ဝင်ရောက်သည့်ရက်ကို ကြည့်ရန်။\n"
        "• `/admin` \n"
        "  Bot ၏ စုစုပေါင်း User အရေအတွက်နှင့် လက်ရှိ Status ကို စစ်ဆေးရန်။\n\n"
        
        "🎁 *Credit စီမံခန့်ခွဲခြင်း*\n"
        "• `/giftall` [Amount] \n"
        "  လက်ရှိ User အားလုံးကို Credit လက်ဆောင် တစ်ပြိုင်နက်ပေးရန်။\n"
        "• `/add_credit` [USER_ID] [Amount] \n"
        "  သတ်မှတ်ထားသော User တစ်ဦးတည်းကို Credit ပေးရန် (နှုတ်လိုပါက အနှုတ်ဂဏန်း သုံးပါ)။\n\n"
        
        "✉️ *တိုက်ရိုက်ဆက်သွယ်ခြင်း*\n"
        "• `/send` [USER_ID] [စာသား] \n"
        "  User တစ်ဦးတည်းဆီသို့ သီးသန့်စာ (သို့မဟုတ်) ပုံ ပို့ရန်။\n\n"
        
        "🚫 *ထိန်းချုပ်မှုနှင့် ပိတ်ပင်ခြင်း*\n"
        "• `/ban` [USER_ID] \n"
        "  စည်းကမ်းဖောက်ဖျက်သူကို Bot အသုံးပြုခွင့် ပိတ်ရန်။\n"
        "• `/unban` [USER_ID] \n"
        "  ပိတ်ထားသော User ကို အသုံးပြုခွင့် ပြန်ဖွင့်ပေးရန်။\n"
        "• `/banned_count` \n"
        "  လက်ရှိ Ban ထားသော User အရေအတွက်ကို ကြည့်ရန်။\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")
    

# --- ၈။ Main Execution ---
def main():
    init_db()
    actual_token = os.environ.get("BOT_TOKEN", TOKEN)
    app = Application.builder().token(actual_token).concurrent_updates(True).build()
    job_queue = app.job_queue

    # Automation Jobs (မူလအတိုင်း)
    job_queue.run_daily(reset_daily_credits, time=time(hour=17, minute=30, second=0))
    job_queue.run_daily(send_morning_quote, time=time(hour=0, minute=5, second=0))
    job_queue.run_daily(send_daily_stats_to_admin, time=time(hour=23, minute=59, second=0))

    # --- ၇။ App ထဲသို့ Handlers အားလုံး ထည့်သွင်းခြင်း (Duplication ရှင်းပြီးသား) ---
    
    # Global Commands
    app.add_handler(CommandHandler("help", admin_help))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    # Conversation Handler (တစ်ခုတည်းပဲ ထားရှိပါသည်)
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🎯 ကံကြမ္မာ လမ်းညွှန်$"), guide_start),
            MessageHandler(filters.Regex("^🔮 Tarot မေးမည်$"), tarot_init),
            MessageHandler(filters.Regex("^✍️ အကြံပြုစာပို့ရန်$"), handle_suggestion),
            MessageHandler(filters.Regex("^🎂 မွေးနေ့မှတ်မည်$"), manage_bday),
            MessageHandler(filters.Regex("^♈ ရာသီခွင်ပင်ကိုယ်စရိုက်$"), zodiac_personality_start)
        ],
        states={
            SELECT_GUIDE_TYPE: [CallbackQueryHandler(handle_guide_choice, pattern="^guide_")],
            GET_GUIDE_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_guide_result)],
            WAIT_BDAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bday)],
            WAIT_CAT: [CallbackQueryHandler(handle_cat)],
            WAIT_SUB: [CallbackQueryHandler(handle_sub)],
            WAIT_NUMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_reading)],
            WAIT_FEEDBACK: [CallbackQueryHandler(handle_feedback_choice, pattern="^(give_review|skip_review)$")],
            WAIT_REVIEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text)],
            WAIT_SUGGESTION_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_suggestion_text)],
            WAIT_PERMANENT_BDAY: [
                CallbackQueryHandler(bday_manage_callback, pattern="^(save_bday_start|delete_bday_data)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_permanent_bday)],
            WAIT_ZODIAC_PERS: [CallbackQueryHandler(handle_zodiac_choice, pattern="^pers_")],
            WAIT_GENDER_PERS: [CallbackQueryHandler(handle_gender_personality, pattern="^pers_gender_")]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_tarot, pattern="^cancel_tarot$"),
            CommandHandler("cancel", cancel_tarot)
        ],
        allow_reentry=True,
        per_message=False
    )

    love_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^❤️ ချစ်သူနဲ့ကိုက်ညီမှု\(RS\)$"), love_start)],
        states={
            CHOOSE_LOVE_METHOD: [CallbackQueryHandler(love_method_choice, pattern="^love_method_")],
            TAROT_LOVE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tarot_love_input),
                               CallbackQueryHandler(love_cancel_handler, pattern='^love_cancel$')],
            ZODIAC_MALE: [CallbackQueryHandler(zodiac_male_choice, pattern="^zlove_")],
            ZODIAC_FEMALE: [CallbackQueryHandler(zodiac_female_choice, pattern="^zlove_")],
            ZODIAC_CONFIRM: [CallbackQueryHandler(zodiac_confirm_process, pattern="^(zlove_confirm_yes|zlove_back_male|love_cancel)$")],
            WAIT_FEEDBACK: [CallbackQueryHandler(handle_feedback_choice, pattern="^(give_review|skip_review)$")],
            WAIT_REVIEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text)],
        },
        fallbacks=[CallbackQueryHandler(love_cancel_handler, pattern='^love_cancel$')],
        allow_reentry=True,
        per_message=False
    )
    
    app.add_handler(love_conv_handler)
    app.add_handler(conv_handler)
    
    # အခြား Message & Admin Handlers (မူလအတိုင်း)
    app.add_handler(MessageHandler(filters.Regex("^❓ အသုံးပြုနည်း$"), handle_help))
    app.add_handler(MessageHandler(filters.Regex("^📤 Share မည်$"), share_logic))
    app.add_handler(CommandHandler("unpost", unpost))
    app.add_handler(MessageHandler(filters.Regex("(?i)^/post") | filters.CaptionRegex("(?i)^/post"), post))
    app.add_handler(CommandHandler("add_credit", add_credit))
    app.add_handler(CommandHandler("check", check_user))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("banned_count", get_banned_count))
    app.add_handler(CallbackQueryHandler(handle_daily_card, pattern="^daily_random_card$"))
    app.add_handler(CommandHandler("send", send_private))
    app.add_handler(CommandHandler("giftall", gift_all_users))
    app.add_error_handler(error_handler)

    print("🚀 Tarot Bot is integrated and starting on Render...")
    app.run_polling(stop_signals=False)

# --- Gunicorn နဲ့ Bot ကို တွဲနှိုးပေးမယ့်အပိုင်း ---
# main() ကို Thread တစ်ခုနဲ့ နောက်ကွယ်မှာ နှိုးထားမှ Gunicorn က ရှေ့ကနေ Web အလုပ်ကို လုပ်နိုင်မှာပါ
t = Thread(target=main)
t.daemon = True
t.start()

if __name__ == "__main__":
    # ဒါကတော့ local မှာ python tarot.py နဲ့ စမ်းတဲ့အခါ သုံးဖို့ပါ
    # main() ကို Thread နဲ့ နှိုးထားပြီးသားမို့လို့ ဒီမှာ Flask ကိုပဲ Run ပါမယ်
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host='0.0.0.0', port=port)
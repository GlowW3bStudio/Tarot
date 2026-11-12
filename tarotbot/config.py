"""Settings. Render environment variables override the defaults below."""
import os
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value.lstrip("-").isdigit() else default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except ValueError:
        return default


# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_DIR = BASE_DIR / "cards"
TAROT_JSON_PATH = BASE_DIR / "tarot_data.json"
# GitHub website က 25MB ထက်ကြီးတဲ့ဖိုင် မလက်ခံလို့ ချုံ့ထားတဲ့ .gz ကို ဦးစားပေး သုံးသည်
TAROT_GZ_PATH = BASE_DIR / "tarot_data.json.gz"
CARD_DB_PATH = BASE_DIR / "tarot_cards.db"
LOG_PATH = BASE_DIR / "bot_errors.log"

# --- Secrets & IDs (Render → Environment) -------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_SSLMODE = os.environ.get("DB_SSLMODE", "require")
ADMIN_ID = _int_env("ADMIN_ID", 7051052390)
STAFF_ID = _int_env("STAFF_ID", 6663409535)
# Rich message ကို "admin" mode မှာ စမ်းသပ်ခွင့်ရှိသူများ
TESTER_IDS = frozenset({ADMIN_ID, STAFF_ID})
# Telegram system account များ (လူမဟုတ်ပါ — user စာရင်းထဲ မထည့်ပါ)
#   777000 = Telegram အသိပေး account၊ 136817688 = Channel Bot၊ 1087968824 = Group Anonymous Bot
TELEGRAM_SERVICE_IDS = frozenset({777000, 136817688, 1087968824})
PORT = _int_env("PORT", 10000)
BOT_USERNAME = os.environ.get("BOT_USERNAME", "TarotBayDinBot")

# --- Time --------------------------------------------------------------------
TZ = ZoneInfo("Asia/Yangon")
MORNING_HOUR, MORNING_MINUTE = 6, 0          # Daily / Sunday စာ ပို့ချိန်
NIGHT_REPORT_HOUR, NIGHT_REPORT_MINUTE = 23, 55
CREDIT_RESET_HOUR = 0                        # မြန်မာအချိန် ညသန်းခေါင်

# --- Credits -----------------------------------------------------------------
GIFT_CREDITS_LIMIT = 7
DAILY_CREDITS_LIMIT = 3
COOLDOWN_SECONDS = 2

# --- Mass sending (Telegram ခွင့်ပြုချက် ~30 စောင်/စက္ကန့်) -------------------
MASS_SEND_RATE = max(1.0, min(_float_env("SEND_RATE", 20.0), 28.0))
MASS_SEND_WORKERS = 8
PROGRESS_EVERY = 2000

# --- Message lifetimes ---------------------------------------------------------
DAILY_MESSAGE_LIFETIME = timedelta(hours=12)
SUNDAY_DAILY_BUTTON_LIFETIME = timedelta(hours=12)
# Telegram က စာပို့ပြီး ၄၈ နာရီမပြည့်ခင်မှသာ ဖျက်ခွင့်ပေးလို့ ၄၇ နာရီခွဲမှာ ဖျက်ပါမယ်
SUNDAY_MESSAGE_LIFETIME = timedelta(hours=47, minutes=30)
SHORT_NOTICE_LIFETIME_SECONDS = 60

# --- Telegram limits -----------------------------------------------------------
TEXT_LIMIT = 3900        # Telegram limit 4096 ထက် နည်းနည်း လျော့ထားသည်
CAPTION_LIMIT = 1000     # Telegram limit 1024

# --- Database ----------------------------------------------------------------
DB_POOL_SIZE = 10

# --- Rich message modes ---------------------------------------------------------
RICH_MODES = ("off", "admin", "on")
DEFAULT_RICH_MODE = "admin"

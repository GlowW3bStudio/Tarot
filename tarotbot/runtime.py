"""Bot run နေစဉ် မှတ်ထားရမယ့် အချက်များ (settings၊ ပုံ file_id၊ အသုံးပြုမှု စာရင်း စသည်)။"""
import asyncio
import logging
import time
from collections import Counter
from datetime import datetime
from typing import Dict, Optional, Set

from . import buttons, config, db

logger = logging.getLogger(__name__)

# --- Settings (Supabase bot_settings table ထဲ သိမ်းသည်) --------------------------------------
settings: Dict[str, str] = {"rich_mode": config.DEFAULT_RICH_MODE, "premium_emoji": "on"}


def load_settings() -> None:
    try:
        settings.update(db.load_settings())
    except Exception:
        logger.exception("Could not load bot settings; using defaults")
    if settings.get("rich_mode") not in config.RICH_MODES:
        settings["rich_mode"] = config.DEFAULT_RICH_MODE
    buttons.set_premium(settings.get("premium_emoji", "on") == "on")


async def save_setting(key: str, value: str) -> None:
    settings[key] = value
    await db.run(db.save_setting, key, value)
    if key == "premium_emoji":
        buttons.set_premium(value == "on")


def rich_enabled_for(user_id: Optional[int]) -> bool:
    mode = settings.get("rich_mode", config.DEFAULT_RICH_MODE)
    if mode == "on":
        return True
    if mode == "admin":
        return user_id in config.TESTER_IDS
    return False


# --- ပုံ file_id cache (ပုံကို တစ်ခါပဲ upload လုပ်ရအောင်) -------------------------------------
media_cache: Dict[str, str] = {}


def load_media_cache() -> None:
    try:
        media_cache.update(db.load_media_cache())
    except Exception:
        logger.exception("Could not load media cache")


async def remember_media(key: str, file_id: str) -> None:
    if not file_id or media_cache.get(key) == file_id:
        return
    media_cache[key] = file_id
    try:
        await db.run(db.save_media, key, file_id)
    except Exception:
        logger.exception("Could not save media cache for %s", key)


# --- Feature အသုံးပြုမှု စာရင်း -----------------------------------------------------------------
FEATURE_NAMES = {
    "tarot": "🔮 Tarot မေးမည်",
    "rs": "❤️ ချစ်သူနဲ့ကိုက်ညီမှု",
    "love_future": "❤️ ဖူးစာဖက်ကြည့်ရန်",
    "guide": "🎯 ကံကြမ္မာ လမ်းညွှန်",
    "personality": "♈ ရာသီပင်ကိုယ်စရိုက်",
    "bday": "🎂 မွေးနေ့မှတ်မည်",
    "help": "❓ အသုံးပြုနည်း",
    "suggest": "✍️ အကြံပြုစာ",
    "share": "📤 Share",
    "daily_card": "🌟 Daily ကဒ်ရွေး",
    "weekly_card": "🗓 Weekly ကဒ်ရွေး",
}
_usage: Counter = Counter()


def record_usage(feature: str, user_id: int) -> None:
    if user_id == config.ADMIN_ID:
        return
    _usage[(datetime.now(config.TZ).date(), feature, user_id)] += 1


async def flush_usage() -> None:
    if not _usage:
        return
    rows = [(day, feature, uid, taps) for (day, feature, uid), taps in _usage.items()]
    _usage.clear()
    try:
        await db.run(db.add_usage, rows)
    except Exception:
        logger.exception("Could not save %d usage rows; keeping them for next time", len(rows))
        for day, feature, uid, taps in rows:
            _usage[(day, feature, uid)] += taps


# --- Network ခဏထစ်မှု (admin ကို ချက်ချင်း မပို့ဘဲ ညစဉ် report မှာ စုပြရန်) ------------------------
network_issues: Counter = Counter()


def count_network_issue(kind: str) -> None:
    network_issues[kind] += 1


def take_network_issues() -> Counter:
    taken = Counter(network_issues)
    network_issues.clear()
    return taken


# --- အခြား -------------------------------------------------------------------------------------------
active_users: Set[int] = set()
busy: Dict[str, bool] = {"broadcast": False, "unpost": False, "morning": False, "actions": False}
_last_click: Dict[int, float] = {}
_admin_notices: Dict[str, float] = {}


def too_fast(user_id: int, seconds: float) -> bool:
    """မူလ bot ရဲ့ cooldown — အလွန်မြန်မြန် ထပ်နှိပ်ရင် လျစ်လျူရှုသည်။"""
    now = time.monotonic()
    last = _last_click.get(user_id)
    if last is not None and now - last < seconds:
        return True
    _last_click[user_id] = now
    if len(_last_click) > 50000:
        cutoff = now - 3600
        for uid in [u for u, t in _last_click.items() if t < cutoff]:
            _last_click.pop(uid, None)
    return False


async def notify_admin_once(bot, key: str, text: str, every_seconds: int = 3600) -> None:
    """တူညီတဲ့ သတိပေးချက်ကို admin ဆီ တစ်နာရီ တစ်ခါပဲ ပို့သည်။"""
    now = time.monotonic()
    if now - _admin_notices.get(key, -every_seconds - 1) < every_seconds:
        return
    _admin_notices[key] = now
    try:
        await bot.send_message(config.ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("Could not notify admin")


def memory_mb() -> Optional[float]:
    """လက်ရှိ RAM အသုံး (MB) — Render (Linux) မှာ /proc ကနေ ဖတ်သည်။"""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024) if peak > 10_000_000 else peak / 1024
    except Exception:
        return None


def lock(name: str) -> bool:
    """busy flag ကို ယူသည်။ အခြားတစ်ခု လုပ်နေဆဲဆိုရင် False။"""
    if busy.get(name):
        return False
    busy[name] = True
    return True


def unlock(name: str) -> None:
    busy[name] = False


def background(coro, name: str = "task"):
    """Task ကို နောက်ကွယ်မှာ run ပြီး error ဖြစ်ရင် log ရေးသည်။"""
    task = asyncio.create_task(coro, name=name)

    def _done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            logger.error("Background task %s failed", name, exc_info=t.exception())

    task.add_done_callback(_done)
    return task

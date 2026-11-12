"""Admin command များ (စာသားအားလုံး မြန်မာလို)။"""
import logging
from datetime import timedelta

from telegram import MessageEntity, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from . import broadcast, buttons, config, content, db, delivery, handlers_user, jobs, reports, runtime, sender
from .richtext import esc

logger = logging.getLogger(__name__)

ADMIN_HELP = (
    "✨ <b>Tarot BayDin — Admin Command များ</b>\n"
    "<i>(command ကို နှိပ်ရင် copy ကူးလို့ရပါတယ်)</i>\n\n"
    "📢 <b>ကြေညာချက်</b>\n"
    "• <code>/post</code> — ပို့ချင်တဲ့ စာ/ပုံကို Reply လုပ်ပြီး ရိုက်ပါ။ User အားလုံးဆီ ပို့ပါမယ်။\n"
    "• <code>/unpost</code> — နောက်ဆုံး ကြေညာချက်ကို user အားလုံးဆီက ပြန်ဖျက်ပါမယ် (ပို့ပြီး ၄၈ နာရီအတွင်းသာ)။\n\n"
    "📊 <b>စာရင်းများ</b>\n"
    "• <code>/admin</code> — User အရေအတွက်၊ RAM နဲ့ bot အခြေအနေ\n"
    "• <code>/stats</code> — ၇ ရက်စာ feature အသုံးပြုမှု (<code>/stats 30</code> ဆိုရင် ရက် ၃၀ စာ)\n"
    "• <code>/check USER_ID</code> — User တစ်ဦးရဲ့ အချက်အလက်\n\n"
    "🎁 <b>မေးခွင့် (Credit)</b>\n"
    "• <code>/add_credit USER_ID အရေအတွက်</code> — User တစ်ဦးကို ပေးရန် (နှုတ်ချင်ရင် အနှုတ်ဂဏန်း)\n"
    "• <code>/giftall အရေအတွက်</code> — User အားလုံးကို ပေးရန်\n\n"
    "✉️ <b>တိုက်ရိုက်ပို့ရန်</b>\n"
    "• <code>/send USER_ID</code> — ပို့ချင်တဲ့ စာ/ပုံကို Reply လုပ်ပြီး ရိုက်ပါ။\n\n"
    "🚫 <b>ပိတ်ပင်ခြင်း</b>\n"
    "• <code>/ban USER_ID</code> — အသုံးပြုခွင့် ပိတ်ရန်\n"
    "• <code>/unban USER_ID</code> — ပြန်ဖွင့်ရန်\n"
    "• <code>/banned_count</code> — ပိတ်ထားသူ အရေအတွက်\n\n"
    "⚙️ <b>ဆက်တင်နဲ့ စမ်းသပ်ခြင်း</b>\n"
    "• <code>/rich</code> — ဟောစာတမ်းပုံစံ (off / admin / on)\n"
    "• <code>/premium</code> — ခလုတ်ပေါ်က premium emoji (on / off)\n"
    "• <code>/emoji_id</code> — Premium emoji ပါတဲ့ စာကို Reply လုပ်ပြီး ID ကြည့်ရန်\n"
    "• <code>/test_morning</code> — မနက်စာကို ကိုယ့်ဆီပဲ စမ်းပို့ရန် (<code>/test_morning sunday</code>)"
)
RICH_HELP = {
    "off": "ပိတ်ထား — user အားလုံး ပုံမှန် message နဲ့ ရပါမယ်",
    "admin": "Admin နဲ့ staff ဆီမှာပဲ rich message စမ်းနေတယ်",
    "on": "User အားလုံး rich message နဲ့ ရပါမယ်",
}


def _is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == config.ADMIN_ID)


async def reply(update: Update, text: str) -> None:
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)


def _int_arg(context, index: int):
    try:
        return int(context.args[index])
    except (IndexError, ValueError, TypeError):
        return None


# --- Help / စာရင်းများ ------------------------------------------------------------------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await handlers_user.show_help(update, context)
        return
    await reply(update, ADMIN_HELP)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    rows = await jobs.user_count_rows(jobs.now_local().date())
    memory = runtime.memory_mb()
    if memory:
        rows.append(("💾 RAM အသုံး", f"{memory:.0f} MB / 512 MB"))
    pending = await db.run(db.pending_action_count)
    rows += [
        ("🗑 ဖျက်ရန်စောင့်နေသော message", f"{pending:,}"),
        ("📖 Rich message", RICH_HELP[runtime.settings.get("rich_mode", config.DEFAULT_RICH_MODE)]),
        ("✨ Premium emoji ခလုတ်", "ဖွင့်ထား" if buttons.premium_enabled() else "ပိတ်ထား"),
        ("📤 စာပို့နှုန်း", f"တစ်စက္ကန့် {config.MASS_SEND_RATE:g} စောင်"),
        ("📚 ကဒ် data ဖိုင်", content.card_source().name),
    ]
    await reports.send_table(context.bot, update.effective_chat.id, "🛡 Admin ထိန်းချုပ်ခန်း", rows)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    days = _int_arg(context, 0) or 7
    days = max(1, min(days, 120))
    last = jobs.now_local().date()
    first = last - timedelta(days=days - 1)
    usage = await jobs.usage_rows(first, last)
    daily = await db.run(db.usage_daily_users, first, last)
    daily_rows = [(f"{day:%d-%m}", f"{users:,}") for day, users in daily]
    await reports.send_table(
        context.bot, update.effective_chat.id, f"📈 Feature အသုံးပြုမှု — ရက် {days} ရက်",
        usage or [("မှတ်တမ်း မရှိသေးပါ", "-", "-")], header=("Feature", "နှိပ်ကြိမ်", "လူဦးရေ"),
        extra_tables=[("👥 တစ်ရက်ချင်း သုံးသူ", daily_rows, ("ရက်", "လူဦးရေ"))],
    )


async def check_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    target = _int_arg(context, 0)
    if target is None:
        await reply(update, "❌ သုံးပုံ – <code>/check USER_ID</code>")
        return
    row = await db.run(db.user_info, target)
    if not row:
        await reply(update, f"❌ ID <code>{target}</code> နဲ့ user ကို ရှာမတွေ့ပါ။")
        return
    gift, daily, birthday, zodiac, bday_status, joined, last_active, active, banned = row
    status = "⛔ ပိတ်ပင်ထား" if banned else ("✅ အသုံးပြုနေဆဲ" if active else "🚫 Bot ကို ပိတ်ထား")
    rows = [
        ("🆔 ID", str(target)),
        ("💳 လက်ကျန်မေးခွင့်", f"{(gift or 0) + (daily or 0)} ကြိမ် (လက်ဆောင် {gift or 0} + နေ့စဉ် {daily or 0})"),
        ("🎂 မွေးနေ့", (birthday or "-") + ("" if bday_status else " (ဖျက်ထား)")),
        ("♈ ရာသီ", zodiac or "-"),
        ("📅 စတင်သုံးသည့်နေ့", f"{joined:%Y-%m-%d}" if joined else "-"),
        ("🕒 နောက်ဆုံး active", f"{last_active:%Y-%m-%d}" if last_active else "-"),
        ("📶 အခြေအနေ", status),
    ]
    await reports.send_table(context.bot, update.effective_chat.id, "👤 အသုံးပြုသူ အချက်အလက်", rows)


# --- Credit ------------------------------------------------------------------------------------------
async def add_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    target, amount = _int_arg(context, 0), _int_arg(context, 1)
    if target is None or amount is None or amount == 0:
        await reply(update, "❌ သုံးပုံ – <code>/add_credit USER_ID အရေအတွက်</code>\n(နှုတ်ချင်ရင် အနှုတ်ဂဏန်း၊ ဥပမာ -2)")
        return
    if not await db.run(db.add_gift_credits, target, amount):
        await reply(update, f"❌ ID <code>{target}</code> နဲ့ user မရှိပါ။")
        return
    if amount > 0:
        user_msg = f"🎁 လူကြီးမင်းအတွက် အခမဲ့မေးမြန်းခွင့် ({amount}) ကြိမ် ထည့်သွင်းပေးလိုက်ပါပြီရှင်။ ✨"
    else:
        user_msg = f"ℹ️ လူကြီးမင်း၏ အခမဲ့မေးမြန်းခွင့်ထဲမှ ({abs(amount)}) ကြိမ်ကို ပြန်လည်နှုတ်ယူလိုက်ပါပြီရှင်။"
    try:
        await context.bot.send_message(target, user_msg)
        note = "User ကိုလည်း အသိပေးပြီးပါပြီ။"
    except TelegramError as exc:
        note = f"User ဆီ အသိပေးစာ မရောက်ပါ ({sender.classify(exc)[1]})။"
    word = "ပေး" if amount > 0 else "နှုတ်"
    await reply(update, f"✅ ID <code>{target}</code> ကို မေးခွင့် {abs(amount)} ကြိမ် {word}ပြီးပါပြီ။\n{note}")


async def gift_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    amount = _int_arg(context, 0)
    if not amount or amount < 1:
        await reply(update, "❌ သုံးပုံ – <code>/giftall အရေအတွက်</code>\nဥပမာ – <code>/giftall 5</code> (user အားလုံးကို ၅ ကြိမ်စီ)")
        return
    try:
        count = await db.run(db.gift_all, amount)
        await reply(update, f"✅ User {count:,} ဦးလုံးကို မေးခွင့် {amount} ကြိမ်စီ လက်ဆောင်ပေးပြီးပါပြီ။")
    except Exception:
        logger.exception("gift_all failed")
        await reply(update, "❌ မေးခွင့်ပေးရာမှာ အမှားဖြစ်သွားပါတယ်။ ခဏနေမှ ပြန်ကြိုးစားပါ။")


# --- Ban -----------------------------------------------------------------------------------------------
async def _set_ban(update: Update, context, banned: bool) -> None:
    if not _is_admin(update):
        return
    target = _int_arg(context, 0)
    command = "ban" if banned else "unban"
    if target is None:
        await reply(update, f"❌ သုံးပုံ – <code>/{command} USER_ID</code>")
        return
    if not await db.run(db.set_banned, target, banned):
        await reply(update, f"❌ ID <code>{target}</code> နဲ့ user မရှိပါ။")
        return
    cached = context.application.user_data.get(target)
    if cached is not None:
        cached["is_banned"] = banned
    if banned:
        await reply(update, f"🚫 ID <code>{target}</code> ကို အသုံးပြုခွင့် ပိတ်လိုက်ပါပြီ။")
    else:
        await reply(update, f"✅ ID <code>{target}</code> ကို အသုံးပြုခွင့် ပြန်ဖွင့်ပေးလိုက်ပါပြီ။")


async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_ban(update, context, True)


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_ban(update, context, False)


async def banned_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    count = await db.run(db.count_banned)
    await reply(update, f"🚫 လက်ရှိ အသုံးပြုခွင့် ပိတ်ပင်ထားသူ – <b>{count:,}</b> ဦး")


# --- တိုက်ရိုက်ပို့ / ကြေညာချက် -----------------------------------------------------------------------
async def send_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    target = _int_arg(context, 0)
    source = update.message.reply_to_message
    if target is None or source is None:
        await reply(update, "❌ သုံးပုံ – ပို့ချင်တဲ့ စာ/ပုံကို Reply လုပ်ပြီး <code>/send USER_ID</code> လို့ ရိုက်ပါ။")
        return
    try:
        await delivery.send_with_markup(context.bot.copy_message, chat_id=target, from_chat_id=update.effective_chat.id,
                                        message_id=source.message_id, reply_markup=buttons.main_keyboard)
        await reply(update, f"✅ ID <code>{target}</code> ဆီ ပို့ပြီးပါပြီ။")
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"❌ ပို့လို့ မရပါ – {esc(sender.classify(exc)[1])}")


async def post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    if runtime.busy.get("unpost") or not runtime.lock("broadcast"):
        await reply(update, "⏳ ကြေညာချက် ပို့ခြင်း/ဖျက်ခြင်း လုပ်နေဆဲပါ။ ပြီးမှ ထပ်လုပ်ပါ။")
        return
    try:
        target = update.message.reply_to_message
        if target is None:
            await reply(update, "❌ ပို့ချင်တဲ့ စာ/ပုံကို Reply လုပ်ပြီး <code>/post</code> လို့ ရိုက်ပါ။")
            runtime.unlock("broadcast")
            return
        if target.media_group_id:
            await reply(update, "❌ ပုံအများကြီး (album) ကို တစ်ခါတည်း မပို့နိုင်ပါ။ ပုံတစ်ပုံတည်း ပြန်ပို့ပြီး <code>/post</code> လုပ်ပါ။")
            runtime.unlock("broadcast")
            return
        try:
            plan = broadcast.build_photo_plan(target)
        except ValueError as exc:
            await reply(update, f"❌ {esc(str(exc))}")
            runtime.unlock("broadcast")
            return
        users = await db.run(db.recipients, 0)
        mode = ("Caption ရှည်လို့ ပုံအရင်၊ စာနောက်မှ ခွဲပို့ပါမယ်။" if plan else "မူရင်းစာ/ပုံအတိုင်း ပို့ပါမယ်။")
        await reply(update, f"⏳ Admin ဆီ အရင်စမ်းပို့ပြီး user {len(users):,} ဦးဆီ စပို့ပါမယ်။\n{mode}")
        runtime.background(broadcast.run_broadcast(context.bot, update.effective_chat.id, target.message_id, plan, users),
                           name="broadcast")
    except Exception:
        runtime.unlock("broadcast")
        raise


async def unpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    if runtime.busy.get("broadcast") or not runtime.lock("unpost"):
        await reply(update, "⏳ ကြေညာချက် ပို့ခြင်း/ဖျက်ခြင်း လုပ်နေဆဲပါ။ ပြီးမှ ထပ်လုပ်ပါ။")
        return
    runtime.background(broadcast.run_unpost(context.bot, update.effective_chat.id), name="unpost")


# --- ဆက်တင်များ -----------------------------------------------------------------------------------------
async def rich_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    wanted = (context.args[0].lower() if context.args else "")
    current = runtime.settings.get("rich_mode", config.DEFAULT_RICH_MODE)
    if wanted not in config.RICH_MODES:
        options = "\n".join(f"• <code>/rich {m}</code> — {RICH_HELP[m]}" for m in config.RICH_MODES)
        await reply(update, f"📖 <b>Rich message</b> — လက်ရှိ: <b>{current}</b> ({RICH_HELP[current]})\n\n{options}")
        return
    await runtime.save_setting("rich_mode", wanted)
    await reply(update, f"✅ Rich message ကို <b>{wanted}</b> လို့ ပြောင်းပြီးပါပြီ — {RICH_HELP[wanted]}")


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    wanted = (context.args[0].lower() if context.args else "")
    if wanted not in ("on", "off"):
        state = "ဖွင့်ထား" if buttons.premium_enabled() else "ပိတ်ထား"
        await reply(update, f"✨ <b>Premium emoji ခလုတ်</b> — လက်ရှိ: <b>{state}</b>\n\n"
                            "• <code>/premium on</code> — ဖွင့်ရန်\n• <code>/premium off</code> — ပိတ်ရန် (ပုံမှန် emoji ပြမည်)")
        return
    await runtime.save_setting("premium_emoji", wanted)
    if wanted == "on":
        await jobs.validate_emoji_job(context)
    await reply(update, "✅ Premium emoji ခလုတ်ကို " + ("ဖွင့်" if wanted == "on" else "ပိတ်") + "လိုက်ပါပြီ။")


async def emoji_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    source = update.message.reply_to_message or update.message
    found = []
    for entities, text in ((source.entities, source.text), (source.caption_entities, source.caption)):
        for entity in entities or ():
            if entity.type == MessageEntity.CUSTOM_EMOJI and entity.custom_emoji_id:
                found.append((source.parse_entity(entity) if source.text else source.parse_caption_entity(entity),
                              entity.custom_emoji_id))
    if not found:
        await reply(update, "ℹ️ Premium emoji ပါတဲ့ စာကို Reply လုပ်ပြီး <code>/emoji_id</code> လို့ ရိုက်ပါ။")
        return
    lines = [f"{index}. {esc(char)} – <code>{eid}</code>" for index, (char, eid) in enumerate(found, 1)]
    await reply(update, "✨ <b>Premium emoji ID များ</b>\n\n" + "\n".join(lines))


async def test_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    sunday = bool(context.args) and context.args[0].lower().startswith("sun")
    await jobs.run_morning(context.bot, test_chat=update.effective_chat.id, force_sunday=sunday or None)


def handlers():
    return [
        CommandHandler("help", help_command),
        CommandHandler("admin", admin_panel),
        CommandHandler("stats", stats),
        CommandHandler("check", check_user),
        CommandHandler("add_credit", add_credit),
        CommandHandler("giftall", gift_all),
        CommandHandler("ban", ban_user),
        CommandHandler("unban", unban_user),
        CommandHandler("banned_count", banned_count),
        CommandHandler("send", send_private),
        CommandHandler("unpost", unpost),
        MessageHandler(filters.Regex(r"(?i)^/post\b") | filters.CaptionRegex(r"(?i)^/post\b"), post),
        CommandHandler("rich", rich_mode),
        CommandHandler("premium", premium),
        CommandHandler("emoji_id", emoji_id),
        CommandHandler("test_morning", test_morning),
    ]

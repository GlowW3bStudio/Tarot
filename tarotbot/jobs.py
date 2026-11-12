"""အချိန်ဇယားအလုပ်များ (မြန်မာစံတော်ချိန်)။

06:00  မနက်စာ (တနင်္လာ–စနေ: daily၊ တနင်္ဂနွေ: daily + weekly ခလုတ် ၂ ခုပါ စာတစ်စောင်)
00:00  Daily credit ပြန်ဖြည့်
23:55  ညစဉ် report (admin)
03:00  Data အဟောင်း ရှင်းလင်း
၁ မိနစ်တစ်ခါ  သက်တမ်းကုန်တဲ့ message ဖျက်/ခလုတ်ဖြုတ်၊ အသုံးပြုမှု စာရင်း သိမ်း
"""
import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from telegram.error import BadRequest, TelegramError

from . import buttons, config, db, delivery, reports, runtime, sender, texts
from .delivery import Photo

logger = logging.getLogger(__name__)
DAILY_PHOTO = Photo("daily", config.CARDS_DIR / "daily.jpg")
WELCOME_PHOTO = Photo("welcome", config.CARDS_DIR / "welcome.jpg")
MORNING_STATE_KEY = "morning_run"


def now_local() -> datetime:
    return datetime.now(config.TZ)


def _utc_bounds(day) -> Tuple[datetime, datetime]:
    """မြန်မာရက်တစ်ရက်ရဲ့ အစ/အဆုံးကို (DB ထဲက timestamp လို) UTC naive အဖြစ် ပြောင်းသည်။"""
    start = datetime.combine(day, time(0, 0), tzinfo=config.TZ)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


def _morning_state() -> dict:
    try:
        return json.loads(runtime.settings.get(MORNING_STATE_KEY, "{}"))
    except ValueError:
        return {}


async def _save_morning_state(state: dict) -> None:
    try:
        await runtime.save_setting(MORNING_STATE_KEY, json.dumps(state))
    except Exception:
        logger.exception("Could not save morning progress")


# --- မနက်စာ -------------------------------------------------------------------------------------
async def run_morning(bot, *, test_chat: Optional[int] = None, force_sunday: Optional[bool] = None) -> None:
    """မနက်စာကို user အားလုံးဆီ (test_chat ပေးရင် အဲ့ chat တစ်ခုတည်း) ပို့သည်။"""
    if test_chat is None and not runtime.lock("morning"):
        logger.warning("Morning send is already running")
        return
    try:
        local = now_local()
        sunday = local.weekday() == 6 if force_sunday is None else force_sunday
        quote = texts.DAY_QUOTES[6 if sunday else local.weekday()]
        caption = (texts.SUNDAY_INVITE if sunday else texts.DAILY_INVITE).format(quote=quote)
        file_id = await delivery.ensure_file_id(bot, DAILY_PHOTO)
        today = local.date().isoformat()

        if test_chat is not None:
            recipients, start_after = [test_chat], 0
        else:
            state = _morning_state()
            start_after = int(state.get("cursor", 0)) if state.get("date") == today and not state.get("done") else 0
            recipients = await db.run(db.recipients, start_after)
            await _save_morning_state({"date": today, "cursor": start_after, "done": False})

        actions: List[tuple] = []

        async def flush_actions() -> None:
            if actions:
                rows = actions[:]
                actions.clear()
                try:
                    await db.run(db.add_actions, rows)
                except Exception:
                    logger.exception("Could not save %d scheduled actions", len(rows))

        def markup():
            return buttons.morning_keyboard(daily=True, weekly=sunday)

        async def send_one(uid: int) -> None:
            if file_id:
                msg = await delivery.send_with_markup(bot.send_photo, chat_id=uid, photo=file_id, caption=caption,
                                                      reply_markup=markup, limiter=sender.LIMITER)
            else:
                msg = await delivery.send_with_markup(bot.send_message, chat_id=uid, text=caption,
                                                      reply_markup=markup, limiter=sender.LIMITER)
            sent = datetime.now(timezone.utc)
            if sunday:
                actions.append((uid, msg.message_id, "strip_daily", sent + config.SUNDAY_DAILY_BUTTON_LIFETIME))
                actions.append((uid, msg.message_id, "delete", sent + config.SUNDAY_MESSAGE_LIFETIME))
            else:
                actions.append((uid, msg.message_id, "delete", sent + config.DAILY_MESSAGE_LIFETIME))
            if len(actions) >= 500:
                await flush_actions()

        async def on_inactive(ids: List[int]) -> None:
            await db.run(db.mark_inactive, ids)
            runtime.active_users.difference_update(ids)

        async def on_progress(result: sender.MassResult) -> None:
            await flush_actions()
            if test_chat is None:
                # bot ပြန်စတင်ရင် ဒီနေရာကနေ ဆက်ပို့နိုင်ရန် (အနည်းငယ် ထပ်ရောက်နိုင်သည်)
                index = max(0, result.attempted - 2 * config.MASS_SEND_WORKERS)
                cursor = recipients[index - 1] if index > 0 else start_after
                await _save_morning_state({"date": today, "cursor": cursor, "done": False})

        result = await sender.run_mass(recipients, send_one, on_inactive=on_inactive,
                                       on_progress=on_progress, progress_every=1000)
        await flush_actions()
        if test_chat is None:
            await _save_morning_state({"date": today, "cursor": 0, "done": True})
            title = "🌅 တနင်္ဂနွေ မနက်စာ (daily + weekly) ပို့ပြီးပါပြီ" if sunday else "🌅 မနက်စာ ပို့ပြီးပါပြီ"
            await reports.send_mass_report(bot, title, result)
    finally:
        if test_chat is None:
            runtime.unlock("morning")


async def morning_job(context) -> None:
    await run_morning(context.bot)


async def resume_morning_job(context) -> None:
    """Bot ပြန်စတင်ချိန်မှာ ဒီနေ့ မနက်စာ မပြီးသေးရင် (မနက် ၁၀ နာရီမတိုင်ခင်) ဆက်ပို့သည်။"""
    local = now_local()
    if not (time(config.MORNING_HOUR, config.MORNING_MINUTE) <= local.time() < time(10, 0)):
        return
    state = _morning_state()
    if state.get("date") == local.date().isoformat() and state.get("done"):
        return
    logger.info("Resuming/sending today's morning message after restart")
    await run_morning(context.bot)


# --- သက်တမ်းကုန် message များ ---------------------------------------------------------------------
async def process_actions_job(context) -> None:
    if not runtime.lock("actions"):
        return
    bot = context.bot
    try:
        rows = await db.run(db.due_actions, datetime.now(timezone.utc), 2000)
        if not rows:
            return
        finished: List[int] = []

        async def act(row) -> None:
            action_id, chat_id, message_id, action = row
            try:
                if action == "strip_daily":
                    await delivery.send_with_markup(
                        bot.edit_message_reply_markup, chat_id=chat_id, message_id=message_id,
                        reply_markup=lambda: buttons.morning_keyboard(daily=False, weekly=True),
                        limiter=sender.LIMITER,
                    )
                else:
                    try:
                        await sender.call(bot.delete_message, chat_id=chat_id, message_id=message_id)
                    except BadRequest as exc:
                        if "can't be deleted" in str(exc).lower():
                            # ၄၈ နာရီကျော်သွားရင် ဖျက်ခွင့်မရလို့ ခလုတ်ကိုပဲ ဖြုတ်မည်
                            await sender.call(bot.edit_message_reply_markup, chat_id=chat_id,
                                              message_id=message_id, reply_markup=None)
                        else:
                            raise
            except BadRequest as exc:
                text = str(exc).lower()
                if not any(k in text for k in ("not found", "not modified", "can't be edited", "can't be deleted")):
                    logger.warning("Scheduled %s failed for %s/%s: %s", action, chat_id, message_id, exc)
            except TelegramError:
                pass   # bot ကို ပိတ်ထားသူ စသည် — ထပ်မကြိုးစားတော့ပါ
            finally:
                finished.append(action_id)

        await sender.run_mass(rows, act, workers=6, progress_every=10 ** 9)
        await db.run(db.delete_actions, finished)
    except Exception:
        logger.exception("Scheduled actions job failed")
    finally:
        runtime.unlock("actions")


# --- အခြား job များ ---------------------------------------------------------------------------------
async def reset_credits_job(context) -> None:
    try:
        count = await db.run(db.reset_daily_credits)
        logger.info("Daily credits reset for %s users", count)
    except Exception:
        logger.exception("Daily credit reset failed")


async def flush_usage_job(context) -> None:
    await runtime.flush_usage()


async def prune_job(context) -> None:
    try:
        local = now_local()
        await db.run(db.prune_old_rows, local.date() - timedelta(days=120),
                     datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30))
    except Exception:
        logger.exception("Pruning old rows failed")


async def user_count_rows(day) -> List[Tuple[str, str]]:
    start, end = _utc_bounds(day)
    total, reachable, inactive, banned, new_today = await db.run(db.user_counts, start, end)
    return [
        ("👥 User စုစုပေါင်း", f"{total:,}"),
        ("✅ စာပို့၍ရသူ", f"{reachable:,}"),
        ("🚫 Bot ကို ပိတ်ထားသူ", f"{inactive:,}"),
        ("⛔ အသုံးပြုခွင့် ပိတ်ပင်ထားသူ", f"{banned:,}"),
        ("📈 ဒီနေ့ အသစ်ဝင်သူ", f"{new_today:,}"),
    ]


async def usage_rows(first_day, last_day) -> List[Tuple[str, str, str]]:
    await runtime.flush_usage()
    rows = await db.run(db.usage_summary, first_day, last_day)
    return [(runtime.FEATURE_NAMES.get(feature, feature), f"{taps:,}", f"{users:,}") for feature, taps, users in rows]


async def nightly_report_job(context) -> None:
    try:
        today = now_local().date()
        rows = await user_count_rows(today)
        memory = runtime.memory_mb()
        if memory:
            rows.append(("💾 RAM အသုံး", f"{memory:.0f} MB / 512 MB"))
        usage = await usage_rows(today, today)
        await reports.send_table(
            context.bot, config.ADMIN_ID, f"📊 ညစဉ် Report — {today:%d-%m-%Y}", rows,
            extra_tables=[("📈 ဒီနေ့ feature အသုံးပြုမှု", usage, ("Feature", "နှိပ်ကြိမ်", "လူဦးရေ"))],
        )
    except Exception:
        logger.exception("Nightly report failed")


async def warm_media_job(context) -> None:
    """ကဒ်ပုံ file_id မရှိသေးတာတွေကို တစ်ခါပဲ upload လုပ်ထားသည် (ပထမဆုံး deploy အတွက်)။"""
    photos = [DAILY_PHOTO, WELCOME_PHOTO] + [delivery.card_photo(i) for i in range(1, 79)]
    missing = [p for p in photos if p.key not in runtime.media_cache and p.path.exists()]
    if not missing:
        return
    logger.info("Uploading %d card images once to cache their file_ids", len(missing))
    for photo in missing:
        await delivery.ensure_file_id(context.bot, photo)
        await asyncio.sleep(1.5)


async def validate_emoji_job(context) -> None:
    ids = buttons.all_emoji_ids()
    try:
        stickers = await context.bot.get_custom_emoji_stickers(ids)
    except TelegramError as exc:
        logger.warning("Could not validate custom emoji IDs: %s", exc)
        return
    valid = {s.custom_emoji_id for s in stickers}
    invalid = [i for i in ids if i not in valid]
    if invalid:
        buttons.mark_bad_emoji_ids(invalid)
        await runtime.notify_admin_once(
            context.bot, "bad_emoji",
            "⚠️ <b>Premium emoji ID အချို့ မမှန်ပါ</b>\nအဲ့ခလုတ်တွေမှာ ပုံမှန် emoji နဲ့ ပြထားပါတယ်:\n"
            + "\n".join(f"• <code>{i}</code>" for i in invalid),
        )


def schedule(job_queue) -> None:
    tz = config.TZ
    job_queue.run_daily(morning_job, time(config.MORNING_HOUR, config.MORNING_MINUTE, tzinfo=tz), name="morning")
    job_queue.run_daily(reset_credits_job, time(config.CREDIT_RESET_HOUR, 0, tzinfo=tz), name="credits")
    job_queue.run_daily(nightly_report_job, time(config.NIGHT_REPORT_HOUR, config.NIGHT_REPORT_MINUTE, tzinfo=tz),
                        name="nightly_report")
    job_queue.run_daily(prune_job, time(3, 0, tzinfo=tz), name="prune")
    job_queue.run_repeating(process_actions_job, interval=60, first=30, name="actions")
    job_queue.run_repeating(flush_usage_job, interval=60, first=60, name="usage")
    job_queue.run_once(validate_emoji_job, 5, name="validate_emoji")
    job_queue.run_once(warm_media_job, 15, name="warm_media")
    job_queue.run_once(resume_morning_job, 40, name="resume_morning")

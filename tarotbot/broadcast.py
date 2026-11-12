"""/post (ကြေညာချက် ပို့ခြင်း) နဲ့ /unpost (ပြန်ဖျက်ခြင်း)။"""
import logging
from datetime import datetime, timezone
from typing import List, Sequence, Tuple

from telegram import LinkPreviewOptions, MessageEntity, ReplyParameters
from telegram.error import BadRequest, Forbidden, TelegramError

from . import buttons, config, db, delivery, reports, runtime, sender

logger = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_SPLITTABLE = {"bold", "italic", "underline", "strikethrough", "spoiler", "code", "pre",
               "blockquote", "expandable_blockquote"}


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def formatted_chunks(text: str, entities: Sequence[MessageEntity], limit: int = 4096):
    """Caption ရှည်ကို စာ message များအဖြစ် ခွဲသည် — link/emoji ကို အလယ်က မဖြတ်ပါ (မူလ logic)။"""
    boundaries = [0]
    for char in text:
        boundaries.append(boundaries[-1] + len(char.encode("utf-16-le")) // 2)
    positions = {offset: index for index, offset in enumerate(boundaries)}
    for entity in entities:
        if entity.offset not in positions or entity.offset + entity.length not in positions:
            raise ValueError("Caption ထဲက format (link/စာလုံးမည်း စသည်) နေရာ မမှန်လို့ ခွဲမရပါ။")
        if entity.type not in _SPLITTABLE and entity.length > limit:
            raise ValueError("Link (သို့) format တစ်ခုက စာလုံး ၄,၀၉၆ ထက် ရှည်နေပါတယ်။ တိုအောင် ပြင်ပေးပါ။")
    result, start = [], 0
    while start < len(text):
        end = start
        while end < len(text) and boundaries[end + 1] - boundaries[start] <= limit:
            end += 1
        while True:
            crossing = [e for e in entities if e.type not in _SPLITTABLE
                        and e.offset < boundaries[end] < e.offset + e.length]
            if not crossing:
                break
            end = min(positions[e.offset] for e in crossing)
        if end <= start:
            raise ValueError("Caption ကို format မပျက်အောင် ခွဲလို့ မရပါ။")
        left, right = boundaries[start], boundaries[end]
        adjusted = []
        for entity in entities:
            lo, hi = max(left, entity.offset), min(right, entity.offset + entity.length)
            if lo < hi:
                data = entity.to_dict()
                data.update(offset=lo - left, length=hi - lo)
                adjusted.append(MessageEntity.de_json(data, None))
        result.append((text[start:end], adjusted))
        start = end
    return result


class PartialDelivery(Exception):
    def __init__(self, original: Exception, sent_ids: List[int]):
        super().__init__(str(original))
        self.original = original
        self.sent_ids = sent_ids


async def deliver_post(bot, uid: int, source_chat: int, source_message: int, photo_plan) -> List[int]:
    """User တစ်ယောက်ဆီ ကြေညာချက် ပို့ပြီး message id များကို ပြန်ပေးသည်။"""
    if photo_plan is None:
        result = await delivery.send_with_markup(bot.copy_message, chat_id=uid, from_chat_id=source_chat,
                                                 message_id=source_message, reply_markup=buttons.main_keyboard,
                                                 limiter=sender.LIMITER)
        return [result.message_id]
    photo_id, chunks, spoiler = photo_plan
    sent: List[int] = []
    try:
        photo = await sender.call(bot.send_photo, chat_id=uid, photo=photo_id, has_spoiler=spoiler)
        sent.append(photo.message_id)
        for index, (text, entities) in enumerate(chunks):
            last = index == len(chunks) - 1
            msg = await delivery.send_with_markup(
                bot.send_message, chat_id=uid, text=text, entities=entities, parse_mode=None,
                reply_parameters=ReplyParameters(message_id=photo.message_id, allow_sending_without_reply=True),
                link_preview_options=NO_PREVIEW, reply_markup=buttons.main_keyboard if last else None,
                limiter=sender.LIMITER,
            )
            sent.append(msg.message_id)
        return sent
    except Exception as exc:
        if sent:
            raise PartialDelivery(exc, sent) from exc
        raise


def build_photo_plan(message):
    """Caption ၁,၀၂၄ ကျော်တဲ့ ပုံဆိုရင် (ပုံ + စာ) ခွဲပို့ရန် plan။ မလိုရင် None။"""
    if not message.caption or utf16_len(message.caption) <= 1024:
        return None
    if not message.photo:
        raise ValueError("Caption ရှည်ကို ခွဲပို့တာက ပုံတစ်ပုံပါတဲ့ post အတွက်ပဲ ရပါတယ်။")
    chunks = formatted_chunks(message.caption, message.caption_entities or ())
    return message.photo[-1].file_id, chunks, bool(message.has_media_spoiler)


async def run_broadcast(bot, source_chat: int, source_message: int, photo_plan, users: List[int]) -> None:
    batch = datetime.now(timezone.utc).replace(tzinfo=None)
    logs: List[Tuple[int, int, datetime]] = []

    async def flush_logs() -> None:
        if logs:
            rows = logs[:]
            logs.clear()
            try:
                await db.run(db.insert_broadcast_logs, rows)
            except Exception:
                logger.exception("Could not save %d broadcast log rows", len(rows))

    async def send_one(uid: int) -> None:
        try:
            ids = await deliver_post(bot, uid, source_chat, source_message, photo_plan)
        except PartialDelivery as exc:
            logs.extend((uid, mid, batch) for mid in exc.sent_ids)
            raise exc.original
        logs.extend((uid, mid, batch) for mid in ids)
        if len(logs) >= 500:
            await flush_logs()

    try:
        try:
            await send_one(config.ADMIN_ID)
        except Exception as exc:  # noqa: BLE001
            _, reason = sender.classify(exc)
            await bot.send_message(config.ADMIN_ID, "❌ Admin ဆီ စမ်းပို့တာ မအောင်မြင်လို့ user တွေဆီ မပို့ရသေးပါ။\n"
                                                    f"အကြောင်းရင်း – {reason}")
            return
        await flush_logs()
        recipients = [uid for uid in users if uid != config.ADMIN_ID]
        progress = await bot.send_message(config.ADMIN_ID, reports.progress_text("📢 ကြေညာချက်", 0, len(recipients)))

        async def on_progress(result: sender.MassResult) -> None:
            await flush_logs()
            try:
                await progress.edit_text(reports.progress_text("📢 ကြေညာချက်", result.attempted, result.total, result))
            except TelegramError:
                pass

        async def on_inactive(ids: List[int]) -> None:
            await db.run(db.mark_inactive, ids)
            runtime.active_users.difference_update(ids)

        result = await sender.run_mass(recipients, send_one, on_inactive=on_inactive, on_progress=on_progress)
        await flush_logs()
        try:
            await progress.delete()
        except TelegramError:
            pass
        await reports.send_mass_report(bot, "📢 ကြေညာချက် ပို့ပြီးပါပြီ", result,
                                       footer="ပြန်ဖျက်ချင်ရင် /unpost ကို သုံးပါ (၄၈ နာရီအတွင်း)။")
    finally:
        await flush_logs()
        runtime.unlock("broadcast")


async def run_unpost(bot, chat_id: int) -> None:
    try:
        batch = await db.run(db.last_broadcast_batch)
        if not batch:
            await bot.send_message(chat_id, "❌ ပြန်ဖျက်စရာ ကြေညာချက် မရှိပါဘူး။")
            return
        rows = await db.run(db.broadcast_batch_rows, batch)
        done: List[Tuple[int, int]] = []

        async def delete_one(pair) -> None:
            uid, mid = pair
            try:
                await sender.call(bot.delete_message, chat_id=uid, message_id=mid)
            except Forbidden:
                pass   # bot ကို ပိတ်ထားသူ — ဖျက်လို့ မရတော့လို့ စာရင်းထဲက ဖယ်မည်
            except BadRequest as exc:
                if "not found" not in str(exc).lower():
                    raise
            done.append((uid, mid))

        progress = await bot.send_message(chat_id, f"🗑 ကြေညာချက် ပြန်ဖျက်နေပါတယ်… (message {len(rows):,} ခု)")
        result = await sender.run_mass(rows, delete_one)
        await db.run(db.delete_broadcast_rows, batch, done)
        try:
            await progress.delete()
        except TelegramError:
            pass
        lines = [("ဖျက်ပြီး / မရှိတော့", f"{len(done):,}"), ("မဖျက်ရသေး", f"{len(rows) - len(done):,}")]
        footer = ("မဖျက်ရသေးတာတွေကို /unpost နဲ့ ထပ်ကြိုးစားနိုင်ပါတယ်။ ပို့ပြီး ၄၈ နာရီကျော်ရင် "
                  "Telegram က ဖျက်ခွင့် မပေးပါ။") if len(rows) > len(done) else ""
        await reports.send_table(bot, chat_id, "🗑 ကြေညာချက် ပြန်ဖျက်ခြင်း", lines, footer=footer,
                                 reasons=result.reasons)
    finally:
        runtime.unlock("unpost")

"""/post (ကြေညာချက် ပို့ခြင်း) နဲ့ /unpost (ပြန်ဖျက်ခြင်း)။"""
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from telegram import (InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions,
                      MessageEntity, ReplyParameters)
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


@dataclass
class AlbumPlan:
    """Album (ပုံ/video အများ) ပို့ရန်။ chunks ရှိရင် caption ရှည်လို့ ပုံတွေအရင်၊ စာကို နောက်မှ ခွဲပို့သည်။"""
    message_ids: List[int]
    media: List[Tuple[str, str, bool]]          # (kind, file_id, has_spoiler)
    chunks: Optional[list] = None


def _album_media(message) -> Optional[Tuple[str, str, bool]]:
    spoiler = bool(getattr(message, "has_media_spoiler", False))
    if message.photo:
        return "photo", message.photo[-1].file_id, spoiler
    if message.video:
        return "video", message.video.file_id, spoiler
    if message.document:
        return "document", message.document.file_id, False
    if message.audio:
        return "audio", message.audio.file_id, False
    return None


def build_album_plan(messages: Sequence) -> AlbumPlan:
    """Album message များ (message_id အစဉ်) ကနေ ပို့မယ့် plan။"""
    ids = [m.message_id for m in messages]
    captioned = [m for m in messages if m.caption]
    long_caption = next((m for m in captioned if utf16_len(m.caption) > 1024), None)
    if long_caption is None:
        return AlbumPlan(ids, [])
    media = [_album_media(m) for m in messages]
    if any(item is None for item in media):
        raise ValueError("Caption ရှည်တဲ့ album ကို ခွဲပို့တာက ပုံ/video/ဖိုင် album အတွက်ပဲ ရပါတယ်။")
    chunks = formatted_chunks(long_caption.caption, long_caption.caption_entities or ())
    return AlbumPlan(ids, media, chunks)


def _input_media(kind: str, file_id: str, spoiler: bool):
    if kind == "photo":
        return InputMediaPhoto(file_id, has_spoiler=spoiler)
    if kind == "video":
        return InputMediaVideo(file_id, has_spoiler=spoiler)
    if kind == "document":
        return InputMediaDocument(file_id)
    return InputMediaAudio(file_id)


async def deliver_album(bot, uid: int, source_chat: int, plan: AlbumPlan) -> List[int]:
    """Album ကို album ပုံစံအတိုင်း ပို့သည်။ Album က Telegram အတွက် message အများလို ဖြစ်လို့ ပို့နှုန်း slot များများ ယူသည်။"""
    for _ in range(max(0, len(plan.message_ids) - 1)):
        await sender.LIMITER.wait()
    if not plan.chunks:
        result = await sender.call(bot.copy_messages, chat_id=uid, from_chat_id=source_chat,
                                   message_ids=plan.message_ids)
        return [m.message_id for m in result]
    sent: List[int] = []
    try:
        msgs = await sender.call(bot.send_media_group, chat_id=uid,
                                 media=[_input_media(*item) for item in plan.media])
        sent.extend(m.message_id for m in msgs)
        for text, entities in plan.chunks:
            msg = await sender.call(
                bot.send_message, chat_id=uid, text=text, entities=entities, parse_mode=None,
                reply_parameters=ReplyParameters(message_id=sent[0], allow_sending_without_reply=True),
                link_preview_options=NO_PREVIEW,
            )
            sent.append(msg.message_id)
        return sent
    except Exception as exc:
        if sent:
            raise PartialDelivery(exc, sent) from exc
        raise


async def deliver_post(bot, uid: int, source_chat: int, source_message: int, photo_plan) -> List[int]:
    """User တစ်ယောက်ဆီ ကြေညာချက် ပို့ပြီး message id များကို ပြန်ပေးသည်။"""
    if isinstance(photo_plan, AlbumPlan):
        return await deliver_album(bot, uid, source_chat, photo_plan)
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


# Database ထဲ သိမ်းမရတဲ့ broadcast log တွေကို ဒီ folder ထဲ ဖိုင်အဖြစ် သိမ်းသည် (/unpost က ပြန်ဖတ်သည်)
RECOVERY_DIR = config.BASE_DIR


@dataclass
class Recovery:
    path: Path
    batch: datetime
    rows: List[Tuple[int, int]]


def write_recovery(batch: datetime, rows) -> Path:
    path = Path(RECOVERY_DIR) / f"broadcast_recovery_{batch:%Y%m%d_%H%M%S_%f}.json"
    pairs = [[int(uid), int(mid)] for uid, mid, *_ in rows]
    path.write_text(json.dumps({"batch": batch.isoformat(sep=" "), "rows": pairs}), encoding="utf-8")
    return path


def load_recoveries() -> List[Recovery]:
    found = []
    for path in sorted(Path(RECOVERY_DIR).glob("broadcast_recovery_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(Recovery(path, datetime.fromisoformat(data["batch"]),
                                  [(int(u), int(m)) for u, m in data["rows"]]))
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Unreadable recovery file %s", path.name)
    return found


async def run_broadcast(bot, source_chat: int, source_message: int, photo_plan, users: List[int]) -> None:
    batch = datetime.now(timezone.utc).replace(tzinfo=None)
    logs: List[Tuple[int, int, datetime]] = []
    flush_at = {"size": 500}

    async def flush_logs() -> bool:
        """Log တွေကို DB ထဲ သိမ်းသည်။ မရရင် ဖယ်မပစ်ဘဲ နောက်တစ်ခါ ထပ်ကြိုးစားမည်။"""
        if not logs:
            return True
        rows = logs[:]
        try:
            await db.run(db.insert_broadcast_logs, rows)
        except Exception:
            logger.exception("Could not save %d broadcast log rows; keeping them", len(rows))
            flush_at["size"] = len(logs) + 2000   # DB ကို ခဏနားပေးပြီးမှ ထပ်ကြိုးစားမည်
            return False
        del logs[:len(rows)]
        flush_at["size"] = 500
        return True

    async def save_remaining() -> str:
        """အဆုံးမှာ ကျန်နေတဲ့ log တွေ — ၃ ခါ ထပ်ကြိုးစားပြီး မရရင် ဖိုင်ထဲ သိမ်းသည်။"""
        for delay in (0, 3, 10):
            if delay:
                await asyncio.sleep(delay)
            if await flush_logs():
                return ""
        count = len(logs)
        try:
            path = write_recovery(batch, logs)
            logs.clear()
            logger.error("Saved %d broadcast log rows to %s", count, path.name)
            return (f"⚠️ Message {count:,} ခုရဲ့ မှတ်တမ်းကို database ထဲ မသိမ်းနိုင်လို့ ဖိုင်ထဲ သိမ်းထားပါတယ်။ "
                    "/unpost က ဒီဖိုင်ကိုပါ ဖတ်ပြီး ဖျက်ပေးပါမယ် (bot ပြန်စတက်ရင်တော့ ဖိုင်ပျောက်ပါမယ်)။")
        except OSError:
            logger.exception("Could not write broadcast recovery file")
            return f"⚠️ Message {count:,} ခုရဲ့ မှတ်တမ်း သိမ်းမရလို့ /unpost က အဲ့ဒါတွေကို မဖျက်နိုင်ပါ။"

    async def send_one(uid: int) -> None:
        try:
            ids = await deliver_post(bot, uid, source_chat, source_message, photo_plan)
        except PartialDelivery as exc:
            logs.extend((uid, mid, batch) for mid in exc.sent_ids)
            raise exc.original
        logs.extend((uid, mid, batch) for mid in ids)
        if len(logs) >= flush_at["size"]:
            await flush_logs()

    note = ""
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
        note = await save_remaining()
        try:
            await progress.delete()
        except TelegramError:
            pass
        if result.stopped:
            title = "⏸ ကြေညာချက် ရပ်လိုက်ပါတယ်"
            footer = ("User အားလုံးကို ထိတဲ့ ပြဿနာကြောင့် ရပ်လိုက်တာပါ။ ပြဿနာ ပြေလည်ရင် ပို့ပြီးသူတွေကို "
                      "/unpost နဲ့ ဖျက်ပြီး /post ကို ပြန်လုပ်နိုင်ပါတယ်။")
        else:
            title, footer = "📢 ကြေညာချက် ပို့ပြီးပါပြီ", "ပြန်ဖျက်ချင်ရင် /unpost ကို သုံးပါ (၄၈ နာရီအတွင်း)။"
        await reports.send_mass_report(bot, title, result, footer=" ".join(x for x in (footer, note) if x))
    finally:
        if logs and not note:
            await save_remaining()
        runtime.unlock("broadcast")


async def run_unpost(bot, chat_id: int) -> None:
    try:
        db_batch = await db.run(db.last_broadcast_batch)
        recoveries = load_recoveries()
        candidates = [b for b in [db_batch] + [r.batch for r in recoveries] if b]
        if not candidates:
            await bot.send_message(chat_id, "❌ ပြန်ဖျက်စရာ ကြေညာချက် မရှိပါဘူး။")
            return
        batch = max(candidates)
        rows = list(await db.run(db.broadcast_batch_rows, batch)) if db_batch == batch else []
        mine = [r for r in recoveries if r.batch == batch]
        for recovery in mine:
            rows += recovery.rows
        rows = list(dict.fromkeys((int(u), int(m)) for u, m in rows))
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
        finished = set(done)
        for recovery in mine:
            left = [pair for pair in recovery.rows if pair not in finished]
            try:
                if left:
                    recovery.path.write_text(json.dumps({"batch": batch.isoformat(sep=" "),
                                                         "rows": [list(x) for x in left]}), encoding="utf-8")
                else:
                    recovery.path.unlink()
            except OSError:
                logger.exception("Could not update recovery file %s", recovery.path.name)
        try:
            await progress.delete()
        except TelegramError:
            pass
        lines = [("ဖျက်ပြီး / မရှိတော့", f"{len(done):,}"), ("မဖျက်ရသေး", f"{len(rows) - len(done):,}")]
        footer = ("မဖျက်ရသေးတာတွေကို /unpost နဲ့ ထပ်ကြိုးစားနိုင်ပါတယ်။ ပို့ပြီး ၄၈ နာရီကျော်ရင် "
                  "Telegram က ဖျက်ခွင့် မပေးပါ။") if len(rows) > len(done) else ""
        if result.stopped:
            footer = f"⏸ ရပ်လိုက်ပါတယ် — {result.stop_reason}။ " + footer
        await reports.send_table(bot, chat_id, "🗑 ကြေညာချက် ပြန်ဖျက်ခြင်း", lines, footer=footer,
                                 reasons=result.reasons)
    finally:
        runtime.unlock("unpost")

"""ဟောစာတမ်းတစ်ခုကို user တစ်ယောက်ဆီ ပို့ခြင်း။

Rich message ဖွင့်ထားရင်: ကဒ်ပုံ + ဟောစာတမ်း + "📖 ဆက်ဖတ်ရန်" ကို message တစ်စောင်တည်းနဲ့ ပို့သည်။
Rich message ပို့မရရင် (သို့) ပိတ်ထားရင်: ကဒ်ပုံကို အရင်ပို့ပြီး Telegram လက်ခံကြောင်း သေချာမှ
စာကို ပို့သည် (စာက ပုံထက် အရင် မရောက်တော့ပါ)။

reply_markup ကို function (ဥပမာ buttons.main_keyboard) အဖြစ် ပေးရင် premium emoji ပိတ်လိုက်ရတဲ့အခါ
ခလုတ်ကို ပုံမှန် emoji နဲ့ ပြန်ဆောက်ပြီး ထပ်ပို့နိုင်သည်။
"""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from telegram import InputFile, InputMediaPhoto, LinkPreviewOptions, Message, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import TelegramError, TimedOut

from . import config, runtime, sender
from .richtext import Doc, render_plain, render_rich

logger = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@dataclass(frozen=True)
class Photo:
    key: str      # media cache key ("card:12", "daily", ...)
    path: Path    # ဖိုင်လမ်းကြောင်း


def card_photo(card_id) -> Photo:
    return Photo(f"card:{card_id}", config.CARDS_DIR / f"{card_id}.jpg")


def _markup(reply_markup):
    return reply_markup() if callable(reply_markup) else reply_markup


async def send_with_markup(func, *, reply_markup=None, limiter=None, **kwargs):
    """ခလုတ်ပါတဲ့ စာတစ်စောင် ပို့/ပြင်သည် (func = Bot ရဲ့ method)။
    Premium emoji ကို Telegram မလက်ခံရင် ပိတ်ပြီး ပုံမှန် emoji ခလုတ်နဲ့ ပြန်ပို့သည်။
    limiter = user အများကြီးဆီ ပို့တဲ့အခါ sender.LIMITER ကို ပေးပါ။"""
    try:
        return await sender.call(func, reply_markup=_markup(reply_markup), limiter=limiter, **kwargs)
    except sender.PremiumEmojiError:
        await disable_premium(getattr(func, "__self__", None))
        fallback = _markup(reply_markup) if callable(reply_markup) else None
        return await sender.call(func, reply_markup=fallback, limiter=limiter, **kwargs)


def photo_input(photo: Photo, attach: bool = False):
    """Cache ထဲက file_id (မရှိရင် ဖိုင်ကို upload လုပ်ဖို့ InputFile)။"""
    file_id = runtime.media_cache.get(photo.key)
    if file_id:
        return file_id
    if photo.path.exists():
        return InputFile(photo.path.read_bytes(), filename=photo.path.name, attach=attach)
    return None


async def _send_photos(bot, chat_id: int, photos: Sequence[Photo], caption: Optional[str]) -> Optional[int]:
    """ပုံ(များ) ကို ပို့ပြီး ပထမပုံရဲ့ message_id ကို ပြန်ပေးသည်။ file_id ကို cache လုပ်သည်။"""
    attach = len(photos) > 1   # media group ဆိုရင် attach:// နဲ့ upload ရသည်
    usable = [(p, photo_input(p, attach)) for p in photos]
    usable = [(p, data) for p, data in usable if data is not None]
    if not usable:
        return None
    caption = (caption or "")[: config.CAPTION_LIMIT] or None
    try:
        if len(usable) == 1:
            photo, data = usable[0]
            msg = await sender.call(bot.send_photo, chat_id=chat_id, photo=data, caption=caption,
                                    parse_mode=ParseMode.HTML, write_timeout=60, read_timeout=60, limiter=None)
            await runtime.remember_media(photo.key, msg.photo[-1].file_id)
            return msg.message_id
        media = [InputMediaPhoto(data, caption=caption if i == 0 else None, parse_mode=ParseMode.HTML)
                 for i, (_, data) in enumerate(usable)]
        msgs = await sender.call(bot.send_media_group, chat_id=chat_id, media=media,
                                 write_timeout=60, read_timeout=60, limiter=None)
        for (photo, _), msg in zip(usable, msgs):
            if msg.photo:
                await runtime.remember_media(photo.key, msg.photo[-1].file_id)
        return msgs[0].message_id if msgs else None
    except TimedOut:
        # ပုံ ရောက်/မရောက် မသေချာ — ပုံရောက်ဖို့ အချိန်ပေးပြီးမှ စာကို ဆက်ပို့မည်
        logger.warning("Photo upload timed out for chat %s; waiting before text", chat_id)
        await asyncio.sleep(5)
        return None
    except TelegramError as exc:
        logger.warning("Photo send failed for chat %s: %s", chat_id, exc)
        return None


async def send_plain_text(bot, chat_id: int, doc: Doc, reply_markup=None,
                          reply_to: Optional[int] = None) -> List[Message]:
    """ပုံမှန် message (HTML) များနဲ့ ပို့သည် — ခလုတ်ကို နောက်ဆုံး message မှာ တပ်သည်။"""
    messages = render_plain(doc, config.TEXT_LIMIT)
    sent = []
    for index, text in enumerate(messages):
        last = index == len(messages) - 1
        reply = (ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
                 if index == 0 and reply_to else None)
        sent.append(await send_with_markup(
            bot.send_message, chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
            reply_markup=reply_markup if last else None, reply_parameters=reply,
            link_preview_options=NO_PREVIEW, write_timeout=60, read_timeout=60,
        ))
    return sent


async def _send_rich(bot, chat_id: int, doc: Doc, file_ids: Sequence[str], reply_markup) -> Message:
    api_kwargs = {"chat_id": chat_id, "rich_message": render_rich(doc, file_ids)}
    markup = _markup(reply_markup)
    if markup is not None:
        api_kwargs["reply_markup"] = markup
    return await sender.call(bot.do_api_request, "sendRichMessage", api_kwargs=api_kwargs,
                             return_type=Message, read_timeout=60, write_timeout=60, limiter=None)


async def deliver(bot, chat_id: int, doc: Doc, *, photos: Sequence[Photo] = (),
                  photo_caption: Optional[str] = None, reply_markup=None) -> bool:
    """ဟောစာတမ်း ပို့ခြင်း။ စာ ရောက်သွားရင် True (credit နှုတ်ဖို့ အခြေခံ)။"""
    photos_done = False
    if runtime.rich_enabled_for(chat_id):
        file_ids = [runtime.media_cache.get(p.key) for p in photos]
        if photos and not all(file_ids):
            # ပုံကို တစ်ခါမှ upload မလုပ်ရသေးရင် ပုံကို အရင်ပို့ (file_id ရလာမည်)
            await _send_photos(bot, chat_id, photos, photo_caption)
            photos_done, file_ids = True, []
        try:
            try:
                await _send_rich(bot, chat_id, doc, [f for f in file_ids if f], reply_markup)
            except sender.PremiumEmojiError:
                await disable_premium(bot)
                await _send_rich(bot, chat_id, doc, [f for f in file_ids if f],
                                 reply_markup if callable(reply_markup) else None)
            return True
        except TelegramError as exc:
            logger.warning("Rich message failed for chat %s: %s", chat_id, exc)
            await runtime.notify_admin_once(
                bot, "rich_failed",
                "⚠️ <b>Rich message ပို့လို့ မရပါ</b>\nပုံမှန် message နဲ့ အစားထိုး ပို့လိုက်ပါတယ်။\n"
                f"အကြောင်းရင်း – <code>{_escape(str(exc)[:300])}</code>\n\n"
                "ဆက်မဖြစ်စေချင်ရင် <code>/rich off</code> လို့ ရိုက်ပါ။",
            )
        except Exception:
            logger.exception("Rich message crashed for chat %s", chat_id)
    try:
        reply_to = None
        if photos and not photos_done:
            reply_to = await _send_photos(bot, chat_id, photos, photo_caption)
        await send_plain_text(bot, chat_id, doc, reply_markup, reply_to)
        return True
    except Exception:
        logger.exception("Delivery failed for chat %s", chat_id)
        return False


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def disable_premium(bot) -> None:
    if runtime.settings.get("premium_emoji") == "off":
        return
    await runtime.save_setting("premium_emoji", "off")
    if bot is not None:
        await runtime.notify_admin_once(
            bot, "premium_off",
            "⚠️ <b>Premium emoji ကို Telegram က လက်မခံလို့ ယာယီ ပိတ်လိုက်ပါတယ်</b>\n"
            "ခလုတ်တွေမှာ ပုံမှန် emoji ပြန်ပေါ်ပါမယ်။ Owner account ရဲ့ Premium ကို စစ်ပြီး "
            "<code>/premium on</code> နဲ့ ပြန်ဖွင့်နိုင်ပါတယ်။",
        )


async def ensure_file_id(bot, photo: Photo) -> Optional[str]:
    """ပုံကို admin chat ထဲ အသံမမြည်ဘဲ upload လုပ်ပြီး ချက်ချင်း ဖျက်ကာ file_id ကို မှတ်သည်။"""
    cached = runtime.media_cache.get(photo.key)
    if cached or not photo.path.exists():
        return cached
    try:
        msg = await sender.call(bot.send_photo, chat_id=config.ADMIN_ID, photo=photo_input(photo),
                                disable_notification=True, write_timeout=60, read_timeout=60, limiter=None)
        await runtime.remember_media(photo.key, msg.photo[-1].file_id)
        try:
            await msg.delete()
        except TelegramError:
            pass
        return runtime.media_cache.get(photo.key)
    except Exception:
        logger.exception("Could not upload %s", photo.path.name)
        return None

"""User feature များ (Tarot ၃ ကဒ်၊ ကံကြမ္မာလမ်းညွှန်၊ RS၊ ဖူးစာဖက်၊ ပင်ကိုယ်စရိုက်၊ မွေးနေ့၊
Daily/Weekly ကဒ်၊ Review၊ အကြံပြုစာ၊ Share)။
"""
import html
import logging
import random
import re
import urllib.parse
from datetime import date, datetime

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import buttons, catalog, config, content, db, delivery, runtime, texts
from .delivery import Photo, card_photo
from .richtext import Doc

logger = logging.getLogger(__name__)

# --- Conversation states (မူလ bot အတိုင်း) -------------------------------------------------
(WAIT_BDAY, WAIT_CAT, WAIT_SUB, WAIT_NUMS, WAIT_FEEDBACK, WAIT_REVIEW_TEXT, WAIT_SUGGESTION_TEXT,
 WAIT_PERMANENT_BDAY, WAIT_ZODIAC_PERS, WAIT_GENDER_PERS, SELECT_GUIDE_TYPE, GET_GUIDE_NUM,
 SELECT_OUTCOME_SUB) = range(13)
(SELECT_RELATION_TYPE, CHOOSE_LOVE_METHOD, TAROT_LOVE_INPUT,
 ZODIAC_FIRST, ZODIAC_SECOND, ZODIAC_CONFIRM) = range(20, 26)
LOVE_FUTURE_NUM = 30
END = ConversationHandler.END

CARD_IDS = [str(i) for i in range(1, 79)]
TEXT_INPUT = filters.TEXT & ~filters.COMMAND


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except TelegramError:
        pass


async def say(context, chat_id: int, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    return await delivery.send_with_markup(
        context.bot.send_message, chat_id=chat_id, text=text, parse_mode=parse_mode,
        reply_markup=reply_markup, link_preview_options=delivery.NO_PREVIEW,
    )


async def edit(query, text: str, reply_markup=None):
    """ခလုတ်နှိပ်ခဲ့တဲ့ message ကို စာသားအသစ်နဲ့ ပြင်သည်။"""
    message = query.message
    if message is None:
        return None
    bot = query.get_bot()
    try:
        return await delivery.send_with_markup(
            bot.edit_message_text, chat_id=message.chat_id, message_id=message.message_id, text=text,
            reply_markup=reply_markup, parse_mode=ParseMode.HTML, link_preview_options=delivery.NO_PREVIEW,
        )
    except TelegramError as exc:
        if "not modified" in str(exc).lower():
            return None
        raise


async def delete_quietly(message) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except TelegramError:
        pass


# --- အခြေခံ စစ်ဆေးချက်များ -----------------------------------------------------------------
async def mark_user_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User တစ်ယောက်ယောက် bot ကို သုံးတိုင်း active ပြန်ဖြစ်စေသည် (cache ရှိရင် DB မသွား)။"""
    user = update.effective_user
    if not user or user.id == config.ADMIN_ID or user.id in runtime.active_users:
        return
    try:
        await db.run(db.ensure_user_active, user.id)
        runtime.active_users.add(user.id)
    except Exception:
        logger.exception("mark_user_active failed for %s", user.id)


async def is_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == config.ADMIN_ID:
        return False
    if "is_banned" not in context.user_data:
        try:
            context.user_data["is_banned"] = await db.run(db.is_banned, user_id)
        except Exception:
            logger.exception("Ban check failed")
            context.user_data["is_banned"] = False
    if not context.user_data["is_banned"]:
        return False
    if update.callback_query:
        await update.callback_query.answer(texts.BANNED_ALERT, show_alert=True)
    elif update.effective_chat:
        await say(context, update.effective_chat.id, texts.BANNED)
    return True


async def has_credits(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return True
    try:
        row = await db.run(db.get_credits, user_id)
        if row is None:
            await db.run(db.create_user, user_id)
            runtime.active_users.add(user_id)
            return True
        return bool((row[0] or 0) > 0 or (row[1] or 0) > 0)
    except Exception:
        logger.exception("Credit check failed for %s", user_id)
        return False


async def charge(user_id: int) -> None:
    """ဟောစာတမ်း ရောက်ပြီးမှ credit ၁ ခု နှုတ်သည်။"""
    if user_id == config.ADMIN_ID:
        return
    try:
        await db.run(db.deduct_credit, user_id)
    except Exception:
        logger.exception("Credit deduction failed for %s", user_id)


def parse_number(text: str):
    text = (text or "").strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= 78 else None


def validate_birthday(text: str, mode: str):
    """(zodiac, None) သို့မဟုတ် (None, error စာ)"""
    errors = texts.BDAY_ERRORS[mode]
    parts = re.sub(r"[/,\-၊\.]", " ", text or "").split()
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None, errors["format"]
    day, month, year = (int(p) for p in parts)
    if year < 100 and day > 1900:
        day, year = year, day
    if month < 1 or month > 12:
        return None, errors["month"]
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = {1: 31, 2: 29 if leap else 28, 3: 31, 4: 30, 5: 31, 6: 30,
            7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[month]
    if day < 1 or day > days:
        if month == 2:
            return None, errors["feb_leap" if leap else "feb"].format(year=year)
        return None, errors["day"].format(month=month, max_days=days)
    try:
        born = date(year, month, day)
    except ValueError:
        return None, errors["invalid"]
    if year < 1926 or born > datetime.now(config.TZ).date():
        return None, errors["year"]
    return catalog.calculate_zodiac(day, month), None


async def ask_for_review(context, chat_id: int, text: str = texts.REVIEW_PROMPT) -> int:
    await say(context, chat_id, text, reply_markup=buttons.review_keyboard)
    return WAIT_FEEDBACK


# --- Start / Help / Share -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat_id = update.effective_user, update.effective_chat.id
    await typing(context, chat_id)
    await mark_user_active(update, context)
    caption = texts.WELCOME.format(name=esc(user.full_name))
    photo = Photo("welcome", config.CARDS_DIR / "welcome.jpg")
    source = delivery.photo_input(photo)
    if source is not None:
        try:
            msg = await delivery.send_with_markup(
                context.bot.send_photo, chat_id=chat_id, photo=source, caption=caption,
                parse_mode=ParseMode.HTML, reply_markup=buttons.main_keyboard,
            )
            await runtime.remember_media(photo.key, msg.photo[-1].file_id)
            return END
        except TelegramError:
            logger.exception("Welcome photo failed; sending text")
    await say(context, chat_id, caption, reply_markup=buttons.main_keyboard)
    return END


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    runtime.record_usage("help", update.effective_user.id)
    await typing(context, update.effective_chat.id)
    await say(context, update.effective_chat.id,
              texts.HELP.format(name=esc(update.effective_user.full_name)), reply_markup=buttons.main_keyboard)
    return END


async def share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    runtime.record_usage("share", update.effective_user.id)
    message = texts.SHARE_MESSAGE.format(bot=config.BOT_USERNAME)
    url = f"https://t.me/share/url?url=&text={urllib.parse.quote(message)}"
    await say(context, update.effective_chat.id, texts.SHARE_TEXT,
              reply_markup=lambda: buttons.share_keyboard(url))
    return END


# --- Cancel ----------------------------------------------------------------------------------
async def cancel_tarot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_quietly(query.message)
    await say(context, update.effective_chat.id, texts.CANCELLED, reply_markup=buttons.main_keyboard)
    return END


async def cancel_love(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await edit(query, texts.LOVE_CANCELLED)
    return END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await say(context, update.effective_chat.id, texts.CANCELLED, reply_markup=buttons.main_keyboard)
    return END


# --- မွေးနေ့မှတ်မည် ---------------------------------------------------------------------------
async def bday_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    runtime.record_usage("bday", update.effective_user.id)
    await say(context, update.effective_chat.id, texts.BDAY_MENU, reply_markup=buttons.bday_keyboard)
    return WAIT_PERMANENT_BDAY


async def bday_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "delete_bday_data":
        await db.run(db.forget_birthday, update.effective_user.id)
        await edit(query, texts.BDAY_DELETED)
        return END
    await edit(query, texts.BDAY_ASK_SAVE)
    return WAIT_PERMANENT_BDAY


async def bday_save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await typing(context, chat_id)
    text = update.message.text.strip()
    zodiac, error = validate_birthday(text, "save")
    if error:
        await say(context, chat_id, error)
        return WAIT_PERMANENT_BDAY
    context.user_data["zodiac"] = zodiac
    try:
        await db.run(db.save_birthday, update.effective_user.id, text, zodiac)
        await say(context, chat_id, texts.BDAY_SAVED.format(zodiac=zodiac), reply_markup=buttons.main_keyboard)
    except Exception:
        logger.exception("Saving birthday failed")
        await say(context, chat_id, texts.BDAY_SAVE_ERROR)
    return END


# --- Tarot မေးမည် (ကဒ် ၃ ကဒ်) ----------------------------------------------------------------
async def tarot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    if runtime.too_fast(user_id, config.COOLDOWN_SECONDS):
        return None
    runtime.record_usage("tarot", user_id)
    if not await has_credits(user_id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    await typing(context, chat_id)
    try:
        row = await db.run(db.get_birthday, user_id)
    except Exception:
        logger.exception("Birthday lookup failed")
        row = None
    if row and row[0] and row[1] and row[2] is True:
        context.user_data["birthday"], context.user_data["zodiac"] = row[0], row[1]
        return await show_categories(update, context)
    await say(context, chat_id, texts.TAROT_ASK_BDAY, reply_markup=buttons.cancel_keyboard)
    return WAIT_BDAY


async def tarot_birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await typing(context, chat_id)
    text = update.message.text.strip()
    zodiac, error = validate_birthday(text, "reading")
    if error:
        await say(context, chat_id, error)
        return WAIT_BDAY
    context.user_data["zodiac"], context.user_data["birthday"] = zodiac, text
    await say(context, chat_id, texts.BDAY_NOTED.format(zodiac=zodiac), reply_markup=buttons.main_keyboard)
    return await show_categories(update, context)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await edit(update.callback_query, texts.CATEGORY_ASK, buttons.category_keyboard)
    else:
        await say(context, update.effective_chat.id, texts.CATEGORY_ASK, reply_markup=buttons.category_keyboard)
    return WAIT_CAT


async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if runtime.too_fast(update.effective_user.id, 1):
        return None
    category = catalog.category_by_label(query.data)
    context.user_data["category"] = category.label
    await edit(query, texts.SUB_ASK.format(category=esc(category.label)), lambda: buttons.sub_keyboard(category.label))
    return WAIT_SUB


async def sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if runtime.too_fast(update.effective_user.id, 1):
        return None
    context.user_data["sub"] = query.data
    await edit(query, texts.NUMBERS_ASK.format(sub=esc(query.data)), buttons.cancel_keyboard)
    return WAIT_NUMS


def three_card_doc(cards, category, sub, zodiac: str) -> Doc:
    data = content.get()
    doc = Doc().title(texts.THREE_CARD_TITLE.format(sub=sub.label))
    for index, card_id in enumerate(cards):
        meaning = data.meaning(card_id, category.key, sub.key) or texts.CARD_NOT_READY
        doc.heading(f"📅 {texts.THREE_CARD_TIMEFRAMES[index]}")
        doc.label(f"🃏 ကဒ် {index + 1}: {data.card_name(card_id)}")
        doc.para(f"📝 {meaning}")
        if index == 2 and category.key != "general":
            advice = data.zodiac_advice(card_id, zodiac, category.key, sub.key)
            if advice:
                doc.heading(texts.THREE_CARD_ZODIAC.format(zodiac=zodiac))
                doc.para(advice)
    return doc


async def three_card_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat_id = update.effective_user, update.effective_chat.id
    await typing(context, chat_id)
    numbers = [int(n) for n in re.findall(r"\d+", update.message.text.replace("၊", ","))]
    if len(numbers) != 3 or any(n < 1 or n > 78 for n in numbers):
        await say(context, chat_id, texts.NUMBERS_INVALID)
        return WAIT_NUMS
    if len(set(numbers)) != 3:
        await say(context, chat_id, texts.NUMBERS_DUPLICATE)
        return WAIT_NUMS
    if not await has_credits(user.id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    loading = await say(context, chat_id, texts.READING_WAIT)

    category = catalog.category_by_label(context.user_data.get("category", ""))
    sub = catalog.sub_by_label(category, context.user_data.get("sub", "")) or category.subs[0]
    zodiac = context.user_data.get("zodiac")
    if not zodiac:
        try:
            zodiac = await db.run(db.get_zodiac, user.id)
        except Exception:
            zodiac = None
    zodiac = (zodiac or "Aries").strip().capitalize()

    deck = CARD_IDS[:]
    random.shuffle(deck)
    cards = [deck[n - 1] for n in numbers]
    await typing(context, chat_id)
    ok = await delivery.deliver(
        context.bot, chat_id, three_card_doc(cards, category, sub, zodiac),
        photos=[card_photo(c) for c in cards],
        photo_caption=texts.THREE_CARD_CAPTION.format(name=esc(user.first_name)),
    )
    await delete_quietly(loading)
    if not ok:
        await say(context, chat_id, texts.READING_FAILED, reply_markup=buttons.main_keyboard)
        return END
    await charge(user.id)
    return await ask_for_review(context, chat_id, texts.REVIEW_PROMPT_SHORT)


# --- ကံကြမ္မာ လမ်းညွှန် ---------------------------------------------------------------------
async def guide_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    runtime.record_usage("guide", user_id)
    if not await has_credits(user_id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    if await is_banned(update, context):
        return END
    await typing(context, chat_id)
    await say(context, chat_id, texts.GUIDE_ASK_TYPE, reply_markup=buttons.guide_type_keyboard)
    return SELECT_GUIDE_TYPE


async def guide_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "guide_outcome":
        await edit(query, texts.GUIDE_ASK_OUTCOME, buttons.guide_outcome_keyboard)
        return SELECT_OUTCOME_SUB
    context.user_data["guide_sub_mode"] = "advice"
    await edit(query, texts.GUIDE_ASK_NUMBER_ADVICE, buttons.cancel_keyboard)
    return GET_GUIDE_NUM


async def guide_outcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["guide_sub_mode"] = query.data.replace("guide_sub_", "")
    await edit(query, texts.GUIDE_ASK_NUMBER_OUTCOME, buttons.cancel_keyboard)
    return GET_GUIDE_NUM


async def guide_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat_id = update.effective_user, update.effective_chat.id
    await typing(context, chat_id)
    if parse_number(update.message.text) is None:
        await say(context, chat_id, texts.ONE_NUMBER_INVALID)
        return GET_GUIDE_NUM
    if not await has_credits(user.id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    wait = await say(context, chat_id, texts.GUIDE_WAIT)
    card_id = random.choice(CARD_IDS)
    data = content.get()
    entry = data.guide_entry(card_id)
    mode = context.user_data.get("guide_sub_mode", "advice")
    if mode == "advice":
        title, result, note = texts.GUIDE_TITLE_ADVICE, entry.get("advice") or texts.GUIDE_NOT_READY, texts.GUIDE_NOTE_ADVICE
    else:
        outcomes = entry.get("outcomes") if isinstance(entry.get("outcomes"), dict) else {}
        title, result, note = texts.GUIDE_TITLE_OUTCOME, outcomes.get(mode) or texts.GUIDE_OUTCOME_NOT_READY, texts.GUIDE_NOTE_OUTCOME
    card_name = data.card_name(card_id)
    doc = (Doc().title(title).label(f"🃏 {card_name}").para(f"📝 {result}")
           .para(texts.GUIDE_ENERGY.format(energy=entry.get("energy", "??"))).note(note))
    ok = await delivery.deliver(
        context.bot, chat_id, doc, photos=[card_photo(card_id)],
        photo_caption=texts.GUIDE_CAPTION.format(name=esc(user.full_name), card=f"<b>{esc(card_name)}</b>"),
        reply_markup=buttons.main_keyboard,
    )
    await delete_quietly(wait)
    if not ok:
        await say(context, chat_id, texts.READING_FAILED, reply_markup=buttons.main_keyboard)
        return END
    await charge(user.id)
    return await ask_for_review(context, chat_id)


# --- ချစ်သူနဲ့ကိုက်ညီမှု (RS) ------------------------------------------------------------------
async def love_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    runtime.record_usage("rs", update.effective_user.id)
    await say(context, update.effective_chat.id, texts.LOVE_ASK_RELATION, reply_markup=buttons.relation_keyboard)
    return SELECT_RELATION_TYPE


async def love_relation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["relation_type"] = "near" if query.data == "rel_near" else "ldr"
    await edit(query, texts.LOVE_ASK_METHOD, buttons.love_method_keyboard)
    return CHOOSE_LOVE_METHOD


async def love_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "love_method_tarot":
        await edit(query, texts.LOVE_ASK_NUMBER, lambda: buttons.cancel_keyboard(buttons.CB_LOVE_CANCEL))
        return TAROT_LOVE_INPUT
    await edit(query, texts.LOVE_ASK_FIRST_SIGN, lambda: buttons.zodiac_keyboard("zlove_", buttons.CB_LOVE_CANCEL))
    return ZODIAC_FIRST


async def love_first_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["first_zodiac"] = query.data.replace("zlove_", "", 1)
    await edit(query, texts.LOVE_ASK_SECOND_SIGN,
               lambda: buttons.zodiac_keyboard("zlove_", buttons.CB_LOVE_CANCEL, with_back=True))
    return ZODIAC_SECOND


async def love_second_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "zlove_back_first":
        await edit(query, texts.LOVE_ASK_FIRST_SIGN, lambda: buttons.zodiac_keyboard("zlove_", buttons.CB_LOVE_CANCEL))
        return ZODIAC_FIRST
    second = query.data.replace("zlove_", "", 1)
    first = context.user_data.get("first_zodiac", "")
    context.user_data["second_zodiac"] = second
    await edit(query, texts.LOVE_CONFIRM_PAIR.format(first=esc(first), second=esc(second)),
               buttons.love_confirm_keyboard)
    return ZODIAC_CONFIRM


async def love_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    if query.data == "zlove_back_first":
        await edit(query, texts.LOVE_ASK_FIRST_SIGN_AGAIN, lambda: buttons.zodiac_keyboard("zlove_", buttons.CB_LOVE_CANCEL))
        return ZODIAC_FIRST
    if not await has_credits(user_id):
        await edit(query, texts.NO_CREDIT)
        return END
    first = context.user_data.get("first_zodiac", "")
    second = context.user_data.get("second_zodiac", "")
    relation = context.user_data.get("relation_type", "near")
    result = content.get().zodiac_pair(relation, first, second)
    if not result:
        await edit(query, texts.LOVE_PAIR_NOT_READY)
        return END
    await edit(query, texts.LOVE_CALCULATING)
    await typing(context, chat_id)
    doc = (Doc().title(texts.LOVE_ZODIAC_TITLE.format(pair=f"{first} ❤️ {second}",
                                                       relation="LDR" if relation == "ldr" else "Near"))
           .para(f"✨ {result.get('percent', '')}")
           .heading(texts.LOVE_ZODIAC_STRENGTH).para(result.get("strength", ""))
           .heading(texts.LOVE_ZODIAC_WEAKNESS).para(result.get("weakness", ""))
           .heading(texts.LOVE_ZODIAC_ADVICE).para(result.get("advice", ""))
           .para(texts.LOVE_ZODIAC_CLOSING))
    ok = await delivery.deliver(context.bot, chat_id, doc)
    await delete_quietly(query.message)
    if not ok:
        await say(context, chat_id, texts.READING_FAILED, reply_markup=buttons.main_keyboard)
        return END
    await charge(user_id)
    return await ask_for_review(context, chat_id, texts.REVIEW_PROMPT_SHORT)


def _love_card_photo(card: dict) -> Photo:
    card_id = str(card.get("id", ""))
    image = str(card.get("image_url", ""))
    if image == f"cards/{card_id}.jpg" or not image:
        return card_photo(card_id)
    return Photo(f"love:{image}", config.BASE_DIR / image)


async def love_tarot_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat_id = update.effective_user, update.effective_chat.id
    if parse_number(update.message.text) is None:
        await say(context, chat_id, texts.LOVE_NUMBER_INVALID)
        return TAROT_LOVE_INPUT
    if not await has_credits(user.id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    await typing(context, chat_id)
    relation = context.user_data.get("relation_type", "near")
    cards = content.get().tarot_love_cards(relation)
    if not cards:
        await say(context, chat_id, texts.LOVE_NO_DATA)
        return END
    wait = await say(context, chat_id, texts.LOVE_TAROT_WAIT)
    card = random.choice(cards)
    relation_text = "LDR" if relation == "ldr" else "Near"
    doc = (Doc().label(texts.LOVE_TAROT_CARD.format(card=card.get("name", "")))
           .heading(texts.LOVE_TAROT_HEADING).para(card.get("love_meaning", "")))
    ok = await delivery.deliver(
        context.bot, chat_id, doc, photos=[_love_card_photo(card)],
        photo_caption=texts.LOVE_TAROT_CAPTION.format(name=esc(user.full_name), relation=relation_text),
    )
    await delete_quietly(wait)
    if not ok:
        await say(context, chat_id, texts.READING_FAILED, reply_markup=buttons.main_keyboard)
        return END
    await charge(user.id)
    return await ask_for_review(context, chat_id, texts.REVIEW_PROMPT_SHORT)


# --- ဖူးစာဖက်ကြည့်ရန် --------------------------------------------------------------------------
async def love_future_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    runtime.record_usage("love_future", user_id)
    if not await has_credits(user_id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    await typing(context, chat_id)
    await say(context, chat_id, texts.LOVE_FUTURE_ASK, reply_markup=buttons.cancel_keyboard)
    return LOVE_FUTURE_NUM


def love_future_doc(card_id: str) -> Doc:
    data = content.get()
    entry = data.love_future_entry(card_id)
    doc = Doc(shown_sections=texts.LOVE_FUTURE_SHOWN_SECTIONS)
    doc.title(texts.LOVE_FUTURE_TITLE.format(card=data.card_name(card_id)))
    for key, heading in texts.LOVE_FUTURE_SECTIONS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            doc.heading(heading).para(value)
    return doc


async def love_future_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat_id = update.effective_user, update.effective_chat.id
    await typing(context, chat_id)
    if parse_number(update.message.text) is None:
        await say(context, chat_id, texts.ONE_NUMBER_INVALID)
        return LOVE_FUTURE_NUM
    if not await has_credits(user.id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    wait = await say(context, chat_id, texts.LOVE_FUTURE_WAIT)
    card_id = random.choice(CARD_IDS)
    doc = love_future_doc(card_id)
    ok = await delivery.deliver(
        context.bot, chat_id, doc, photos=[card_photo(card_id)],
        photo_caption=esc(texts.LOVE_FUTURE_TITLE.format(card=content.get().card_name(card_id))),
    )
    await delete_quietly(wait)
    if not ok:
        await say(context, chat_id, texts.READING_FAILED, reply_markup=buttons.main_keyboard)
        return END
    await charge(user.id)
    return await ask_for_review(context, chat_id)


# --- ရာသီခွင်ပင်ကိုယ်စရိုက် -----------------------------------------------------------------------
async def personality_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    runtime.record_usage("personality", user_id)
    if not await has_credits(user_id):
        await say(context, chat_id, texts.NO_CREDIT)
        return END
    if await is_banned(update, context):
        return END
    await typing(context, chat_id)
    await say(context, chat_id, texts.PERSONALITY_ASK_SIGN,
              reply_markup=lambda: buttons.zodiac_keyboard("pers_", buttons.CB_CANCEL))
    return WAIT_ZODIAC_PERS


async def personality_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sign = query.data.replace("pers_", "", 1)
    context.user_data["selected_pers_zodiac"] = sign
    await edit(query, texts.PERSONALITY_ASK_GENDER.format(sign=esc(sign)), buttons.gender_keyboard)
    return WAIT_GENDER_PERS


async def personality_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    gender = "male" if query.data == "pers_gender_male" else "female"
    sign = context.user_data.get("selected_pers_zodiac", "")
    data = content.get()
    if not data.personality:
        await edit(query, texts.PERSONALITY_NO_DATA)
        return END
    entry = data.personality_entry(sign, gender)
    if not entry:
        await edit(query, texts.PERSONALITY_NOT_READY)
        return END
    male = gender == "male"
    doc = (Doc().title(texts.PERSONALITY_TITLE.format(sign=sign, gender="အမျိုးသား" if male else "အမျိုးသမီး"))
           .para(entry.get("personality", ""))
           .heading(texts.PERSONALITY_SPEECH).para(entry.get("speech", ""))
           .heading(texts.PERSONALITY_LOVE).para(entry.get("relationship", ""))
           .heading(texts.PERSONALITY_SINGLE.format(life="လူပျိုဘဝ" if male else "အပျိုဘဝ"))
           .para(entry.get("single", ""))
           .para(texts.PERSONALITY_CLOSING))
    await typing(context, chat_id)
    ok = await delivery.deliver(context.bot, chat_id, doc)
    if not ok:
        await edit(query, texts.READING_FAILED)
        return END
    await delete_quietly(query.message)
    await charge(user_id)
    return await ask_for_review(context, chat_id)


# --- Review / အကြံပြုစာ ---------------------------------------------------------------------------
async def review_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_quietly(query.message)
    chat_id = update.effective_chat.id
    if query.data == buttons.CB_REVIEW_YES:
        msg = await say(context, chat_id, texts.REVIEW_ASK_TEXT, reply_markup=buttons.writing_keyboard)
        context.user_data["instr_msg_id"] = msg.message_id
        return WAIT_REVIEW_TEXT
    await say(context, chat_id, texts.REVIEW_SKIPPED, reply_markup=buttons.main_keyboard)
    return END


async def suggestion_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update, context):
        return END
    runtime.record_usage("suggest", update.effective_user.id)
    await typing(context, update.effective_chat.id)
    msg = await say(context, update.effective_chat.id, texts.SUGGEST_ASK, reply_markup=buttons.writing_keyboard)
    context.user_data["suggest_instr_id"] = msg.message_id
    return WAIT_SUGGESTION_TEXT


async def _forward_to_admins(context, update: Update, kind: str, body: str) -> None:
    user = update.effective_user
    link = f'<a href="tg://user?id={user.id}">{esc(user.full_name)}</a>'
    username = f"@{esc(user.username)}" if user.username else "username မရှိ"
    if kind == "review":
        header, label = "⭐ <b>သုံးသပ်ချက်အသစ်</b>", "💬 <b>သုံးသပ်ချက်</b>"
    else:
        header, label = "💡 <b>အကြံပြုချက်အသစ်</b>", "🛠 <b>အကြံပြုချက်</b>"
    report = (f"{header}\n\n👤 <b>ပေးပို့သူ</b> – {link} ({username})\n"
              f"🆔 <b>ID</b> – <code>{user.id}</code>\n{label} – <i>{esc(body)}</i>")
    for target in {config.ADMIN_ID, config.STAFF_ID}:
        try:
            await context.bot.send_message(chat_id=target, text=report, parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.warning("Could not forward %s to %s", kind, target)


async def _delete_later(context, chat_id: int, message_id: int) -> None:
    async def remove(job_context):
        try:
            await job_context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass
    if context.job_queue:
        context.job_queue.run_once(remove, config.SHORT_NOTICE_LIFETIME_SECONDS)


async def _written_text(update, context, instruction_key: str, kind: str, cancelled: str, thanks: str):
    chat_id = update.effective_chat.id
    text = update.message.text
    if buttons.is_button_text(text, "stop_writing"):
        await say(context, chat_id, cancelled, reply_markup=buttons.main_keyboard)
        return END
    instruction = context.user_data.pop(instruction_key, None)
    if instruction:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=instruction)
        except TelegramError:
            pass
    await _forward_to_admins(context, update, kind, text)
    confirm = await say(context, chat_id, thanks, reply_markup=buttons.main_keyboard)
    await _delete_later(context, chat_id, confirm.message_id)
    return END


async def review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _written_text(update, context, "instr_msg_id", "review",
                               texts.REVIEW_CANCELLED, texts.REVIEW_THANKS)


async def suggestion_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _written_text(update, context, "suggest_instr_id", "suggestion",
                               texts.SUGGEST_CANCELLED, texts.SUGGEST_THANKS)


# --- Daily / Weekly ကဒ် (မနက်စာပေါ်က ခလုတ်) --------------------------------------------------------
def _first_pick(context, message, kind: str) -> bool:
    picked = context.user_data.setdefault("picked_cards", set())
    key = (message.chat_id, message.message_id, kind)
    if key in picked:
        return False
    if len(picked) > 50:
        picked.clear()
    picked.add(key)
    return True


async def _after_pick(context, message, used: str) -> None:
    """ကဒ်ရွေးပြီးနောက် မနက်စာပေါ်က ခလုတ်ကို ဖြုတ်/စာကို ဖျက်သည်။"""
    remaining = buttons.callbacks_in(message.reply_markup) - {used}
    chat_id, message_id = message.chat_id, message.message_id
    try:
        if remaining:
            daily, weekly = buttons.CB_DAILY in remaining, buttons.CB_WEEKLY in remaining
            await delivery.send_with_markup(
                context.bot.edit_message_reply_markup, chat_id=chat_id, message_id=message_id,
                reply_markup=lambda: buttons.morning_keyboard(daily=daily, weekly=weekly),
            )
            if used == buttons.CB_DAILY:
                await db.run(db.drop_message_actions, chat_id, message_id, "strip_daily")
            else:
                # တစ်ပတ်ကဒ် ရွေးပြီးပြီ — ၁၂ နာရီပြည့်ရင် daily ခလုတ်နဲ့အတူ စာကို ဖျက်မည်
                await db.run(db.change_message_action, chat_id, message_id, "strip_daily", "delete")
        else:
            await delete_quietly(message)
            await db.run(db.drop_message_actions, chat_id, message_id)
    except Exception:
        logger.exception("Could not update morning message %s/%s", chat_id, message_id)


def daily_doc(card_id: str) -> Doc:
    data = content.get()
    text = data.daily_text(card_id) or texts.DAILY_NOT_READY
    return Doc().title(texts.DAILY_TITLE.format(card=data.card_name(card_id))).para(f"📝 {text}")


def weekly_doc(card_id: str) -> Doc:
    data = content.get()
    entry = data.weekly_entry(card_id)
    doc = Doc(shown_sections=texts.WEEKLY_SHOWN_SECTIONS)
    doc.title(texts.WEEKLY_TITLE.format(card=data.card_name(card_id)))
    if not entry:
        return doc.para(texts.WEEKLY_NOT_READY)
    for key, heading in texts.WEEKLY_SECTIONS:
        value = entry.get(key)
        if isinstance(value, dict):
            parts = [f"{label} - {value[k]}" for k, label in texts.WEEKLY_PLAN_PARTS
                     if isinstance(value.get(k), str) and value[k].strip()]
            if parts:
                doc.heading(heading).para("\n\n".join(parts))
        elif isinstance(value, list):
            items = [v for v in value if isinstance(v, str) and v.strip()]
            if items:
                doc.heading(heading).bullets(items)
        elif isinstance(value, str) and value.strip():
            doc.heading(heading).para(value)
    return doc


async def _pick_card(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    query = update.callback_query
    message = query.message
    if await is_banned(update, context):
        return
    if message is None or not _first_pick(context, message, kind):
        await query.answer(texts.ALREADY_PICKED)
        return
    await query.answer()
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    runtime.record_usage("daily_card" if kind == buttons.CB_DAILY else "weekly_card", user_id)
    await typing(context, chat_id)
    card_id = random.choice(CARD_IDS)
    name = content.get().card_name(card_id)
    if kind == buttons.CB_DAILY:
        doc, caption = daily_doc(card_id), texts.DAILY_TITLE.format(card=f"<b>{esc(name)}</b>")
    else:
        doc, caption = weekly_doc(card_id), texts.WEEKLY_TITLE.format(card=f"<b>{esc(name)}</b>")
    ok = await delivery.deliver(context.bot, chat_id, doc, photos=[card_photo(card_id)],
                                photo_caption=caption, reply_markup=buttons.main_keyboard)
    if not ok:
        context.user_data.get("picked_cards", set()).discard((message.chat_id, message.message_id, kind))
        await say(context, chat_id, texts.DAILY_FAILED, reply_markup=buttons.main_keyboard)
        return
    await _after_pick(context, message, kind)


async def daily_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pick_card(update, context, buttons.CB_DAILY)


async def weekly_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pick_card(update, context, buttons.CB_WEEKLY)


async def stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot ပြန်စတင်ပြီးနောက် ခလုတ်အဟောင်းကို နှိပ်ရင် spinner မလည်နေစေရန်။"""
    try:
        await update.callback_query.answer("ဒီခလုတ်က သက်တမ်းကုန်သွားပါပြီ။ Menu ကနေ ပြန်စပေးပါရှင်။ ✨")
    except TelegramError:
        pass


# --- Handler စာရင်း ---------------------------------------------------------------------------
def _menu(key: str, callback):
    return MessageHandler(filters.Regex(buttons.menu_pattern(key)), callback)


def _choice_pattern(labels) -> str:
    return "^(" + "|".join(re.escape(label) for label in labels) + ")$"


def conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            _menu("tarot", tarot_start),
            _menu("rs", love_start),
            _menu("love_future", love_future_start),
            _menu("guide", guide_start),
            _menu("personality", personality_start),
            _menu("bday", bday_menu),
            _menu("suggest", suggestion_start),
            _menu("help", show_help),
            _menu("share", share),
        ],
        states={
            WAIT_BDAY: [MessageHandler(TEXT_INPUT, tarot_birthday)],
            WAIT_CAT: [CallbackQueryHandler(category_chosen, pattern=_choice_pattern(catalog.CATEGORY_LABELS))],
            WAIT_SUB: [CallbackQueryHandler(sub_chosen, pattern=_choice_pattern(catalog.SUB_LABELS))],
            WAIT_NUMS: [MessageHandler(TEXT_INPUT, three_card_numbers)],
            WAIT_FEEDBACK: [CallbackQueryHandler(review_choice, pattern="^(give_review|skip_review)$")],
            WAIT_REVIEW_TEXT: [MessageHandler(TEXT_INPUT, review_text)],
            WAIT_SUGGESTION_TEXT: [MessageHandler(TEXT_INPUT, suggestion_text)],
            WAIT_PERMANENT_BDAY: [
                CallbackQueryHandler(bday_choice, pattern="^(save_bday_start|delete_bday_data)$"),
                MessageHandler(TEXT_INPUT, bday_save_text),
            ],
            WAIT_ZODIAC_PERS: [CallbackQueryHandler(personality_sign, pattern=r"^pers_(?!gender_)")],
            WAIT_GENDER_PERS: [CallbackQueryHandler(personality_gender, pattern="^pers_gender_(male|female)$")],
            SELECT_GUIDE_TYPE: [CallbackQueryHandler(guide_type, pattern="^guide_(outcome|advice)$")],
            SELECT_OUTCOME_SUB: [CallbackQueryHandler(guide_outcome, pattern="^guide_sub_(exam|interview|trade|other)$")],
            GET_GUIDE_NUM: [MessageHandler(TEXT_INPUT, guide_number)],
            SELECT_RELATION_TYPE: [CallbackQueryHandler(love_relation, pattern="^rel_(near|ldr)$")],
            CHOOSE_LOVE_METHOD: [CallbackQueryHandler(love_method, pattern="^love_method_(tarot|zodiac)$")],
            TAROT_LOVE_INPUT: [MessageHandler(TEXT_INPUT, love_tarot_number)],
            ZODIAC_FIRST: [CallbackQueryHandler(love_first_sign, pattern=_choice_pattern(
                [f"zlove_{s}" for s in catalog.ZODIAC_SIGNS]))],
            ZODIAC_SECOND: [CallbackQueryHandler(love_second_sign, pattern=_choice_pattern(
                ["zlove_back_first"] + [f"zlove_{s}" for s in catalog.ZODIAC_SIGNS]))],
            ZODIAC_CONFIRM: [CallbackQueryHandler(love_confirm, pattern="^(zlove_confirm_yes|zlove_back_first)$")],
            LOVE_FUTURE_NUM: [MessageHandler(TEXT_INPUT, love_future_number)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_tarot, pattern=f"^{buttons.CB_CANCEL}$"),
            CallbackQueryHandler(cancel_love, pattern=f"^{buttons.CB_LOVE_CANCEL}$"),
            CommandHandler("cancel", cancel_command),
        ],
        allow_reentry=True,
        per_message=False,
    )


def card_handlers():
    return [
        CallbackQueryHandler(daily_card, pattern=f"^{buttons.CB_DAILY}$"),
        CallbackQueryHandler(weekly_card, pattern=f"^{buttons.CB_WEEKLY}$"),
    ]

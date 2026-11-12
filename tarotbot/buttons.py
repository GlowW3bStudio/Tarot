"""ခလုတ်အားလုံး — အရောင်နဲ့ premium emoji ကို ဒီနေရာတစ်ခုတည်းမှာ စီမံသည်။

* အရောင်: အပြာ (primary)၊ cancel ခလုတ်များ အနီ (danger)
* Premium emoji: ခလုတ်ရှေ့မှာ icon အဖြစ် ပေါ်သည်။ Telegram app အသစ်တွေမှာပဲ မြင်ရပါတယ်။
  Premium ပိတ်ထားရင် (သို့) ID မမှန်ရင် ခလုတ်စာသားထဲမှာ ပုံမှန် emoji ကို ပြန်ထည့်ပေးသည်။
"""
import re
from dataclasses import dataclass
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import catalog

PRIMARY = "primary"   # အပြာ
DANGER = "danger"     # အနီ


@dataclass(frozen=True)
class Spec:
    emoji: str
    text: str
    emoji_id: Optional[str]
    style: str = PRIMARY


# ခလုတ်နံပါတ်တွေက admin နဲ့ တိုင်ပင်ထားတဲ့ စာရင်းအတိုင်းပါ (၁–၃၃)
SPECS = {
    # Menu (အောက်ခြေ keyboard)
    "tarot": Spec("🔮", "Tarot မေးမည်", "4956525562483967357"),                    # 1
    "rs": Spec("❤️", "ချစ်သူနဲ့ကိုက်ညီမှု(RS)", "5355215058605450179"),            # 2
    "love_future": Spec("❤️", "ဖူးစာဖက်ကြည့်ရန်", "5350705145376156068"),           # 3
    "guide": Spec("🎯", "ကံကြမ္မာ လမ်းညွှန်", "5294325496228620537"),               # 4
    "personality": Spec("♈", "ရာသီခွင်ပင်ကိုယ်စရိုက်", "5303543015736883901"),      # 5
    "bday": Spec("🎂", "မွေးနေ့မှတ်မည်", "5328273248448686763"),                     # 6
    "help": Spec("❓", "အသုံးပြုနည်း", "5436113877181941026"),                       # 7
    "suggest": Spec("✍️", "အကြံပြုစာပို့ရန်", "5362079447136610876"),                # 8
    "share": Spec("📤", "Share မည်", "5458485283790004353"),                         # 9
    "stop_writing": Spec("🚫", "မပေးတော့ပါ", "5210952531676504517", DANGER),        # 10
    # စာအောက်က ခလုတ်များ
    "daily_pick": Spec("🔮", "ယနေ့အတွက် ကဒ်ရွေးမည်", "4956525562483967357"),        # 11
    "weekly_pick": Spec("🗓", "ဒီတစ်ပတ်အတွက် ကဒ်ရွေးမည်", "4956525562483967357"),    # 12
    "cancel": Spec("❌", "မမေးတော့ပါ", "5210952531676504517", DANGER),              # 13
    "review_yes": Spec("📝", "Review ပေးမည်", "4956475826762679249"),                # 14
    "review_no": Spec("✖️", "မပေးဘူး", "5210952531676504517", DANGER),              # 15
    "guide_outcome": Spec("✅", "လုပ်ပြီးသားကိစ္စရလဒ်", "5330356071364046086"),      # 16
    "guide_advice": Spec("💡", "ရှေ့ဆက်လမ်းညွှန်ချက်", "5276269595450162557"),       # 17
    "guide_exam": Spec("🎓", "စာမေးပွဲရလဒ်", "5325790508243495261"),                 # 18
    "guide_interview": Spec("💼", "အင်တာဗျူးရလဒ်", "5463401243227472308"),           # 19
    "guide_trade": Spec("💰", "အရောင်းအဝယ်ရလဒ်", "5395732581780040886"),             # 20
    "guide_other": Spec("✨", "အခြား", "5416081784641168838"),                       # 21
    "rel_near": Spec("👫", "အနေနီးအချစ်", "5388743385794228775"),                    # 22
    "rel_ldr": Spec("🌍", "LDRs", "5357296215073497424"),                            # 23
    "method_tarot": Spec("🃏", "Tarot ကဒ်ဖြင့်", "5343639460317767455"),             # 24
    "method_zodiac": Spec("♈", "ရာသီခွင်ဖြင့်", "5343990891721796356"),             # 25
    "zodiac_back": Spec("🔙", "ရာသီခွင် ပြန်ရွေးမည်", "6132138165294601245"),        # 26
    "zodiac_yes": Spec("✅", "မှန်ကန်ပါသည်", "5206607081334906820"),                 # 27
    "zodiac_reselect": Spec("🔙", "ပြန်ရွေးမည်", "6132138165294601245"),             # 28
    "male": Spec("👦🏻", "ကျား", None),                                             # 29 (ပုံမှန် emoji)
    "female": Spec("👧🏻", "မ", None),                                              # 30 (ပုံမှန် emoji)
    "bday_save": Spec("✅", "မွေးနေ့အသစ် မှတ်သားမည်", "5389062952835886404"),      # 31
    "bday_delete": Spec("🗑", "မှတ်ထားတာ ပြန်ဖျက်မည်", "5445267414562389170"),      # 32
    "share_send": Spec("🚀", "သူငယ်ချင်းများထံ Share မည်", "5458485283790004353"),  # 33
}

# Callback data (ခလုတ်အဟောင်းတွေ ဆက်အလုပ်လုပ်အောင် မူလအတိုင်း ထားထားသည်)
CB_CANCEL = "cancel_tarot"
CB_LOVE_CANCEL = "love_cancel"
CB_REVIEW_YES = "give_review"
CB_REVIEW_NO = "skip_review"
CB_DAILY = "daily_random_card"
CB_WEEKLY = "weekly_random_card"

_state = {"premium": True, "bad_ids": set()}


def set_premium(enabled: bool) -> None:
    _state["premium"] = bool(enabled)


def premium_enabled() -> bool:
    return _state["premium"]


def mark_bad_emoji_ids(ids) -> None:
    _state["bad_ids"].update(ids)


def all_emoji_ids() -> list:
    return sorted({spec.emoji_id for spec in SPECS.values() if spec.emoji_id})


def _label(spec: Spec):
    """(ခလုတ်စာသား, icon id) ကို ပြန်ပေးသည်။"""
    if spec.emoji_id and _state["premium"] and spec.emoji_id not in _state["bad_ids"]:
        return spec.text, spec.emoji_id
    return f"{spec.emoji} {spec.text}", None


def ibtn(key: str, callback_data: Optional[str] = None, url: Optional[str] = None) -> InlineKeyboardButton:
    spec = SPECS[key]
    text, icon = _label(spec)
    return InlineKeyboardButton(
        text, callback_data=callback_data, url=url, style=spec.style, icon_custom_emoji_id=icon
    )


def plain_ibtn(text: str, callback_data: str) -> InlineKeyboardButton:
    """Emoji မပါတဲ့ ခလုတ် (ကဏ္ဍ၊ ရာသီ စသည်) — အပြာရောင်။"""
    return InlineKeyboardButton(text, callback_data=callback_data, style=PRIMARY)


def kbtn(key: str) -> KeyboardButton:
    spec = SPECS[key]
    text, icon = _label(spec)
    return KeyboardButton(text, style=spec.style, icon_custom_emoji_id=icon)


def menu_pattern(key: str) -> str:
    """Menu ခလုတ်စာသားကို emoji ပါ/မပါ နှစ်မျိုးလုံး လက်ခံမယ့် regex။"""
    spec = SPECS[key]
    emoji = "".join(re.escape(ch) + ("?" if ch == "️" else "") for ch in spec.emoji)
    return rf"^\s*(?:{emoji}\s*)?{re.escape(spec.text)}\s*$"


def is_button_text(text: Optional[str], key: str) -> bool:
    return bool(text) and re.match(menu_pattern(key), text) is not None


# --- Keyboards ------------------------------------------------------------------
def main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [kbtn("tarot"), kbtn("rs")],
        [kbtn("love_future")],
        [kbtn("guide"), kbtn("personality")],
        [kbtn("bday"), kbtn("help")],
        [kbtn("suggest"), kbtn("share")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def writing_keyboard() -> ReplyKeyboardMarkup:
    """Review / အကြံပြုစာ ရေးနေစဉ် ပြမယ့် 'မပေးတော့ပါ' keyboard။"""
    return ReplyKeyboardMarkup([[kbtn("stop_writing")]], resize_keyboard=True, is_persistent=True)


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ibtn("review_yes", CB_REVIEW_YES), ibtn("review_no", CB_REVIEW_NO)]])


def cancel_keyboard(callback_data: str = CB_CANCEL) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ibtn("cancel", callback_data)]])


def zodiac_keyboard(prefix: str, cancel_cb: str, with_back: bool = False) -> InlineKeyboardMarkup:
    signs = catalog.ZODIAC_SIGNS
    rows = [[plain_ibtn(sign, f"{prefix}{sign}") for sign in signs[i:i + 3]] for i in range(0, 12, 3)]
    if with_back:
        rows.append([ibtn("zodiac_back", "zlove_back_first")])
    rows.append([ibtn("cancel", cancel_cb)])
    return InlineKeyboardMarkup(rows)


def category_keyboard() -> InlineKeyboardMarkup:
    labels = [c.label for c in catalog.CATEGORIES]
    rows = [[plain_ibtn(label, label) for label in labels[i:i + 2]] for i in range(0, len(labels), 2)]
    rows.append([ibtn("cancel", CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


def sub_keyboard(category_label: str) -> InlineKeyboardMarkup:
    category = catalog.category_by_label(category_label)
    subs = [s.label for s in category.subs]
    rows = [[plain_ibtn(label, label) for label in subs[i:i + 2]] for i in range(0, len(subs), 2)]
    rows.append([ibtn("cancel", CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


def guide_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("guide_outcome", "guide_outcome"), ibtn("guide_advice", "guide_advice")],
        [ibtn("cancel", CB_CANCEL)],
    ])


def guide_outcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("guide_exam", "guide_sub_exam"), ibtn("guide_interview", "guide_sub_interview")],
        [ibtn("guide_trade", "guide_sub_trade"), ibtn("guide_other", "guide_sub_other")],
        [ibtn("cancel", CB_CANCEL)],
    ])


def relation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("rel_near", "rel_near"), ibtn("rel_ldr", "rel_ldr")],
        [ibtn("cancel", CB_LOVE_CANCEL)],
    ])


def love_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("method_tarot", "love_method_tarot"), ibtn("method_zodiac", "love_method_zodiac")],
        [ibtn("cancel", CB_LOVE_CANCEL)],
    ])


def love_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("zodiac_yes", "zlove_confirm_yes")],
        [ibtn("zodiac_reselect", "zlove_back_first")],
        [ibtn("cancel", CB_LOVE_CANCEL)],
    ])


def gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("male", "pers_gender_male"), ibtn("female", "pers_gender_female")],
        [ibtn("cancel", CB_CANCEL)],
    ])


def bday_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ibtn("bday_save", "save_bday_start")],
        [ibtn("bday_delete", "delete_bday_data")],
    ])


def share_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ibtn("share_send", url=url)]])


def morning_keyboard(daily: bool = True, weekly: bool = False) -> Optional[InlineKeyboardMarkup]:
    rows = []
    if daily:
        rows.append([ibtn("daily_pick", CB_DAILY)])
    if weekly:
        rows.append([ibtn("weekly_pick", CB_WEEKLY)])
    return InlineKeyboardMarkup(rows) if rows else None


def callbacks_in(markup) -> set:
    """Message တစ်ခုပေါ်က ခလုတ်တွေရဲ့ callback data များ။"""
    found = set()
    if markup and getattr(markup, "inline_keyboard", None):
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    found.add(button.callback_data)
    return found

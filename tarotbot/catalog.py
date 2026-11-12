"""Tarot ကဏ္ဍ၊ ကဏ္ဍခွဲ၊ ရာသီခွင် စာရင်းများ (မူလ bot အတိုင်း)။"""
from dataclasses import dataclass
from typing import Optional

ZODIAC_SIGNS = (
    "Aries", "Taurus", "Gemini",
    "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius",
    "Capricorn", "Aquarius", "Pisces",
)


@dataclass(frozen=True)
class Sub:
    label: str   # ခလုတ်ပေါ်က စာသား (callback data လည်း ဒီအတိုင်း)
    key: str     # tarot_data.json ထဲက key


@dataclass(frozen=True)
class Category:
    label: str
    key: str
    subs: tuple


CATEGORIES = (
    Category("အချစ်ရေး", "love", (
        Sub("Crush", "crush"), Sub("ချစ်သူ/စုံတွဲ", "couple"),
        Sub("ရည်းစားဟောင်း", "ex"), Sub("လူပျို/လူလွတ်", "single"),
    )),
    Category("စီးပွားရေး", "work", (
        Sub("ဝန်ထမ်း", "employee"), Sub("လုပ်ငန်းရှင်", "entrepreneur"), Sub("ငွေကြေး", "finance"),
    )),
    Category("ပညာရေး", "education", (
        Sub("စာမေးပွဲ", "exam"), Sub("ဘာသာရပ်သစ်", "new_subject"), Sub("ပညာသင်ဆု", "scholarship"),
    )),
    Category("ကျန်းမာရေး", "health", (
        Sub("ကိုယ်ကာယ", "physical"), Sub("စိတ်ကျန်းမာရေး", "mental"), Sub("ခွဲစိတ်ကုသမှု", "surgery"),
    )),
    Category("ခရီးသွားလာရေး", "travel", (
        Sub("အပန်းဖြေခရီး", "vacation"), Sub("အလုပ်ကိစ္စ", "business"), Sub("ဗီဇာ/စာရွက်စာတမ်း", "visa"),
    )),
    Category("လူမှုရေး", "social", (
        Sub("မိတ်ဆွေသစ်", "new_friend"), Sub("ဆက်ဆံရေး", "harmony"), Sub("သတိထားရန်", "betrayal"),
    )),
    Category("မိသားစုရေး", "family", (
        Sub("မိသားစုအရေး", "harmony"), Sub("အိုးအိမ်", "home"), Sub("ရင်သွေးရတနာ", "baby"),
    )),
    Category("ယေဘုယျ", "general", (Sub("ကံကြမ္မာ", "general"),)),
)
GENERAL = CATEGORIES[-1]
CATEGORY_LABELS = tuple(c.label for c in CATEGORIES)
SUB_LABELS = tuple(dict.fromkeys(s.label for c in CATEGORIES for s in c.subs))


def category_by_label(label: str) -> Category:
    for category in CATEGORIES:
        if category.label == label:
            return category
    return GENERAL


def sub_by_label(category: Category, label: str) -> Optional[Sub]:
    for sub in category.subs:
        if sub.label == label:
            return sub
    return None


def calculate_zodiac(day: int, month: int) -> str:
    zodiac_map = (
        (21, 3, 19, 4, "Aries"), (20, 4, 20, 5, "Taurus"),
        (21, 5, 20, 6, "Gemini"), (21, 6, 22, 7, "Cancer"),
        (23, 7, 22, 8, "Leo"), (23, 8, 22, 9, "Virgo"),
        (23, 9, 22, 10, "Libra"), (23, 10, 21, 11, "Scorpio"),
        (22, 11, 21, 12, "Sagittarius"), (22, 12, 19, 1, "Capricorn"),
        (20, 1, 18, 2, "Aquarius"), (19, 2, 20, 3, "Pisces"),
    )
    for d1, m1, d2, m2, name in zodiac_map:
        if (month == m1 and day >= d1) or (month == m2 and day <= d2):
            return name
    return "Pisces"

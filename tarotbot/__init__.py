"""Tarot BayDin Telegram bot.

Module layout (ဖိုင်တစ်ခုချင်းစီရဲ့ တာဝန်):
    config.py          - Render environment settings နဲ့ ပုံသေတန်ဖိုးများ
    texts.py           - User တွေ မြင်ရမယ့် မြန်မာစာသားများ (စာပြင်ချင်ရင် ဒီဖိုင်)
    catalog.py         - Tarot ကဏ္ဍ / ကဏ္ဍခွဲ / ရာသီခွင် စာရင်း
    buttons.py         - ခလုတ်များ (အရောင်၊ premium emoji ID)
    richtext.py        - ဟောစာတမ်းကို rich message / ပုံမှန် message အဖြစ် ပုံစံချခြင်း
    db.py              - Supabase (PostgreSQL) နဲ့ ချိတ်ဆက်ခြင်း
    content.py         - JSON ဟောစာတမ်း data များ
    cardstore_build.py - tarot_data.json ကို SQLite (tarot_cards.db) အဖြစ် ပြောင်းခြင်း
    runtime.py         - Settings၊ ပုံ file_id၊ အသုံးပြုမှု စာရင်း
    sender.py          - User အများကြီးဆီ စာပို့စနစ် (broadcast / daily / weekly)
    delivery.py        - ဟောစာတမ်းတစ်ခုကို user တစ်ယောက်ဆီ ပို့ခြင်း
    reports.py         - Admin report / table များ
    broadcast.py       - /post နဲ့ /unpost
    handlers_user.py   - User feature များ
    handlers_admin.py  - Admin command များ
    jobs.py            - အချိန်ဇယားအလုပ်များ
    app.py             - အားလုံးကို ချိတ်ဆက်ပြီး bot စတင်ခြင်း
"""

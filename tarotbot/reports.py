"""Admin ဆီ ပို့မယ့် report / table များ။

Rich message ဖွင့်ထားရင် (admin / on) table ပုံစံနဲ့ ပို့ပြီး ပိတ်ထားရင် (သို့) ပို့မရရင်
ပုံမှန် စာသားနဲ့ ပို့သည်။
"""
import logging
from collections import Counter
from typing import Optional, Sequence, Tuple

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import TelegramError

from . import config, runtime, sender
from .richtext import esc, rich_table

logger = logging.getLogger(__name__)


def duration_text(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} နာရီ {minutes} မိနစ်"
    if minutes:
        return f"{minutes} မိနစ် {secs} စက္ကန့်"
    return f"{secs} စက္ကန့်"


def progress_text(title: str, done: int, total: int, result: Optional[sender.MassResult] = None) -> str:
    percent = (done * 100 // total) if total else 100
    text = f"{title} ပို့နေဆဲ… {done:,} / {total:,} ({percent}%)"
    if result is not None:
        text += f"\n✅ {result.ok:,}   🚫 {result.inactive:,}   ⚠️ {result.failed + result.uncertain:,}"
    return text


async def send_table(bot, chat_id: int, title: str, rows: Sequence[Tuple[str, str]], *,
                     footer: str = "", reasons: Optional[Counter] = None,
                     header: Optional[Sequence[str]] = None,
                     extra_tables: Sequence[Tuple[str, Sequence[Sequence[str]], Sequence[str]]] = ()) -> None:
    """(အချက်, တန်ဖိုး) row များကို table အဖြစ် ပို့သည်။"""
    reason_rows = [(reason, f"{count:,}") for reason, count in (reasons or Counter()).most_common(5)]
    if runtime.rich_enabled_for(chat_id):
        parts = [rich_table(rows, header=header, title=title)]
        for sub_title, sub_rows, sub_header in extra_tables:
            if sub_rows:
                parts.append(rich_table(sub_rows, header=sub_header, title=sub_title))
        if reason_rows:
            parts.append(rich_table(reason_rows, header=("အကြောင်းရင်း", "ဦးရေ"), title="⚠️ မရောက်ရတဲ့ အကြောင်းရင်း"))
        if footer:
            parts.append(f"<p><i>{esc(footer)}</i></p>")
        try:
            await sender.call(bot.do_api_request, "sendRichMessage",
                              api_kwargs={"chat_id": chat_id, "rich_message": {"html": "".join(parts)}},
                              return_type=Message, limiter=None)
            return
        except TelegramError as exc:
            logger.warning("Rich table failed, sending plain text: %s", exc)
    lines = [f"<b>{esc(title)}</b>", ""]
    if header:
        lines.append(" | ".join(f"<i>{esc(str(h))}</i>" for h in header))
    lines += [f"{esc(label)} – <b>{esc(str(value))}</b>" for label, value in rows]
    for sub_title, sub_rows, sub_header in extra_tables:
        if sub_rows:
            lines += ["", f"<b>{esc(sub_title)}</b>"]
            if sub_header:
                lines.append(" | ".join(f"<i>{esc(str(h))}</i>" for h in sub_header))
            lines += [" | ".join(esc(str(c)) for c in row) for row in sub_rows]
    if reason_rows:
        lines += ["", "<b>⚠️ မရောက်ရတဲ့ အကြောင်းရင်း</b>"]
        lines += [f"{esc(reason)} – {count}" for reason, count in reason_rows]
    if footer:
        lines += ["", f"<i>{esc(footer)}</i>"]
    text = "\n".join(lines)
    try:
        await bot.send_message(chat_id, text[:4000], parse_mode=ParseMode.HTML)
    except TelegramError:
        logger.exception("Could not send admin report")


async def send_mass_report(bot, title: str, result: sender.MassResult, footer: str = "", chat_id: int = None) -> None:
    rows = [
        ("ပို့ရမည့်သူ", f"{result.total:,}"),
        ("✅ ရောက်ရှိသူ", f"{result.ok:,}"),
        ("🚫 Bot ပိတ်ထား / account မရှိ (စာရင်းမှ ဖယ်ပြီး)", f"{result.inactive:,}"),
        ("⚠️ မရောက်သူ (အခြားအကြောင်း)", f"{result.failed + result.uncertain:,}"),
    ]
    if result.total > result.attempted:
        rows.append(("⏸ မပို့ရသေးသူ", f"{result.total - result.attempted:,}"))
    rows.append(("⏱ ကြာချိန်", duration_text(result.seconds)))
    await send_table(bot, chat_id or config.ADMIN_ID, title, rows, footer=footer, reasons=result.reasons)


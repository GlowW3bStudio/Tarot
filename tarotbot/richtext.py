"""ဟောစာတမ်းကို ပုံစံချခြင်း။

ဟောစာတမ်းတစ်ခုကို Doc (block စာရင်း) အဖြစ် တည်ဆောက်ပြီး
  * render_rich()   → Telegram rich message (HTML) — ပုံ + အစပိုင်း + "📖 ဆက်ဖတ်ရန်" (details)
  * render_plain()  → ပုံမှန် message များ (HTML) — အစပိုင်း + expandable quote၊ 4096 အောက် ခွဲထားသည်
နှစ်မျိုးလုံး ထုတ်ပေးနိုင်သည်။ ဒီဖိုင်ထဲမှာ Telegram နဲ့ မချိတ်ဘဲ စာသားကိုသာ စီမံသည်။
"""
import html
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import texts

TITLE, HEADING, LABEL, PARA, BULLETS, NOTE = "title", "heading", "label", "para", "bullets", "note"

PREVIEW_RATIO = 0.10        # အစပိုင်း ~၁၀%
MIN_TEXT_FOR_PREVIEW = 300  # ဒီထက်တိုတဲ့ ဟောစာတမ်းကို အကုန်ပြမည်
MIN_HIDDEN_RATIO = 0.08     # ဖုံးထားမယ့်အပိုင်း သိပ်တိုရင် အကုန်ပြမည်

_SENTENCE_END = re.compile(r"(?<=[။?!])\s*|\n+")
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Block:
    kind: str
    text: str = ""
    items: Tuple[str, ...] = ()

    def length(self) -> int:
        return len(self.text) + sum(len(item) for item in self.items)


@dataclass
class Doc:
    blocks: List[Block] = field(default_factory=list)
    # None = ~၁၀% ပြမည်။ ဂဏန်းဆိုရင် ခေါင်းစဉ် (heading) အဲ့ဒီအရေအတွက်အထိ ပြမည်။
    shown_sections: Optional[int] = None

    def add(self, kind: str, text: str = "", items: Sequence[str] = ()) -> "Doc":
        text = (text or "").strip()
        items = tuple(i.strip() for i in items if i and i.strip())
        if text or items:
            self.blocks.append(Block(kind, text, items))
        return self

    def title(self, text: str) -> "Doc":
        return self.add(TITLE, text)

    def heading(self, text: str) -> "Doc":
        return self.add(HEADING, text)

    def label(self, text: str) -> "Doc":
        return self.add(LABEL, text)

    def para(self, text: str) -> "Doc":
        return self.add(PARA, text)

    def bullets(self, items: Sequence[str]) -> "Doc":
        return self.add(BULLETS, items=items)

    def note(self, text: str) -> "Doc":
        return self.add(NOTE, text)


# --- ဖုံးထားမယ့်အပိုင်း ခွဲခြင်း ---------------------------------------------------------
def split_text(text: str, need: int) -> Tuple[str, str]:
    """စာပိုဒ်ကို စာကြောင်းဆုံး ("။") မှာ ခွဲသည်။ need = အစပိုင်းမှာ လိုသေးတဲ့ စာလုံးရေ။"""
    if need <= 0:
        return "", text
    if need >= len(text):
        return text, ""
    cuts = [m.end() for m in _SENTENCE_END.finditer(text) if 0 < m.end() < len(text)]
    after = [c for c in cuts if c >= need]
    before = [c for c in cuts if c < need]
    cut = None
    if after and after[0] - need <= 220:
        cut = after[0]
    elif before and before[-1] >= need * 0.5:
        cut = before[-1]
    elif after:
        cut = after[0]
    if cut is not None:
        return text[:cut].rstrip(), text[cut:].lstrip()
    # စာကြောင်းဆုံး မရှိရင် space မှာ ဖြတ်ပြီး "…" ထည့်မည်
    space = text.rfind(" ", 0, need + 40)
    if space <= 0:
        return text, ""
    return text[:space].rstrip() + " …", "… " + text[space:].lstrip()


def split_doc(doc: Doc) -> Tuple[List[Block], List[Block]]:
    """(ပြထားမယ့် blocks, "ဆက်ဖတ်ရန်" ထဲ ထည့်မယ့် blocks)"""
    blocks = list(doc.blocks)
    if doc.shown_sections is not None:
        shown, hidden, headings = [], [], 0
        for block in blocks:
            if block.kind == HEADING:
                headings += 1
            (shown if headings <= doc.shown_sections and not hidden else hidden).append(block)
        return shown, hidden

    body = [b for b in blocks if b.kind != TITLE]
    total = sum(b.length() for b in body)
    if total < MIN_TEXT_FOR_PREVIEW:
        return blocks, []
    target = total * PREVIEW_RATIO
    shown, hidden, used = [], [], 0
    for index, block in enumerate(blocks):
        if block.kind in (TITLE, HEADING, LABEL):
            shown.append(block)
            used += block.length() if block.kind != TITLE else 0
            continue
        if used + block.length() < target:
            shown.append(block)
            used += block.length()
            continue
        if block.kind in (PARA, NOTE):
            head, tail = split_text(block.text, int(target - used))
            if head:
                shown.append(Block(block.kind, head))
            if tail:
                hidden.append(Block(block.kind, tail))
        else:
            shown.append(block)
        hidden.extend(blocks[index + 1:])
        break
    # ခေါင်းစဉ်တစ်ခုတည်း အဆုံးမှာ ကျန်နေရင် ဖုံးထားတဲ့ဘက်ကို ရွှေ့မည်
    while hidden and shown and shown[-1].kind in (HEADING, LABEL):
        hidden.insert(0, shown.pop())
    hidden_len = sum(b.length() for b in hidden)
    if not hidden or hidden_len < total * MIN_HIDDEN_RATIO:
        return blocks, []
    return shown, hidden


# --- Rich message (sendRichMessage, html) ----------------------------------------------
def esc(text: str) -> str:
    return html.escape(text, quote=False)


def _rich_lines(text: str, wrap: str = "") -> str:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            body = esc(line)
            out.append(f"<p><{wrap}>{body}</{wrap}></p>" if wrap else f"<p>{body}</p>")
    return "".join(out)


def _rich_block(block: Block) -> str:
    if block.kind == TITLE:
        return f"<h3>{esc(block.text)}</h3>"
    if block.kind == HEADING:
        return f"<h4>{esc(block.text)}</h4>"
    if block.kind == LABEL:
        return _rich_lines(block.text, "b")
    if block.kind == NOTE:
        return _rich_lines(block.text, "i")
    if block.kind == BULLETS:
        return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in block.items) + "</ul>"
    return _rich_lines(block.text)


def render_rich(doc: Doc, photo_file_ids: Sequence[str] = ()) -> dict:
    """sendRichMessage ရဲ့ rich_message (InputRichMessage) ကို ပြန်ပေးသည်။"""
    shown, hidden = split_doc(doc)
    parts, media = [], []
    for index, file_id in enumerate(photo_file_ids):
        media_id = f"card{index + 1}"
        media.append({"id": media_id, "media": {"type": "photo", "media": file_id}})
    images = [f'<img src="tg://photo?id={m["id"]}"/>' for m in media]
    if len(images) == 1:
        parts.append(images[0])
    elif images:
        parts.append("<tg-collage>" + "".join(images) + "</tg-collage>")
    parts.extend(_rich_block(b) for b in shown)
    if hidden:
        inner = "".join(_rich_block(b) for b in hidden)
        parts.append(f"<details><summary>{esc(texts.READ_MORE)}</summary>{inner}</details>")
    payload = {"html": "".join(parts)}
    if media:
        payload["media"] = media
    return payload


def rich_table(rows: Sequence[Sequence[str]], header: Optional[Sequence[str]] = None, title: str = "") -> str:
    """Admin data များအတွက် table (rich message html)။"""
    out = [f"<h4>{esc(title)}</h4>"] if title else []
    out.append("<table bordered striped>")
    if header:
        out.append("<tr>" + "".join(f"<th>{esc(str(h))}</th>" for h in header) + "</tr>")
    for row in rows:
        cells = list(row)
        out.append("<tr>" + "".join(
            f'<td align="right">{esc(str(c))}</td>' if i == len(cells) - 1 and len(cells) > 1 else f"<td>{esc(str(c))}</td>"
            for i, c in enumerate(cells)
        ) + "</tr>")
    out.append("</table>")
    return "".join(out)


# --- ပုံမှန် message (parse_mode=HTML) -----------------------------------------------------
def visible_len(html_text: str) -> int:
    """Telegram က ရေတွက်တဲ့ စာလုံးရေ (tag မပါ၊ UTF-16)။"""
    plain = html.unescape(_TAG.sub("", html_text))
    return len(plain.encode("utf-16-le")) // 2


def _plain_block(block: Block) -> str:
    if block.kind in (TITLE, HEADING, LABEL):
        return f"<b>{esc(block.text)}</b>"
    if block.kind == NOTE:
        return f"<i>{esc(block.text)}</i>"
    if block.kind == BULLETS:
        return "\n".join(f"• {esc(item)}" for item in block.items)
    return esc(block.text)


def _units(blocks: Sequence[Block], max_len: int) -> List[str]:
    """Block တစ်ခုချင်းစီကို message ထဲ ထည့်လို့ရတဲ့ အပိုင်းငယ်များအဖြစ် ပြောင်းသည်။"""
    units = []
    for block in blocks:
        rendered = _plain_block(block)
        if visible_len(rendered) <= max_len:
            units.append(rendered)
            continue
        if block.kind == BULLETS:
            for item in block.items:
                units.extend(_units([Block(PARA, "• " + item)], max_len))
            continue
        remaining = block.text
        while remaining:
            head, remaining = split_text(remaining, max_len - 40)
            if not head:
                head, remaining = remaining[: max_len - 40], remaining[max_len - 40:]
            units.append(_plain_block(Block(block.kind, head)))
    return units


def render_plain(doc: Doc, limit: int = 3900) -> List[str]:
    """ပုံမှန် message များ (HTML) — message တစ်စောင်ချင်း limit အောက်။"""
    shown, hidden = split_doc(doc)
    unit_max = limit - 200
    shown_units = _units(shown, unit_max)
    hidden_units = _units(hidden, unit_max)
    sep = 2  # "\n\n"
    messages: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            messages.append("\n\n".join(current))
        current, current_len = [], 0

    for unit in shown_units:
        size = visible_len(unit)
        if current and current_len + sep + size > limit:
            flush()
        current.append(unit)
        current_len += (sep if current_len else 0) + size

    if hidden_units:
        header = f"<b>{esc(texts.READ_MORE)}</b> 👇"
        header_len = visible_len(header)
        first_len = visible_len(hidden_units[0])
        if current and current_len + sep + header_len + sep + first_len > limit:
            flush()
        current.append(header)
        current_len += (sep if current_len else 0) + header_len
        quote: List[str] = []
        quote_len = 0
        for unit in hidden_units:
            size = visible_len(unit)
            if quote and current_len + sep + quote_len + sep + size > limit:
                current.append("<blockquote expandable>" + "\n\n".join(quote) + "</blockquote>")
                flush()
                quote, quote_len = [], 0
            quote.append(unit)
            quote_len += (sep if quote_len else 0) + size
        if quote:
            current.append("<blockquote expandable>" + "\n\n".join(quote) + "</blockquote>")
    flush()
    return messages

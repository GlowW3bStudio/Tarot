"""tarot_data.json (55MB) ကို SQLite ဖိုင် (tarot_cards.db) အဖြစ် ပြောင်းသည်။

* JSON ကို ကဒ်တစ်ကဒ်ချင်း ဖတ်လို့ RAM အနည်းငယ်ပဲ သုံးသည်
* စာသားတွေကို zlib နဲ့ ချုံ့သိမ်းလို့ ဖိုင်အရွယ်အစား သေးသည်
* Bot က ဒီဖိုင်ကို သီးသန့် process နဲ့ run ပြီး JSON ပြင်ထားရင် အလိုအလျောက် ပြန်ဆောက်သည်

ကိုယ်တိုင် run ချင်ရင်:  python -m tarotbot.cardstore_build tarot_data.json tarot_cards.db
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import zlib

FORMAT_VERSION = "2"
_WS = re.compile(r"\s*")


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_TOP_KEY = re.compile(rb'\n  "((?:[^"\\\n]|\\.)*)": ')


def iter_cards_file(path: str):
    """ဖိုင်ကို memory-map လုပ်ပြီး ကဒ်တစ်ကဒ်ချင်း ဖတ်သည် (indent=2 ပုံစံ JSON အတွက်)။
    ပုံစံ မကိုက်ရင် ValueError ပစ်ပြီး ခေါ်တဲ့သူက ပုံမှန်နည်းနဲ့ ဖတ်မည်။"""
    import mmap
    with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if data[:1] != b"{":
            raise ValueError("not an indented JSON object")
        marks = [(m.start(), m.end(), m.group(1)) for m in _TOP_KEY.finditer(data)]
        if not marks or data[1:marks[0][0]].strip():
            raise ValueError("unexpected JSON layout")
        seen = set()
        for index, (_, value_start, raw_key) in enumerate(marks):
            value_end = marks[index + 1][0] if index + 1 < len(marks) else len(data)
            chunk = data[value_start:value_end].rstrip()
            if index + 1 < len(marks):
                if not chunk.endswith(b","):
                    raise ValueError("unexpected separator")
                chunk = chunk[:-1]
            else:
                if not chunk.endswith(b"}"):
                    raise ValueError("unexpected end")
                chunk = chunk[:-1].rstrip()
            key = json.loads(b'"' + raw_key + b'"')
            value = json.loads(chunk.decode("utf-8"))
            if key in seen or not isinstance(value, dict) or "name" not in value:
                raise ValueError("unexpected card structure")
            seen.add(key)
            yield key, value


def iter_cards(text: str):
    """JSON object ရဲ့ (key, value) တစ်စုံချင်းကို ဖတ်ပေးသည် — တစ်ခုလုံးကို တစ်ခါတည်း မဖတ်ပါ။"""
    decoder = json.JSONDecoder()
    index = _WS.match(text, 0).end()
    if text[index:index + 1] != "{":
        yield from json.loads(text).items()
        return
    index += 1
    while True:
        index = _WS.match(text, index).end()
        if text[index] == "}":
            return
        key, index = decoder.raw_decode(text, index)
        index = _WS.match(text, index).end()
        if text[index] != ":":
            raise ValueError(f"Invalid JSON near position {index}")
        index = _WS.match(text, index + 1).end()
        value, index = decoder.raw_decode(text, index)
        yield key, value
        index = _WS.match(text, index).end()
        if text[index] == ",":
            index += 1
        elif text[index] != "}":
            raise ValueError(f"Invalid JSON near position {index}")


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _flatten(inner, f"{prefix}/{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _flatten(inner, f"{prefix}/{index}" if prefix else str(index))
    elif value is not None:
        yield prefix, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def pack(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), 6)


def unpack(blob: bytes) -> str:
    return zlib.decompress(blob).decode("utf-8")


def _write(conn, cards) -> int:
    conn.execute("DROP TABLE IF EXISTS entries")
    conn.execute("CREATE TABLE entries (card_id TEXT NOT NULL, path TEXT NOT NULL, value BLOB NOT NULL,"
                 " PRIMARY KEY (card_id, path))")
    count = 0
    for card_id, card in cards:
        rows = [(str(card_id), path, pack(value)) for path, value in _flatten(card)]
        conn.executemany("INSERT OR REPLACE INTO entries VALUES (?, ?, ?)", rows)
        count += 1
    return count


def build(json_path: str, db_path: str, sha: str = None) -> int:
    """json_path က .json (သို့) .json.gz ဖြစ်နိုင်သည်။"""
    sha = sha or file_sha256(json_path)
    if str(json_path).endswith(".gz"):
        plain_path = db_path + ".source.json"
        with gzip.open(json_path, "rb") as source, open(plain_path, "wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
        try:
            return build(plain_path, db_path, sha)
        finally:
            os.remove(plain_path)
    tmp_path = db_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        try:
            count = _write(conn, iter_cards_file(json_path))      # RAM အနည်းဆုံးနည်း
        except ValueError:
            with open(json_path, "r", encoding="utf-8") as handle:  # JSON ပုံစံ မတူရင် အရန်နည်း
                count = _write(conn, iter_cards(handle.read()))
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("source_sha256", sha), ("format_version", FORMAT_VERSION), ("cards", str(count)),
        ])
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp_path, db_path)
    return count


if __name__ == "__main__":
    source, target = sys.argv[1], sys.argv[2]
    digest = sys.argv[3] if len(sys.argv) > 3 else None
    print(f"Built {build(source, target, digest)} cards into {target}")

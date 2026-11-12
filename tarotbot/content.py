"""ဟောစာတမ်း data များ။

* tarot_data.json (55MB) → SQLite (tarot_cards.db) ကနေ လိုတဲ့စာကိုပဲ ဖတ်သည် (RAM ~300MB သက်သာ)
* ကျန်တဲ့ JSON ဖိုင်တွေ (စုစုပေါင်း ~8MB) ကို memory ထဲ load လုပ်ထားသည်
"""
import gzip
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from . import config
from .cardstore_build import FORMAT_VERSION, file_sha256, unpack

logger = logging.getLogger(__name__)

CARD_IDS = tuple(str(i) for i in range(1, 79))


class SqliteCardStore:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self.names = {cid: unpack(blob) for cid, blob in
                      self._query_all("SELECT card_id, value FROM entries WHERE path = 'name'")}

    def _query_all(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def get(self, card_id: str, path: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM entries WHERE card_id = ? AND path = ?", (str(card_id), path)
            ).fetchone()
        return unpack(row[0]) if row else None


class JsonCardStore:
    """SQLite ဆောက်မရတဲ့အခါမှသာ သုံးမယ့် အရန်နည်း (RAM များများ သုံးသည်)။"""

    def __init__(self, data: dict):
        self._data = data
        self.names = {cid: card.get("name", "") for cid, card in data.items() if isinstance(card, dict)}

    def get(self, card_id: str, path: str) -> Optional[str]:
        value = self._data.get(str(card_id))
        for part in path.split("/"):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None
        return value if isinstance(value, str) else None


def _db_is_current(db_path: Path, sha: str) -> bool:
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
        return meta.get("source_sha256") == sha and meta.get("format_version") == FORMAT_VERSION
    except sqlite3.Error:
        return False


def card_source() -> Path:
    """ကဒ် data ဖိုင် — tarot_data.json.gz ရှိရင် အဲ့ဒါ၊ မရှိရင် tarot_data.json။"""
    return config.TAROT_GZ_PATH if config.TAROT_GZ_PATH.exists() else config.TAROT_JSON_PATH


def open_card_store(json_path: Optional[Path] = None, db_path: Path = config.CARD_DB_PATH):
    json_path = Path(json_path) if json_path else card_source()
    logger.info("Card data source: %s", json_path.name)
    if not json_path.exists():
        if db_path.exists():
            logger.warning("tarot_data.json not found; using existing %s", db_path.name)
            return SqliteCardStore(db_path)
        logger.error("tarot_data.json is missing — 3-card readings will show 'ပြင်ဆင်နေဆဲ'")
        return JsonCardStore({})
    sha = file_sha256(str(json_path))
    if not _db_is_current(db_path, sha):
        logger.info("Building %s from %s (separate process)…", db_path.name, json_path.name)
        result = subprocess.run(
            [sys.executable, "-m", "tarotbot.cardstore_build", str(json_path), str(db_path), sha],
            cwd=str(config.BASE_DIR), capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0 or not _db_is_current(db_path, sha):
            logger.error("Card DB build failed (%s): %s", result.returncode, (result.stderr or "")[-800:])
            logger.warning("Falling back to loading %s into memory", json_path.name)
            opener = gzip.open if json_path.suffix == ".gz" else open
            with opener(json_path, "rt", encoding="utf-8") as handle:
                return JsonCardStore(json.load(handle))
        logger.info("Card DB ready: %s", result.stdout.strip())
    return SqliteCardStore(db_path)


def _load_json(name: str, default):
    path = config.BASE_DIR / name
    if not path.exists():
        logger.warning("%s not found", name)
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.error("Could not load %s: %s", name, exc)
        return default


class Content:
    def __init__(self, card_store):
        self.cards = card_store
        self.daily = _load_json("daily_tarot.json", {})
        self.guide = _load_json("guide_data.json", {})
        self.personality = _load_json("zodiac_personality.json", {})
        self.tarot_love = {"near": _load_json("tarot_love_near.json", []),
                           "ldr": _load_json("tarot_love_ldr.json", [])}
        self.zodiac_love = {"near": _load_json("zodiac_love_near.json", {}),
                            "ldr": _load_json("zodiac_love_ldr.json", {})}
        self.weekly = _load_json("weekly_tarot.json", {})
        self.love_future = _load_json("love_future.json", {})
        logger.info("Content loaded: daily=%d guide=%d weekly=%d love_future=%d cards=%d",
                    len(self.daily), len(self.guide), len(self.weekly), len(self.love_future),
                    len(self.cards.names))

    # --- Tarot ကဒ်ကြီး data ---
    def card_name(self, card_id: str) -> str:
        return self.cards.names.get(str(card_id)) or f"Card {card_id}"

    def meaning(self, card_id: str, category_key: str, sub_key: str) -> Optional[str]:
        if category_key == "general":
            return self.cards.get(card_id, "general")
        return self.cards.get(card_id, f"{category_key}/{sub_key}")

    def zodiac_advice(self, card_id: str, sign: str, category_key: str, sub_key: str) -> str:
        # မူလ logic အတိုင်း: ရာသီတစ်ခုလုံးအတွက် စာ → ကဏ္ဍအတွက် စာ → ကဏ္ဍခွဲအတွက် စာ
        for path in (f"zodiac_rules/{sign}", f"zodiac_rules/{sign}/{category_key}",
                     f"zodiac_rules/{sign}/{category_key}/{sub_key}"):
            value = self.cards.get(card_id, path)
            if value:
                return value
        return ""

    # --- အခြား data ---
    def daily_text(self, card_id: str) -> Optional[str]:
        value = self.daily.get(str(card_id))
        return value if isinstance(value, str) else None

    def guide_entry(self, card_id: str) -> dict:
        value = self.guide.get(str(card_id))
        return value if isinstance(value, dict) else {}

    def weekly_entry(self, card_id: str) -> dict:
        value = self.weekly.get(str(card_id), {})
        return value.get("weekly", {}) if isinstance(value, dict) else {}

    def love_future_entry(self, card_id: str) -> dict:
        value = self.love_future.get(str(card_id), {})
        return value.get("love_future", {}) if isinstance(value, dict) else {}

    def personality_entry(self, sign: str, gender: str) -> dict:
        value = self.personality.get(sign, {})
        value = value.get(gender, {}) if isinstance(value, dict) else {}
        return value if isinstance(value, dict) else {}

    def zodiac_pair(self, relation: str, first: str, second: str) -> Optional[dict]:
        source = self.zodiac_love.get(relation) or {}
        return source.get(f"{first}_{second}") or source.get(f"{second}_{first}")

    def tarot_love_cards(self, relation: str) -> list:
        return self.tarot_love.get(relation) or []


_content: Optional[Content] = None


def init() -> Content:
    global _content
    _content = Content(open_card_store())
    return _content


def get() -> Content:
    if _content is None:
        raise RuntimeError("Content is not loaded")
    return _content


def card_image_path(card_id) -> Path:
    return config.CARDS_DIR / f"{card_id}.jpg"

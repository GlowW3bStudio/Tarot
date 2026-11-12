"""Supabase (PostgreSQL) — connection pool နဲ့ query များ။

* Connection pool ပြည့်ရင် error မတက်ဘဲ စောင့်သည် (BoundedSemaphore)
* Connection သေသွားရင် အဲ့ connection တစ်ခုကိုပဲ ပိတ်ပြီး အသစ်နဲ့ တစ်ခါ ပြန်ကြိုးစားသည်
  (pool တစ်ခုလုံး အသစ်မဆောက်တော့လို့ Supabase connection မယိုစိမ့်တော့ပါ)
* Async handler တွေက `await run(fn, ...)` နဲ့ ခေါ်သည် (event loop မပိတ်ဆို့အောင် thread ထဲမှာ run သည်)
"""
import asyncio
import logging
import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.pool
from psycopg2.extras import execute_values

from . import config

logger = logging.getLogger(__name__)

_RETRYABLE = (psycopg2.OperationalError, psycopg2.InterfaceError)


class Database:
    def __init__(self, dsn: str, size: int = config.DB_POOL_SIZE, sslmode: str = config.DB_SSLMODE):
        kwargs = dict(
            dsn=dsn, connect_timeout=15,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        if sslmode:
            kwargs["sslmode"] = sslmode
        self._pool = psycopg2.pool.ThreadedConnectionPool(1, size, **kwargs)
        self._slots = threading.BoundedSemaphore(size)

    @contextmanager
    def _connection(self):
        self._slots.acquire()
        conn = None
        try:
            conn = self._pool.getconn()
            yield conn
        finally:
            if conn is not None:
                try:
                    self._pool.putconn(conn, close=bool(conn.closed))
                except Exception:  # pool already closed during shutdown
                    pass
            self._slots.release()

    def run(self, work):
        """work(cursor) ကို transaction တစ်ခုအဖြစ် run ပြီး commit လုပ်သည်။"""
        for attempt in (1, 2):
            with self._connection() as conn:
                try:
                    with conn.cursor() as cur:
                        result = work(cur)
                    conn.commit()
                    return result
                except _RETRYABLE:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if attempt == 2:
                        raise
                    logger.warning("DB connection lost; retrying once with a fresh connection")
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
        return None

    def close(self):
        try:
            self._pool.closeall()
        except Exception:
            pass


_db: Optional[Database] = None


def init(dsn: str = None, **kwargs) -> Database:
    global _db
    _db = Database(dsn or config.DATABASE_URL, **kwargs)
    return _db


def get() -> Database:
    if _db is None:
        raise RuntimeError("Database is not initialised")
    return _db


async def run(fn, *args):
    """Sync DB function ကို thread ထဲမှာ run သည်။"""
    return await asyncio.to_thread(fn, *args)


def _fetchone(sql, params=()):
    def work(cur):
        cur.execute(sql, params)
        return cur.fetchone()
    return get().run(work)


def _fetchall(sql, params=()):
    def work(cur):
        cur.execute(sql, params)
        return cur.fetchall()
    return get().run(work)


def _execute(sql, params=()) -> int:
    def work(cur):
        cur.execute(sql, params)
        return cur.rowcount
    return get().run(work)


# --- Schema ------------------------------------------------------------------------
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS gift_credits INTEGER DEFAULT 7",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_credits INTEGER DEFAULT 3",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS birthday TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS zodiac TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS bday_status BOOLEAN DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
    """CREATE TABLE IF NOT EXISTS feedbacks (
        id SERIAL PRIMARY KEY, user_id BIGINT, rating TEXT, comment TEXT,
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS broadcast_logs (
        id SERIAL PRIMARY KEY, user_id BIGINT, message_id BIGINT,
        batch_id TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS bot_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS media_cache (
        media_key TEXT PRIMARY KEY, file_id TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS scheduled_actions (
        id BIGSERIAL PRIMARY KEY, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
        action TEXT NOT NULL, due_at TIMESTAMPTZ NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS feature_usage (
        day DATE NOT NULL, feature TEXT NOT NULL, user_id BIGINT NOT NULL,
        taps INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, feature, user_id))""",
    """CREATE INDEX IF NOT EXISTS idx_users_broadcast_eligible ON users (user_id)
        WHERE is_active = TRUE AND is_banned = FALSE""",
    "CREATE INDEX IF NOT EXISTS idx_broadcast_logs_batch_id ON broadcast_logs (batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_join_date ON users (join_date)",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_actions_due ON scheduled_actions (due_at)",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_actions_msg ON scheduled_actions (chat_id, message_id)",
)


def init_schema() -> List[int]:
    """Table များ ဆောက်ပြီး active user ID များကို ပြန်ပေးသည်။"""
    def work(cur):
        for statement in SCHEMA:
            cur.execute(statement)
        cur.execute("SELECT user_id FROM users WHERE is_active = TRUE")
        return [row[0] for row in cur.fetchall()]
    return get().run(work)


# --- Users -------------------------------------------------------------------------
def ensure_user_active(user_id: int) -> None:
    _execute(
        """INSERT INTO users (user_id, gift_credits, daily_credits, is_active, last_active)
           VALUES (%s, %s, %s, TRUE, CURRENT_TIMESTAMP)
           ON CONFLICT (user_id) DO UPDATE SET is_active = TRUE, last_active = CURRENT_TIMESTAMP
           WHERE users.is_active IS DISTINCT FROM TRUE""",
        (user_id, config.GIFT_CREDITS_LIMIT, config.DAILY_CREDITS_LIMIT),
    )


def get_credits(user_id: int) -> Optional[Tuple[int, int]]:
    return _fetchone("SELECT gift_credits, daily_credits FROM users WHERE user_id = %s", (user_id,))


def create_user(user_id: int) -> None:
    _execute(
        """INSERT INTO users (user_id, gift_credits, daily_credits) VALUES (%s, %s, %s)
           ON CONFLICT (user_id) DO NOTHING""",
        (user_id, config.GIFT_CREDITS_LIMIT, config.DAILY_CREDITS_LIMIT),
    )


def deduct_credit(user_id: int) -> int:
    """Gift credit ကို အရင်သုံးပြီး မရှိမှ daily credit ကို နှုတ်သည်။"""
    return _execute(
        """UPDATE users SET
               gift_credits = CASE WHEN gift_credits > 0 THEN gift_credits - 1 ELSE gift_credits END,
               daily_credits = CASE WHEN gift_credits > 0 THEN daily_credits
                                    WHEN daily_credits > 0 THEN daily_credits - 1
                                    ELSE daily_credits END
           WHERE user_id = %s AND (gift_credits > 0 OR daily_credits > 0)""",
        (user_id,),
    )


def is_banned(user_id: int) -> bool:
    row = _fetchone("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    return bool(row and row[0] is True)


def get_birthday(user_id: int):
    return _fetchone("SELECT birthday, zodiac, bday_status FROM users WHERE user_id = %s", (user_id,))


def get_zodiac(user_id: int) -> Optional[str]:
    row = _fetchone("SELECT zodiac FROM users WHERE user_id = %s", (user_id,))
    return row[0] if row and row[0] else None


def save_birthday(user_id: int, text: str, zodiac: str) -> None:
    _execute(
        """INSERT INTO users (user_id, birthday, zodiac, bday_status) VALUES (%s, %s, %s, TRUE)
           ON CONFLICT (user_id) DO UPDATE SET birthday = EXCLUDED.birthday,
               zodiac = EXCLUDED.zodiac, bday_status = TRUE""",
        (user_id, text, zodiac),
    )


def forget_birthday(user_id: int) -> None:
    _execute("UPDATE users SET bday_status = FALSE WHERE user_id = %s", (user_id,))


def reset_daily_credits() -> int:
    return _execute(
        "UPDATE users SET daily_credits = %s WHERE daily_credits IS DISTINCT FROM %s",
        (config.DAILY_CREDITS_LIMIT, config.DAILY_CREDITS_LIMIT),
    )


def add_gift_credits(user_id: int, amount: int) -> int:
    return _execute(
        "UPDATE users SET gift_credits = GREATEST(0, gift_credits + %s) WHERE user_id = %s",
        (amount, user_id),
    )


def gift_all(amount: int) -> int:
    return _execute("UPDATE users SET gift_credits = GREATEST(0, gift_credits + %s)", (amount,))


def set_banned(user_id: int, banned: bool) -> int:
    return _execute("UPDATE users SET is_banned = %s WHERE user_id = %s", (banned, user_id))


def count_banned() -> int:
    row = _fetchone("SELECT COUNT(*) FROM users WHERE is_banned = TRUE")
    return row[0] if row else 0


def user_info(user_id: int):
    return _fetchone(
        """SELECT gift_credits, daily_credits, birthday, zodiac, bday_status, join_date,
                  last_active, is_active, is_banned
           FROM users WHERE user_id = %s""",
        (user_id,),
    )


def user_counts(day_start: datetime, day_end: datetime):
    """(စုစုပေါင်း, စာပို့လို့ရသူ, ပိတ်ထားသူ, ban, ဒီနေ့အသစ်)"""
    return _fetchone(
        """SELECT COUNT(*),
                  COUNT(*) FILTER (WHERE is_active = TRUE AND is_banned = FALSE),
                  COUNT(*) FILTER (WHERE is_active = FALSE),
                  COUNT(*) FILTER (WHERE is_banned = TRUE),
                  COUNT(*) FILTER (WHERE join_date >= %s AND join_date < %s)
           FROM users""",
        (day_start, day_end),
    )


def recipients(after_user_id: int = 0) -> List[int]:
    rows = _fetchall(
        """SELECT user_id FROM users
           WHERE is_active = TRUE AND is_banned = FALSE AND user_id > %s
           ORDER BY user_id""",
        (after_user_id,),
    )
    return [row[0] for row in rows]


def mark_inactive(user_ids: Iterable[int]) -> int:
    ids = list(set(user_ids))
    if not ids:
        return 0
    return _execute("UPDATE users SET is_active = FALSE WHERE user_id = ANY(%s)", (ids,))


# --- Broadcast logs --------------------------------------------------------------------
def insert_broadcast_logs(rows: Sequence[Tuple[int, int, datetime]]) -> None:
    if not rows:
        return
    def work(cur):
        execute_values(cur, "INSERT INTO broadcast_logs (user_id, message_id, batch_id) VALUES %s",
                       list(rows), page_size=1000)
    get().run(work)


def last_broadcast_batch():
    row = _fetchone("SELECT MAX(batch_id) FROM broadcast_logs")
    return row[0] if row else None


def broadcast_batch_rows(batch) -> List[Tuple[int, int]]:
    return _fetchall("SELECT user_id, message_id FROM broadcast_logs WHERE batch_id = %s", (batch,))


def delete_broadcast_rows(batch, pairs: Sequence[Tuple[int, int]]) -> None:
    if not pairs:
        return
    def work(cur):
        execute_values(
            cur,
            """DELETE FROM broadcast_logs b USING (VALUES %s) AS v(batch_id, user_id, message_id)
               WHERE b.batch_id = v.batch_id AND b.user_id = v.user_id AND b.message_id = v.message_id""",
            [(batch, int(u), int(m)) for u, m in pairs],
            template="(%s::timestamp, %s::bigint, %s::bigint)", page_size=1000,
        )
    get().run(work)


# --- Settings / media cache ------------------------------------------------------------------
def load_settings() -> dict:
    return dict(_fetchall("SELECT key, value FROM bot_settings"))


def save_setting(key: str, value: str) -> None:
    _execute(
        """INSERT INTO bot_settings (key, value, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, value),
    )


def load_media_cache() -> dict:
    return dict(_fetchall("SELECT media_key, file_id FROM media_cache"))


def save_media(key: str, file_id: str) -> None:
    _execute(
        """INSERT INTO media_cache (media_key, file_id, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (media_key) DO UPDATE SET file_id = EXCLUDED.file_id, updated_at = now()""",
        (key, file_id),
    )


# --- Scheduled actions (message ဖျက်ခြင်း / ခလုတ်ဖြုတ်ခြင်း) -------------------------------------
def add_actions(rows: Sequence[Tuple[int, int, str, datetime]]) -> None:
    if not rows:
        return
    def work(cur):
        execute_values(cur, "INSERT INTO scheduled_actions (chat_id, message_id, action, due_at) VALUES %s",
                       list(rows), page_size=1000)
    get().run(work)


def due_actions(now: datetime, limit: int = 2000) -> List[Tuple[int, int, int, str]]:
    return _fetchall(
        """SELECT id, chat_id, message_id, action FROM scheduled_actions
           WHERE due_at <= %s ORDER BY due_at LIMIT %s""",
        (now, limit),
    )


def delete_actions(ids: Sequence[int]) -> None:
    if ids:
        _execute("DELETE FROM scheduled_actions WHERE id = ANY(%s)", (list(ids),))


def drop_message_actions(chat_id: int, message_id: int, action: Optional[str] = None) -> None:
    if action:
        _execute("DELETE FROM scheduled_actions WHERE chat_id = %s AND message_id = %s AND action = %s",
                 (chat_id, message_id, action))
    else:
        _execute("DELETE FROM scheduled_actions WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))


def change_message_action(chat_id: int, message_id: int, old: str, new: str) -> None:
    _execute("UPDATE scheduled_actions SET action = %s WHERE chat_id = %s AND message_id = %s AND action = %s",
             (new, chat_id, message_id, old))


def pending_action_count() -> int:
    row = _fetchone("SELECT COUNT(*) FROM scheduled_actions")
    return row[0] if row else 0


# --- Feature usage ---------------------------------------------------------------------------------
def add_usage(rows: Sequence[Tuple[date, str, int, int]]) -> None:
    if not rows:
        return
    def work(cur):
        execute_values(
            cur,
            """INSERT INTO feature_usage (day, feature, user_id, taps) VALUES %s
               ON CONFLICT (day, feature, user_id) DO UPDATE
               SET taps = feature_usage.taps + EXCLUDED.taps""",
            list(rows), page_size=1000,
        )
    get().run(work)


def usage_summary(first_day: date, last_day: date) -> List[Tuple[str, int, int]]:
    """(feature, နှိပ်ကြိမ်, နှိပ်သူဦးရေ) — ရက်အပိုင်းအခြားအတွင်း"""
    return _fetchall(
        """SELECT feature, SUM(taps)::BIGINT, COUNT(DISTINCT user_id)
           FROM feature_usage WHERE day BETWEEN %s AND %s
           GROUP BY feature ORDER BY SUM(taps) DESC""",
        (first_day, last_day),
    )


def usage_daily_users(first_day: date, last_day: date) -> List[Tuple[date, int]]:
    return _fetchall(
        """SELECT day, COUNT(DISTINCT user_id) FROM feature_usage
           WHERE day BETWEEN %s AND %s GROUP BY day ORDER BY day""",
        (first_day, last_day),
    )


def prune_old_rows(usage_before: date, logs_before: datetime) -> None:
    def work(cur):
        cur.execute("DELETE FROM feature_usage WHERE day < %s", (usage_before,))
        cur.execute("DELETE FROM broadcast_logs WHERE batch_id < %s", (logs_before,))
    get().run(work)

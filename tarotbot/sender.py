"""User အများကြီးဆီ စာပို့စနစ် (broadcast / daily / weekly / message ဖျက်ခြင်း)။

* တစ်စက္ကန့် MASS_SEND_RATE စောင်နှုန်းနဲ့ တည်ငြိမ်စွာ ပို့သည် (Telegram ~30/s)
* Telegram က "ခဏစောင့်" (RetryAfter) ပြောရင် အားလုံးကို ခဏရပ်ပြီး ဆက်ပို့သည် — လုံးဝ မရပ်တော့ပါ
* Bot ကို ပိတ်ထားသူ / account ဖျက်ထားသူ / chat မတွေ့သူ → inactive အဖြစ် မှတ်သည်
"""
import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Optional

import httpx
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from . import config

logger = logging.getLogger(__name__)

OK, INACTIVE, FAILED, UNCERTAIN = "ok", "inactive", "failed", "uncertain"


class RateLimiter:
    def __init__(self, rate: float):
        self.interval = 1.0 / rate
        self._next = 0.0
        self._pause_until = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next, self._pause_until)
            self._next = slot + self.interval
        delay = slot - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def pause(self, seconds: float) -> None:
        until = time.monotonic() + seconds
        self._pause_until = max(self._pause_until, until)


LIMITER = RateLimiter(config.MASS_SEND_RATE)


class PremiumEmojiError(Exception):
    """Telegram က premium emoji ခလုတ်ကို လက်မခံတဲ့အခါ။"""


def _retry_seconds(exc: RetryAfter) -> float:
    value = exc.retry_after
    return value.total_seconds() if hasattr(value, "total_seconds") else float(value)


def looks_like_emoji_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, BadRequest) and any(k in text for k in ("emoji", "icon"))


_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def never_sent(exc: BaseException) -> bool:
    """Request က Telegram ဆီ မရောက်ခဲ့တာ သေချာလား (ချိတ်ဆက်မှုကိုတောင် မဖွင့်နိုင်ခဲ့ / pool စောင့်ချိန်ကုန်)။
    ဒီလိုဆိုရင်ပဲ ပြန်ပို့သည် — ရောက်ပြီးသား ဖြစ်နိုင်တဲ့ request ကို ပြန်ပို့ရင် စာနှစ်ခါ ရောက်နိုင်လို့ပါ။"""
    seen, cause = set(), exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, _NOT_SENT):
            return True
        cause = cause.__cause__ or cause.__context__
    return "pool timeout" in str(exc).lower()


async def call(func: Callable[..., Awaitable], *args, limiter: Optional[RateLimiter] = LIMITER, **kwargs):
    """Telegram API တစ်ခါခေါ်ခြင်း — rate limit + RetryAfter စောင့်ခြင်း + ချိတ်ဆက်မရရင် ၂ ခါထိ ပြန်ကြိုးစားခြင်း။"""
    safe_retries = 0
    for _ in range(8):
        if limiter is not None:
            await limiter.wait()
        try:
            return await func(*args, **kwargs)
        except RetryAfter as exc:
            seconds = _retry_seconds(exc) + 1
            logger.warning("Flood control: pausing %.0fs", seconds)
            if limiter is not None:
                limiter.pause(seconds)
            else:
                await asyncio.sleep(seconds)
        except BadRequest as exc:
            if looks_like_emoji_error(exc):
                raise PremiumEmojiError(str(exc)) from exc
            raise
        except NetworkError as exc:  # TimedOut လည်း ဒီထဲမှာ ပါသည်
            if never_sent(exc) and safe_retries < 2:
                safe_retries += 1
                logger.warning("Could not reach Telegram (%s); retry %d/2", type(exc).__name__, safe_retries)
                await asyncio.sleep(1.5 * safe_retries)
                continue
            raise  # ရောက်/မရောက် မသေချာရင် ထပ်မပို့ပါ (နှစ်ခါရောက်မှာ စိုးလို့)
    raise RetryAfter(30)


def classify(exc: BaseException):
    """(outcome, မြန်မာလို အကြောင်းရင်း)"""
    text = str(exc).lower()
    if isinstance(exc, Forbidden):
        if "deactivated" in text:
            return INACTIVE, "Account ဖျက်ထားသူ"
        if "to bots" in text:
            return INACTIVE, "Bot / Telegram system account"
        return INACTIVE, "Bot ကို ပိတ်ထားသူ"
    if isinstance(exc, BadRequest):
        if "chat not found" in text or "user not found" in text or "peer_id_invalid" in text:
            return INACTIVE, "Chat မတွေ့တော့သူ"
        if "bot_to_bot" in text:
            return INACTIVE, "Bot / Telegram system account"
        return FAILED, f"Telegram က လက်မခံ ({str(exc)[:80]})"
    if isinstance(exc, PremiumEmojiError):
        return FAILED, "Premium emoji ကို Telegram က လက်မခံ"
    if isinstance(exc, TimedOut):
        return UNCERTAIN, "အချိန်ကုန်သွား (ရောက်/မရောက် မသေချာ)"
    if isinstance(exc, (NetworkError, RetryAfter)):
        return UNCERTAIN, "ကွန်ရက် ပြဿနာ"
    return FAILED, f"အခြားအမှား ({type(exc).__name__})"


@dataclass
class MassResult:
    total: int = 0
    attempted: int = 0
    ok: int = 0
    inactive: int = 0
    failed: int = 0
    uncertain: int = 0
    reasons: Counter = field(default_factory=Counter)
    started: float = field(default_factory=time.monotonic)
    finished: Optional[float] = None
    stopped: bool = False

    @property
    def seconds(self) -> int:
        return int((self.finished or time.monotonic()) - self.started)


async def run_mass(
    recipients: Iterable[int],
    send_one: Callable[[int], Awaitable[object]],
    *,
    on_inactive: Optional[Callable[[List[int]], Awaitable[None]]] = None,
    on_progress: Optional[Callable[[MassResult], Awaitable[None]]] = None,
    progress_every: int = config.PROGRESS_EVERY,
    workers: int = config.MASS_SEND_WORKERS,
    should_stop: Optional[Callable[[], bool]] = None,
) -> MassResult:
    """recipients အားလုံးကို send_one(user_id) နဲ့ ပို့သည်။ user တစ်ယောက် မရလို့ တစ်ခုလုံး မရပ်ပါ။"""
    queue: asyncio.Queue = asyncio.Queue()
    for uid in recipients:
        queue.put_nowait(uid)
    result = MassResult(total=queue.qsize())
    inactive_buffer: List[int] = []
    next_progress = progress_every

    async def flush_inactive() -> None:
        if inactive_buffer and on_inactive:
            batch = inactive_buffer[:]
            inactive_buffer.clear()
            try:
                await on_inactive(batch)
            except Exception:
                logger.exception("Could not mark %d users inactive", len(batch))

    async def worker() -> None:
        nonlocal next_progress
        while True:
            if should_stop and should_stop():
                result.stopped = True
                return
            try:
                uid = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await send_one(uid)
                result.ok += 1
            except Exception as exc:  # noqa: BLE001 — user တစ်ယောက်ချင်းစီရဲ့ အမှား
                outcome, reason = classify(exc)
                result.reasons[reason] += 1
                if outcome == INACTIVE:
                    result.inactive += 1
                    inactive_buffer.append(uid)
                elif outcome == UNCERTAIN:
                    result.uncertain += 1
                else:
                    result.failed += 1
                if outcome != INACTIVE:
                    logger.warning("Mass send uid=%s: %s", uid, reason)
            result.attempted += 1
            if len(inactive_buffer) >= 200:
                await flush_inactive()
            if on_progress and result.attempted >= next_progress:
                next_progress += progress_every
                try:
                    await on_progress(result)
                except Exception:
                    logger.exception("Progress callback failed")

    await asyncio.gather(*(worker() for _ in range(max(1, workers))))
    await flush_inactive()
    result.finished = time.monotonic()
    return result

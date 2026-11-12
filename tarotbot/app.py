"""Bot ကို စတင်ခြင်း — logging၊ Render web server၊ handler နဲ့ job အားလုံး ချိတ်ဆက်ခြင်း။"""
import logging
import re
import sys
import warnings
from logging.handlers import RotatingFileHandler
from threading import Thread

from flask import Flask
from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CallbackQueryHandler, TypeHandler
from telegram.warnings import PTBUserWarning

from . import config, content, db, delivery, handlers_admin, handlers_user, jobs, runtime, sender
from .richtext import esc

logger = logging.getLogger("tarotbot")
_TOKEN_PATTERN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")


class _RedactTokens(logging.Filter):
    """Log ထဲမှာ bot token မပေါ်အောင် ဖုံးသည်။"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _TOKEN_PATTERN.search(message):
            record.msg, record.args = _TOKEN_PATTERN.sub("[TOKEN]", message), ()
        return True


def setup_logging() -> None:
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(_RedactTokens())
    file_handler = RotatingFileHandler(config.LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RedactTokens())
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler], force=True)
    # Request တစ်ခုချင်းစီကို log မရေးတော့ (token ပေါက်ကြားမှု + CPU သက်သာ)
    for noisy in ("httpx", "httpcore", "apscheduler", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=PTBUserWarning, message=".*per_message.*")


# --- Render web server (UptimeRobot က ping လုပ်ရန်) -------------------------------------------------
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is Alive!"


def start_web_server() -> None:
    thread = Thread(target=lambda: web_app.run(host="0.0.0.0", port=config.PORT, use_reloader=False),
                    daemon=True, name="web")
    thread.start()


# --- Error handler ------------------------------------------------------------------------------------
_HARMLESS = ("query is too old", "message is not modified", "message to delete not found",
             "message to edit not found", "query id is invalid")


async def on_error(update: object, context) -> None:
    error = context.error
    if isinstance(error, sender.PremiumEmojiError) or sender.looks_like_emoji_error(error):
        await delivery.disable_premium(context.bot)
        return
    if isinstance(error, Forbidden) or (isinstance(error, BadRequest) and any(h in str(error).lower() for h in _HARMLESS)):
        logger.info("Ignored Telegram error: %s", error)
        return
    logger.error("Unhandled error while processing an update", exc_info=error)
    kind = type(error).__name__
    await runtime.notify_admin_once(
        context.bot, f"error:{kind}",
        f"⚠️ <b>Bot မှာ အမှားတစ်ခု ဖြစ်ခဲ့ပါတယ်</b>\n<code>{esc(kind)}: {esc(str(error)[:300])}</code>\n"
        "(တူညီတဲ့ အမှားကို တစ်နာရီ တစ်ခါပဲ အသိပေးပါမယ်)",
    )


# --- Application ------------------------------------------------------------------------------------------
async def post_init(application: Application) -> None:
    jobs.schedule(application.job_queue)
    logger.info("🚀 Tarot Bot started (rich=%s, premium=%s)", runtime.settings.get("rich_mode"),
                runtime.settings.get("premium_emoji"))


async def post_shutdown(application: Application) -> None:
    await runtime.flush_usage()
    db.get().close()


def build_application(request=None) -> Application:
    """request = စမ်းသပ်ရန်အတွက်သာ (Telegram အတု)။ ပုံမှန် run တဲ့အခါ မပေးပါ။"""
    builder = (Application.builder().token(config.BOT_TOKEN).concurrent_updates(True)
               .post_init(post_init).post_shutdown(post_shutdown))
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    else:
        builder = builder.connect_timeout(10).read_timeout(20).write_timeout(30).pool_timeout(10)
    application = builder.build()
    application.add_handler(TypeHandler(Update, handlers_user.mark_user_active), group=-1)
    application.add_handler(handlers_user.conversation())
    for handler in handlers_admin.handlers():
        application.add_handler(handler)
    for handler in handlers_user.card_handlers():
        application.add_handler(handler)
    application.add_handler(CallbackQueryHandler(handlers_user.stale_button))
    application.add_error_handler(on_error)
    return application


def prepare() -> None:
    """Data load + DB ချိတ်ဆက်ခြင်း (RAM နည်းသေးချိန်မှာ card DB ကို အရင်ဆောက်သည်)။"""
    content.init()
    try:
        db.init()
        runtime.active_users.update(db.init_schema())
    except Exception:
        logger.critical("❌ Database connection failed — check DATABASE_URL", exc_info=True)
        sys.exit(1)
    runtime.load_settings()
    runtime.load_media_cache()
    logger.info("✅ Database ready. Active users cached: %d", len(runtime.active_users))


def run() -> None:
    setup_logging()
    if not config.BOT_TOKEN:
        logger.critical("❌ BOT_TOKEN is not set")
        sys.exit(1)
    prepare()
    start_web_server()
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)

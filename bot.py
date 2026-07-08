# -*- coding: utf-8 -*-
"""
Premium Referral + Daily-Utility Bot
=====================================
Requires:
    pip install "python-telegram-bot[job-queue]" aiohttp sqlalchemy "qrcode[pil]" pillow

Environment variables:
    BOT_TOKEN   - your Telegram bot token (required)
    ADMIN_IDS   - comma-separated Telegram user IDs allowed to use admin commands
                  e.g. ADMIN_IDS="111111111,222222222"

Feature groups (persistent keyboard + slash commands):
    Registration : /start, /stats, /my, /referral            (existing platform signup flow)
    Utilities    : /calc, /convert, /currency, /weather, /news
    Productivity : /note, /todo, /remind
    Fun          : /joke, /quote, /guess
    Dev          : /snippet (Python/Java examples, no code execution)
    Files        : send photos then /makepdf, /clearimages
    Account      : /language, /support
    Admin only   : /broadcast, /adminusers, /ban, /unban, /export
"""

import os
import io
import csv
import re
import ast
import math
import random
import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime, time as dtime
from typing import Dict, Any, Optional

import aiohttp
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Index, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False


# ─────────────────────────── Config ───────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8273845274:AAGgXnqq27WARi2uQVNg5ImJijjQ0y55CGM")
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "5888777479").replace(" ", "").split(",") if x.isdigit()
}

HOLWIN_INVITE_CODE = "WLRPSY"
REX_INVITE_CODE = "O6NVYX"

HOLWIN_BASE = "https://www.holwin123.top"
HOLWIN_DI = "88dd52c70e7b377527be01c39f5a0a4f"
HOLWIN_VTOKEN = "18667bd921478af5fe5f6506865e4f8a"

REX_BASE = "https://rcapi.rexproearn.com"
REX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://rch5.rexproearn.com",
    "Referer": "https://rch5.rexproearn.com/",
}

DATABASE_URL = "sqlite:///registrations.db"

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ─────────────────────────── DB Models ───────────────────────────

class Registration(Base):
    __tablename__ = "registrations"
    id = Column(Integer, primary_key=True)
    mobile = Column(String(20), nullable=False)
    platform = Column(String(20), nullable=False)
    invite_used = Column(String(20), nullable=False)
    telegram_id = Column(Integer, nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_platform", "platform"),
        Index("idx_telegram_id", "telegram_id"),
        Index("idx_registered_at", "registered_at"),
    )


class BotUser(Base):
    __tablename__ = "bot_users"
    telegram_id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=True)
    language = Column(String(5), default="en", nullable=False)
    is_banned = Column(Boolean, default=False, nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_active = Column(DateTime, default=datetime.utcnow, nullable=False)


class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, nullable=False)
    text = Column(String(2000), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("idx_note_user", "telegram_id"),)


class TodoItem(Base):
    __tablename__ = "todos"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, nullable=False)
    text = Column(String(500), nullable=False)
    done = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("idx_todo_user", "telegram_id"),)


Base.metadata.create_all(engine)

MOBILE, OTP, PASSWORD, CONFIRM = range(4)


# ─────────────────────────── i18n ───────────────────────────

STRINGS = {
    "main_title": {
        "en": "💎  R E F E R R A L   B O T  💎",
        "hi": "💎  र े फ र ल   ब ॉ ट  💎",
    },
    "select_platform": {
        "en": "🚀 *Select your platform:*",
        "hi": "🚀 *अपना प्लेटफ़ॉर्म चुनें:*",
    },
    "features": {
        "en": "🛡️ *Features:* OTP resend • Change mobile • Stats • Referral QR • Multi\\-language",
        "hi": "🛡️ *सुविधाएं:* OTP दोबारा भेजें • मोबाइल बदलें • आँकड़े • रेफ़रल QR • बहुभाषी",
    },
    "help": {
        "en": (
            "❓ *Help Center*\n\n"
            "1\\. Choose a platform from the main menu\\.\n"
            "2\\. Enter your mobile number \\(10\\-15 digits\\)\\.\n"
            "3\\. Enter the OTP you receive\\.\n"
            "4\\. Set a password or type `skip`\\.\n"
            "5\\. Confirm and register\\.\n\n"
            "📊 /stats \\- global stats\n"
            "📋 /my \\- your registrations\n"
            "🔗 /referral \\- your referral link \\+ QR\n"
            "🌐 /language \\- switch language\n"
            "🆘 /support \\- quick answers\n"
            "🔄 /start \\- main menu\n"
            "❌ /cancel \\- abort current action"
        ),
        "hi": (
            "❓ *सहायता केंद्र*\n\n"
            "1\\. मुख्य मेनू से एक प्लेटफ़ॉर्म चुनें\\.\n"
            "2\\. अपना मोबाइल नंबर \\(10\\-15 अंक\\) दर्ज करें\\.\n"
            "3\\. प्राप्त OTP दर्ज करें\\.\n"
            "4\\. पासवर्ड सेट करें या `skip` टाइप करें\\.\n"
            "5\\. पुष्टि करें और रजिस्टर करें\\.\n\n"
            "📊 /stats \\- वैश्विक आँकड़े\n"
            "📋 /my \\- आपके पंजीकरण\n"
            "🔗 /referral \\- आपका रेफ़रल लिंक \\+ QR\n"
            "🌐 /language \\- भाषा बदलें\n"
            "🆘 /support \\- त्वरित उत्तर\n"
            "🔄 /start \\- मुख्य मेनू\n"
            "❌ /cancel \\- रद्द करें"
        ),
    },
    "lang_prompt": {
        "en": "🌐 Choose your language:",
        "hi": "🌐 अपनी भाषा चुनें:",
    },
    "lang_set": {
        "en": "✅ Language set to English.",
        "hi": "✅ भाषा हिंदी में सेट हो गई।",
    },
    "enter_mobile": {
        "en": "📱 Enter your mobile number \\(10\\-15 digits\\):",
        "hi": "📱 अपना मोबाइल नंबर \\(10\\-15 अंक\\) दर्ज करें:",
    },
    "invalid_mobile": {
        "en": "❌ Invalid. Enter 10-15 digits:",
        "hi": "❌ अमान्य। 10-15 अंक दर्ज करें:",
    },
    "banned": {
        "en": "🚫 Your access has been restricted. Contact the admin.",
        "hi": "🚫 आपकी पहुँच प्रतिबंधित कर दी गई है। एडमिन से संपर्क करें।",
    },
}


def L(key: str, lang: str) -> str:
    entry = STRINGS.get(key, {})
    return entry.get(lang, entry.get("en", key))


# ─────────────────────────── Markdown escaping ───────────────────────────

_MDV2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def esc(text: str) -> str:
    return _MDV2_SPECIAL.sub(r'\\\1', str(text))


# ─────────────────────────── Keyboards ───────────────────────────

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Holwin", callback_data="platform_holwin"),
            InlineKeyboardButton("📈 Rexproearn", callback_data="platform_rex"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="stats_btn"),
            InlineKeyboardButton("📋 My Registrations", callback_data="my_btn"),
        ],
        [
            InlineKeyboardButton("🔗 Referral QR", callback_data="referral_btn"),
            InlineKeyboardButton("🌐 Language", callback_data="lang_btn"),
        ],
        [
            InlineKeyboardButton("🆘 Support", callback_data="support_btn"),
            InlineKeyboardButton("❓ Help", callback_data="help_btn"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]])


def otp_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Resend OTP", callback_data="resend_otp")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")],
    ])


def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reg")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reg")],
    ])


def main_reply_keyboard():
    """Persistent bottom keyboard - always visible under the chat box."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🎰 Register"), KeyboardButton("🧮 Calculator"), KeyboardButton("🔄 Convert")],
            [KeyboardButton("💱 Currency"), KeyboardButton("☁️ Weather"), KeyboardButton("📰 News")],
            [KeyboardButton("📝 Notes"), KeyboardButton("✅ To-Do"), KeyboardButton("⏰ Reminder")],
            [KeyboardButton("😂 Fun"), KeyboardButton("📄 Image→PDF"), KeyboardButton("💻 Code Snippets")],
            [KeyboardButton("🌐 Language"), KeyboardButton("🆘 Support")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def language_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("English", callback_data="setlang_en"),
            InlineKeyboardButton("हिंदी", callback_data="setlang_hi"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


# ─────────────────────────── DB helpers ───────────────────────────

def db_session():
    return SessionLocal()


def get_or_create_user(telegram_id: int, username: Optional[str]) -> "BotUser":
    db = db_session()
    try:
        user = db.query(BotUser).filter(BotUser.telegram_id == telegram_id).first()
        if user is None:
            user = BotUser(telegram_id=telegram_id, username=username, language="en")
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.last_active = datetime.utcnow()
            if username and user.username != username:
                user.username = username
            db.commit()
        # detach values we need before closing the session
        return {"telegram_id": user.telegram_id, "language": user.language, "is_banned": user.is_banned}
    finally:
        db.close()


def set_user_language(telegram_id: int, lang: str):
    db = db_session()
    try:
        user = db.query(BotUser).filter(BotUser.telegram_id == telegram_id).first()
        if user:
            user.language = lang
            db.commit()
    finally:
        db.close()


def is_user_banned(telegram_id: int) -> bool:
    db = db_session()
    try:
        user = db.query(BotUser).filter(BotUser.telegram_id == telegram_id).first()
        return bool(user and user.is_banned)
    finally:
        db.close()


def set_ban_status(telegram_id: int, banned: bool) -> bool:
    db = db_session()
    try:
        user = db.query(BotUser).filter(BotUser.telegram_id == telegram_id).first()
        if not user:
            return False
        user.is_banned = banned
        db.commit()
        return True
    finally:
        db.close()


def get_all_user_ids():
    db = db_session()
    try:
        return [u.telegram_id for u in db.query(BotUser).filter(BotUser.is_banned == False).all()]  # noqa: E712
    finally:
        db.close()


def save_registration(mobile: str, platform: str, invite: str, telegram_id: int):
    db: Session = db_session()
    try:
        db.add(Registration(mobile=mobile, platform=platform, invite_used=invite, telegram_id=telegram_id))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"DB save error: {e}")
        raise
    finally:
        db.close()


def get_stats():
    db = db_session()
    try:
        total = db.query(func.count(Registration.id)).scalar() or 0
        holwin = db.query(func.count(Registration.id)).filter(Registration.platform == "holwin").scalar() or 0
        rex = db.query(func.count(Registration.id)).filter(Registration.platform == "rex").scalar() or 0
        recent = db.query(Registration).order_by(Registration.registered_at.desc()).limit(10).all()
        return total, holwin, rex, recent
    finally:
        db.close()


def get_user_stats(user_id: int):
    db = db_session()
    try:
        total = db.query(func.count(Registration.id)).filter(Registration.telegram_id == user_id).scalar() or 0
        holwin = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id, Registration.platform == "holwin"
        ).scalar() or 0
        rex = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id, Registration.platform == "rex"
        ).scalar() or 0
        return total, holwin, rex
    finally:
        db.close()


def export_registrations_csv() -> io.BytesIO:
    db = db_session()
    try:
        rows = db.query(Registration).order_by(Registration.registered_at.desc()).all()
    finally:
        db.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "mobile", "platform", "invite_used", "telegram_id", "registered_at"])
    for r in rows:
        writer.writerow([r.id, r.mobile, r.platform, r.invite_used, r.telegram_id, r.registered_at.isoformat()])

    byte_buf = io.BytesIO(buf.getvalue().encode("utf-8"))
    byte_buf.name = f"registrations_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    return byte_buf


# ─────────────────────────── Admin guard ───────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def require_admin(update: Update) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        if update.callback_query:
            await update.callback_query.answer("🚫 Admins only.", show_alert=True)
        else:
            await update.message.reply_text("🚫 This command is for admins only.")
        return False
    return True


# ─────────────────────────── Calculator (safe eval) ───────────────────────────

_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "floor": math.floor, "ceil": math.ceil,
    "factorial": math.factorial, "abs": abs, "round": round,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def safe_calc(expr: str) -> float:
    """Evaluate a basic arithmetic expression without using eval()."""
    node = ast.parse(expr, mode="eval").body

    def _eval(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, _ALLOWED_BINOPS):
            left, right = _eval(n.left), _eval(n.right)
            if isinstance(n.op, ast.Add):
                return left + right
            if isinstance(n.op, ast.Sub):
                return left - right
            if isinstance(n.op, ast.Mult):
                return left * right
            if isinstance(n.op, ast.Div):
                return left / right
            if isinstance(n.op, ast.Pow):
                return left ** right
            if isinstance(n.op, ast.Mod):
                return left % right
            if isinstance(n.op, ast.FloorDiv):
                return left // right
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, _ALLOWED_UNARY):
            val = _eval(n.operand)
            return val if isinstance(n.op, ast.UAdd) else -val
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _ALLOWED_FUNCS:
            args = [_eval(a) for a in n.args]
            return _ALLOWED_FUNCS[n.func.id](*args)
        if isinstance(n, ast.Name) and n.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[n.id]
        raise ValueError("Expression contains a disallowed operation")

    return _eval(node)


# ─────────────────────────── Unit converter ───────────────────────────

_LENGTH_TO_M = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mile": 1609.34, "ft": 0.3048, "in": 0.0254, "yd": 0.9144}
_WEIGHT_TO_KG = {"kg": 1, "g": 0.001, "mg": 1e-6, "lb": 0.453592, "oz": 0.0283495}


def convert_units(value: float, from_u: str, to_u: str) -> Optional[float]:
    from_u, to_u = from_u.lower(), to_u.lower()

    if from_u in ("c", "f", "k") and to_u in ("c", "f", "k"):
        # Temperature (handled separately: convert to Celsius first, then to target)
        if from_u == "c":
            celsius = value
        elif from_u == "f":
            celsius = (value - 32) * 5 / 9
        else:  # kelvin
            celsius = value - 273.15
        if to_u == "c":
            return celsius
        if to_u == "f":
            return celsius * 9 / 5 + 32
        return celsius + 273.15

    if from_u in _LENGTH_TO_M and to_u in _LENGTH_TO_M:
        return value * _LENGTH_TO_M[from_u] / _LENGTH_TO_M[to_u]

    if from_u in _WEIGHT_TO_KG and to_u in _WEIGHT_TO_KG:
        return value * _WEIGHT_TO_KG[from_u] / _WEIGHT_TO_KG[to_u]

    return None


async def get_currency_rate(session: aiohttp.ClientSession, base: str) -> Optional[Dict[str, float]]:
    try:
        async with session.get(f"https://open.er-api.com/v6/latest/{base.upper()}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)
            if data.get("result") == "success":
                return data.get("rates")
    except Exception as e:
        logger.warning(f"Currency API error: {e}")
    return None


# ─────────────────────────── API Clients ───────────────────────────

class HolwinClient:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.holwin123.top",
            "Referer": "https://www.holwin123.top/userRegister",
            "di": HOLWIN_DI,
            "vtoken": HOLWIN_VTOKEN,
        }
        self.session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self.session.post(f"{HOLWIN_BASE}{path}", json=payload) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                logger.error(f"Holwin non-JSON response ({resp.status}): {e}")
                return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
            return data if isinstance(data, dict) else {"code": -1, "msg": "Unexpected response format"}


class RexClient:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self.session = aiohttp.ClientSession(headers=REX_HEADERS, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self.session.post(f"{REX_BASE}{path}", json=payload) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                logger.error(f"Rex non-JSON response ({resp.status}): {e}")
                return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
            return data if isinstance(data, dict) else {"code": -1, "msg": "Unexpected response format"}


# ─────────────────────────── User bootstrap / ban gate ───────────────────────────

async def touch_user_and_check_ban(update: Update) -> Dict[str, Any]:
    user = update.effective_user
    info = get_or_create_user(user.id, user.username)
    return info


# ─────────────────────────── Core handlers ───────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    info = await touch_user_and_check_ban(update)
    lang = info["language"]

    if info["is_banned"]:
        text = L("banned", lang)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    msg = (
        "╔═══════════════════════════════╗\n"
        f"║   {L('main_title', lang)}   ║\n"
        "╚═══════════════════════════════╝\n\n"
        f"{L('select_platform', lang)}\n\n"
        "┌─────────────────────────────┐\n"
        "│  🏠 *Holwin*                │\n"
        f"│  Invite: `{esc(HOLWIN_INVITE_CODE)}`   │\n"
        "├─────────────────────────────┤\n"
        "│  📈 *Rexproearn*            │\n"
        f"│  Invite: `{esc(REX_INVITE_CODE)}`      │\n"
        "└─────────────────────────────┘\n\n"
        f"{L('features', lang)}\n"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_keyboard(), disable_web_page_preview=True
        )
    else:
        # ReplyKeyboardMarkup (the persistent bottom keyboard) can only be attached
        # to a brand-new message, not an edit, so we send it once here on /start.
        await update.message.reply_text(
            msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_keyboard(), disable_web_page_preview=True
        )
        await update.message.reply_text(
            "👇 Quick access menu is pinned below.", reply_markup=main_reply_keyboard()
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = await touch_user_and_check_ban(update)
    text = L("help", info["language"])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=back_keyboard())
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=back_keyboard())


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = await touch_user_and_check_ban(update)
    text = L("lang_prompt", info["language"])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=language_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=language_keyboard())


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.split("_")[1]  # setlang_en / setlang_hi
    set_user_language(update.effective_user.id, lang)
    await q.edit_message_text(L("lang_set", lang), reply_markup=back_keyboard())


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    total, holwin, rex, recent = get_stats()
    msg = (
        "📊 *Global Stats*\n\n"
        f"👥 Total: `{total}`\n"
        f"🏠 Holwin: `{holwin}`\n"
        f"📈 Rexproearn: `{rex}`\n\n"
        "🕒 *Last 10 Registrations:*\n"
    )
    if recent:
        for r in recent:
            msg += f"• `{esc(r.mobile)}` \\- {esc(r.platform.upper())} \\- {esc(r.registered_at.strftime('%Y-%m-%d %H:%M'))}\n"
    else:
        msg += "No registrations yet\\."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="stats_btn")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)


async def my_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    total, holwin, rex = get_user_stats(update.effective_user.id)
    msg = (
        "📋 *Your Registrations*\n\n"
        f"👤 Total: `{total}`\n"
        f"🏠 Holwin: `{holwin}`\n"
        f"📈 Rexproearn: `{rex}`"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)


# ─────────────────────────── Referral QR ───────────────────────────

async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    bot_username = context.bot_data.get("bot_username")
    if not bot_username:
        me = await context.bot.get_me()
        bot_username = me.username
        context.bot_data["bot_username"] = bot_username

    link = f"https://t.me/{bot_username}"
    caption = (
        "🔗 *Your Referral Link*\n\n"
        f"`{esc(link)}`\n\n"
        "Share this link or QR code \\- anyone who opens it lands on this bot's menu\\."
    )

    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    if QR_AVAILABLE:
        img = qrcode.make(link)
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        bio.name = "referral_qr.png"
        await target.reply_photo(
            photo=InputFile(bio),
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=back_keyboard(),
        )
    else:
        await target.reply_text(
            caption + "\n\n⚠️ QR image unavailable \\- install `qrcode[pil]` on the server\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=back_keyboard(),
        )


# ─────────────────────────── Code snippet library (no execution) ───────────────────────────

CODE_SNIPPETS = {
    "python": {
        "read file": "with open('file.txt', 'r') as f:\n    content = f.read()\nprint(content)",
        "sort list": "nums = [5, 2, 9, 1]\nnums.sort()\nprint(nums)  # [1, 2, 5, 9]",
        "http request": "import requests\nresp = requests.get('https://api.example.com/data')\nprint(resp.json())",
        "read csv": "import csv\nwith open('data.csv') as f:\n    for row in csv.reader(f):\n        print(row)",
        "loop dict": "d = {'a': 1, 'b': 2}\nfor key, value in d.items():\n    print(key, value)",
    },
    "java": {
        "read file": "import java.nio.file.*;\nString content = Files.readString(Path.of(\"file.txt\"));\nSystem.out.println(content);",
        "sort list": "List<Integer> nums = new ArrayList<>(List.of(5, 2, 9, 1));\nCollections.sort(nums);\nSystem.out.println(nums);",
        "http request": "HttpClient client = HttpClient.newHttpClient();\nHttpRequest req = HttpRequest.newBuilder(URI.create(\"https://api.example.com/data\")).build();\nHttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());\nSystem.out.println(resp.body());",
        "read csv": "try (BufferedReader br = new BufferedReader(new FileReader(\"data.csv\"))) {\n    String line;\n    while ((line = br.readLine()) != null) {\n        System.out.println(Arrays.toString(line.split(\",\")));\n    }\n}",
        "loop dict": "Map<String, Integer> map = Map.of(\"a\", 1, \"b\", 2);\nfor (Map.Entry<String, Integer> e : map.entrySet()) {\n    System.out.println(e.getKey() + \" \" + e.getValue());\n}",
    },
}

FALLBACK_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 types of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?'",
]
FALLBACK_QUOTES = [
    "The only way to do great work is to love what you do. - Steve Jobs",
    "Success is not final, failure is not fatal: courage to continue counts. - Winston Churchill",
    "Simplicity is the soul of efficiency. - Austin Freeman",
]


# ─────────────────────────── Support / FAQ ───────────────────────────

FAQ = [
    (("otp", "code not"), "If OTP isn't arriving: check the number is correct, wait 60s, then use 🔄 Resend OTP. Some carriers delay SMS by a few minutes."),
    (("password", "pwd"), "Password must be 6+ characters, or type `skip` to use a default one for the platform."),
    (("fail", "error", "not working"), "If registration fails, the platform usually returns a reason in the error message. Common causes: number already registered, wrong OTP, or the platform is temporarily down."),
    (("referral", "link", "qr"), "Use /referral to get your shareable link and QR code."),
    (("language", "hindi", "भाषा"), "Use /language to switch between English and Hindi."),
]


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    text = (
        "🆘 *Quick Support*\n\n"
        "Type a keyword \\(e\\.g\\. `otp`, `password`, `error`\\) after /support, "
        "or just ask your question as a normal message and I'll try to match it to an FAQ\\.\n\n"
        "For anything else, an admin will need to help \\- this bot doesn't have a live AI agent connected yet\\."
    )
    args = context.args if hasattr(context, "args") else []
    if args:
        answer = match_faq(" ".join(args))
        if answer:
            text = f"🆘 {esc(answer)}"
        else:
            text = "🤔 No FAQ match found\\. Try /support with a different keyword, or ask an admin\\."

    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
    await target.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=back_keyboard())


def match_faq(query: str) -> Optional[str]:
    q = query.lower()
    for keywords, answer in FAQ:
        if any(kw in q for kw in keywords):
            return answer
    return None



# ─────────────────────────── Calculator / Converter / Currency handlers ───────────────────────────

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if not context.args:
        await update.message.reply_text(
            "🧮 Usage: `/calc 2 + 2 * (3/6) ** 2`\nSupports: + - * / // % ** and sqrt, sin, cos, tan, log, floor, ceil, factorial, pi, e",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    expr = " ".join(context.args)
    try:
        result = safe_calc(expr)
        await update.message.reply_text(f"🧮 `{esc(expr)}` = `{esc(result)}`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception:
        await update.message.reply_text("❌ Couldn't evaluate that expression. Check the syntax and try again.")


async def convert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if len(context.args) != 3:
        await update.message.reply_text(
            "🔄 Usage: `/convert 10 km mile`\nUnits - length: m km cm mm mile ft in yd | weight: kg g mg lb oz | temp: c f k",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    try:
        value = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ First argument must be a number.")
        return
    result = convert_units(value, context.args[1], context.args[2])
    if result is None:
        await update.message.reply_text("❌ Unknown or mismatched units.")
        return
    await update.message.reply_text(f"🔄 {value} {context.args[1]} = *{round(result, 4)}* {context.args[2]}", parse_mode=ParseMode.MARKDOWN_V2)


async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if len(context.args) != 3:
        await update.message.reply_text("💱 Usage: `/currency 100 USD INR`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ First argument must be a number.")
        return
    base, target = context.args[1].upper(), context.args[2].upper()
    async with aiohttp.ClientSession() as session:
        rates = await get_currency_rate(session, base)
    if not rates or target not in rates:
        await update.message.reply_text("❌ Couldn't fetch that exchange rate right now.")
        return
    converted = amount * rates[target]
    await update.message.reply_text(f"💱 {amount} {base} = *{round(converted, 2)}* {target}", parse_mode=ParseMode.MARKDOWN_V2)


# ─────────────────────────── Weather / News ───────────────────────────

async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if not context.args:
        await update.message.reply_text("☁️ Usage: `/weather Mumbai`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    city = " ".join(context.args)
    url = f"https://wttr.in/{quote(city)}?format=3"
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "curl/8.0"}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                text = (await resp.text()).strip()
        await update.message.reply_text(f"☁️ {text}")
    except Exception as e:
        logger.warning(f"Weather fetch error: {e}")
        await update.message.reply_text("❌ Couldn't fetch weather right now.")


async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    topic = " ".join(context.args) if context.args else "top stories"
    url = f"https://news.google.com/rss/search?q={quote(topic)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                raw = await resp.text()
        root = ET.fromstring(raw)
        items = root.findall(".//item")[:5]
        if not items:
            await update.message.reply_text("❌ No news found for that topic.")
            return
        lines = [f"📰 *Top headlines: {esc(topic)}*\n"]
        for item in items:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            lines.append(f"• {esc(title)}\n  {esc(link)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"News fetch error: {e}")
        await update.message.reply_text("❌ Couldn't fetch news right now.")


# ─────────────────────────── Notes ───────────────────────────

async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("📝 Usage: `/note add <text>` | `/note list` | `/note del <id>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    sub = context.args[0].lower()
    db = db_session()
    try:
        if sub == "add":
            text = " ".join(context.args[1:])
            if not text:
                await update.message.reply_text("❌ Note text can't be empty.")
                return
            db.add(Note(telegram_id=uid, text=text))
            db.commit()
            await update.message.reply_text("✅ Note saved.")
        elif sub == "list":
            notes = db.query(Note).filter(Note.telegram_id == uid).order_by(Note.created_at.desc()).all()
            if not notes:
                await update.message.reply_text("📝 You have no notes yet.")
                return
            lines = [f"`{n.id}` {esc(n.text)}" for n in notes]
            await update.message.reply_text("📝 *Your notes:*\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        elif sub == "del" and len(context.args) > 1 and context.args[1].isdigit():
            note = db.query(Note).filter(Note.id == int(context.args[1]), Note.telegram_id == uid).first()
            if note:
                db.delete(note)
                db.commit()
                await update.message.reply_text("🗑️ Note deleted.")
            else:
                await update.message.reply_text("❌ Note not found.")
        else:
            await update.message.reply_text("❌ Usage: `/note add <text>` | `/note list` | `/note del <id>`", parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        db.close()


# ─────────────────────────── To-Do ───────────────────────────

async def todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("✅ Usage: `/todo add <text>` | `/todo list` | `/todo done <id>` | `/todo del <id>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    sub = context.args[0].lower()
    db = db_session()
    try:
        if sub == "add":
            text = " ".join(context.args[1:])
            if not text:
                await update.message.reply_text("❌ Task text can't be empty.")
                return
            db.add(TodoItem(telegram_id=uid, text=text))
            db.commit()
            await update.message.reply_text("✅ Task added.")
        elif sub == "list":
            tasks = db.query(TodoItem).filter(TodoItem.telegram_id == uid).order_by(TodoItem.created_at.desc()).all()
            if not tasks:
                await update.message.reply_text("✅ Your to-do list is empty.")
                return
            lines = [f"{'✔️' if t.done else '⬜'} `{t.id}` {esc(t.text)}" for t in tasks]
            await update.message.reply_text("✅ *Your to-do list:*\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        elif sub == "done" and len(context.args) > 1 and context.args[1].isdigit():
            task = db.query(TodoItem).filter(TodoItem.id == int(context.args[1]), TodoItem.telegram_id == uid).first()
            if task:
                task.done = True
                db.commit()
                await update.message.reply_text("✔️ Marked as done.")
            else:
                await update.message.reply_text("❌ Task not found.")
        elif sub == "del" and len(context.args) > 1 and context.args[1].isdigit():
            task = db.query(TodoItem).filter(TodoItem.id == int(context.args[1]), TodoItem.telegram_id == uid).first()
            if task:
                db.delete(task)
                db.commit()
                await update.message.reply_text("🗑️ Task deleted.")
            else:
                await update.message.reply_text("❌ Task not found.")
        else:
            await update.message.reply_text("❌ Usage: `/todo add <text>` | `/todo list` | `/todo done <id>` | `/todo del <id>`", parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        db.close()


# ─────────────────────────── Reminders (in-memory, lost on restart) ───────────────────────────

async def _reminder_fire(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=f"⏰ Reminder: {job.data}")


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("⏰ Usage: `/remind <minutes> <text>`  e.g. `/remind 30 drink water`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    minutes = int(context.args[0])
    text = " ".join(context.args[1:])
    if context.job_queue is None:
        await update.message.reply_text("⚠️ Reminders need the job-queue extra: pip install \"python-telegram-bot[job-queue]\"")
        return
    context.job_queue.run_once(_reminder_fire, when=minutes * 60, chat_id=update.effective_chat.id, data=text)
    await update.message.reply_text(f"⏰ Okay, I'll remind you in {minutes} minute(s): \"{text}\"")


# ─────────────────────────── Fun: jokes / quotes / guess game ───────────────────────────

async def joke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://official-joke-api.appspot.com/random_joke", timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None)
        text = f"{data['setup']}\n\n{data['punchline']}"
    except Exception:
        text = random.choice(FALLBACK_JOKES)
    await update.message.reply_text(f"😂 {text}")


async def quote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.quotable.io/random", timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None)
        text = f"{data['content']} - {data['author']}"
    except Exception:
        text = random.choice(FALLBACK_QUOTES)
    await update.message.reply_text(f"💬 {text}")


async def guess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    context.user_data["guess_target"] = random.randint(1, 50)
    context.user_data["guess_tries"] = 0
    await update.message.reply_text("🎯 I'm thinking of a number between 1 and 50. Just type your guess!")


# ─────────────────────────── Code snippets ───────────────────────────

async def snippet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if len(context.args) < 2 or context.args[0].lower() not in CODE_SNIPPETS:
        topics = ", ".join(sorted(next(iter(CODE_SNIPPETS.values())).keys()))
        await update.message.reply_text(
            f"💻 Usage: `/snippet python read file` or `/snippet java sort list`\nTopics: {topics}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    lang = context.args[0].lower()
    topic = " ".join(context.args[1:]).lower()
    code = CODE_SNIPPETS[lang].get(topic)
    if not code:
        topics = ", ".join(sorted(CODE_SNIPPETS[lang].keys()))
        await update.message.reply_text(f"❌ No snippet for that topic. Available: {topics}")
        return
    await update.message.reply_text(f"💻 *{esc(lang)} - {esc(topic)}*\n```{lang}\n{code}\n```", parse_mode=ParseMode.MARKDOWN_V2)


# ─────────────────────────── Image → PDF ───────────────────────────

async def photo_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collects incoming photos into a per-user buffer for later PDF conversion."""
    if not PIL_AVAILABLE:
        await update.message.reply_text("⚠️ Image-to-PDF needs Pillow installed: pip install pillow")
        return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(out=buf)
    buf.seek(0)
    context.user_data.setdefault("pending_images", []).append(buf)
    count = len(context.user_data["pending_images"])
    await update.message.reply_text(f"📸 Image added ({count} queued). Send more, or run /makepdf to combine them.")


async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await touch_user_and_check_ban(update)
    if not PIL_AVAILABLE:
        await update.message.reply_text("⚠️ Image-to-PDF needs Pillow installed: pip install pillow")
        return
    images = context.user_data.get("pending_images", [])
    if not images:
        await update.message.reply_text("📄 No images queued yet. Send me photos first, then run /makepdf.")
        return
    pil_images = [Image.open(b).convert("RGB") for b in images]
    out = io.BytesIO()
    pil_images[0].save(out, format="PDF", save_all=True, append_images=pil_images[1:])
    out.seek(0)
    out.name = f"images_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    await update.message.reply_document(document=InputFile(out, filename=out.name), caption="📄 Here's your PDF.")
    context.user_data["pending_images"] = []


async def clearimages_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pending_images"] = []
    await update.message.reply_text("🗑️ Cleared queued images.")


# ─────────────────────────── Admin: broadcast / users / export ───────────────────────────

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    text = " ".join(context.args)
    ids = get_all_user_ids()
    sent, failed = 0, 0
    for uid in ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users. Failed: {failed}.")


async def admin_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    db = db_session()
    try:
        total = db.query(func.count(BotUser.telegram_id)).scalar() or 0
        banned = db.query(func.count(BotUser.telegram_id)).filter(BotUser.is_banned == True).scalar() or 0  # noqa: E712
    finally:
        db.close()
    await update.message.reply_text(
        f"👥 Total bot users: {total}\n🚫 Banned: {banned}\n\n"
        "Use /ban <telegram_id> or /unban <telegram_id> to manage."
    )


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /ban <telegram_id>")
        return
    ok = set_ban_status(int(context.args[0]), True)
    await update.message.reply_text("✅ User banned." if ok else "❌ User not found.")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban <telegram_id>")
        return
    ok = set_ban_status(int(context.args[0]), False)
    await update.message.reply_text("✅ User unbanned." if ok else "❌ User not found.")


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    bio = export_registrations_csv()
    await update.message.reply_document(document=InputFile(bio, filename=bio.name), caption="📄 Registrations export")


# ─────────────────────────── Scheduled summaries ───────────────────────────

async def send_summary(context: ContextTypes.DEFAULT_TYPE, label: str):
    total, holwin, rex, _ = get_stats()
    text = (
        f"📈 *{label} Summary*\n\n"
        f"👥 Total registrations: `{total}`\n"
        f"🏠 Holwin: `{holwin}`\n"
        f"📈 Rexproearn: `{rex}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.warning(f"Could not send summary to admin {admin_id}: {e}")


async def daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
    await send_summary(context, "Daily")


async def weekly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    await send_summary(context, "Weekly")


# ─────────────────────────── Registration flow ───────────────────────────

async def platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    info = await touch_user_and_check_ban(update)
    if info["is_banned"]:
        await q.edit_message_text(L("banned", info["language"]))
        return ConversationHandler.END

    platform = q.data.split("_")[1]
    context.user_data["platform"] = platform
    context.user_data["invite"] = HOLWIN_INVITE_CODE if platform == "holwin" else REX_INVITE_CODE
    await q.edit_message_text(
        f"✅ Selected: *{esc(platform.upper())}*\n"
        f"Invite: `{esc(context.user_data['invite'])}`\n\n"
        f"{L('enter_mobile', info['language'])}",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=back_keyboard(),
    )
    return MOBILE


async def mobile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = await touch_user_and_check_ban(update)
    mobile = update.message.text.strip()
    if not re.match(r"^\d{10,15}$", mobile):
        await update.message.reply_text(L("invalid_mobile", info["language"]), reply_markup=back_keyboard())
        return MOBILE

    context.user_data["mobile"] = mobile
    platform = context.user_data["platform"]

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})
    except Exception as e:
        logger.error(f"OTP send error: {e}")
        await update.message.reply_text("❌ Failed to send OTP.", reply_markup=back_keyboard())
        return ConversationHandler.END

    ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)
    if not ok:
        await update.message.reply_text(f"❌ OTP request failed: {resp.get('msg', 'Unknown')}", reply_markup=back_keyboard())
        return ConversationHandler.END

    await update.message.reply_text("✅ OTP sent! Enter the OTP:", reply_markup=otp_keyboard())
    return OTP


async def otp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp_code = update.message.text.strip()
    if not otp_code.isdigit():
        await update.message.reply_text("❌ OTP must be numeric. Try again:", reply_markup=otp_keyboard())
        return OTP
    context.user_data["otp"] = otp_code
    await update.message.reply_text("🔑 Set a password, or type `skip`:", parse_mode=ParseMode.MARKDOWN_V2)
    return PASSWORD


async def password_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    platform = context.user_data["platform"]

    if pwd.lower() == "skip":
        pwd = "Dk12345dk" if platform == "rex" else "Password@123"
    elif len(pwd) < 6:
        await update.message.reply_text("❌ Min 6 characters. Try again or type `skip`:")
        return PASSWORD

    context.user_data["password"] = pwd
    mobile = context.user_data["mobile"]
    invite = context.user_data["invite"]
    summary = (
        "📋 *Summary*\n\n"
        f"📱 Mobile: `{esc(mobile)}`\n"
        f"🔑 Password: `{'*' * len(pwd)}`\n"
        f"🎫 Platform: `{esc(platform.upper())}`\n"
        f"🎫 Invite: `{esc(invite)}`\n\n"
        "Confirm?"
    )
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=confirm_keyboard())
    return CONFIRM


async def resend_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Resending OTP...")
    mobile = context.user_data.get("mobile")
    platform = context.user_data.get("platform")
    if not mobile or not platform:
        await q.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})
    except Exception as e:
        logger.error(f"Resend OTP error: {e}")
        await q.edit_message_text("❌ Failed to resend OTP.")
        return ConversationHandler.END

    ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)
    if not ok:
        await q.edit_message_text(f"❌ Resend failed: {resp.get('msg', 'Unknown')}")
        return ConversationHandler.END

    await q.edit_message_text("✅ OTP resent successfully. Enter OTP:", reply_markup=otp_keyboard())
    return OTP


async def change_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✏️ Enter your new mobile number (10-15 digits):", reply_markup=back_keyboard())
    return MOBILE


async def confirm_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    platform = context.user_data.get("platform")
    mobile = context.user_data.get("mobile")
    otp_code = context.user_data.get("otp")
    password = context.user_data.get("password")
    invite = context.user_data.get("invite")

    if not all([platform, mobile, otp_code, password, invite]):
        await q.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                payload = {
                    "mobile": mobile,
                    "authCode": otp_code,
                    "password": password,
                    "inviteCode": invite,
                    "sourceAppType": "lobby",
                    "registerHost": "www.holwin123.top",
                    "sourceUrl": "https://www.hlowin.link/",
                }
                resp = await client.post("/api/user/register", payload)
                success = resp.get("code") == 0
        else:
            async with RexClient() as client:
                payload = {"mobileNo": mobile, "password": password, "smsCode": otp_code, "inviteCode": invite}
                resp = await client.post("/app/user/register", payload)
                success = resp.get("code") == 200
    except Exception as e:
        logger.error(f"Registration error: {e}")
        await q.edit_message_text("❌ Registration failed due to network error.")
        return ConversationHandler.END

    if success:
        try:
            save_registration(mobile, platform, invite, update.effective_user.id)
        except Exception:
            await q.edit_message_text("❌ Registration succeeded but local save failed.")
            return ConversationHandler.END

        await q.edit_message_text(
            "✅ *Registration successful\\!*\n\n"
            f"Platform: {esc(platform.upper())}\n"
            f"Mobile: `{esc(mobile)}`\n"
            f"Invite used: `{esc(invite)}`\n\n"
            "Saved locally\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=back_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    await q.edit_message_text(
        f"❌ Registration failed: `{esc(resp.get('msg', 'Unknown error'))}`",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=back_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Cancelled.")
    else:
        await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


# ─────────────────────────── Wiring ───────────────────────────

conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(platform_selected, pattern="^platform_(holwin|rex)$")],
    states={
        MOBILE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_input),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        OTP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, otp_input),
            CallbackQueryHandler(resend_otp, pattern="^resend_otp$"),
            CallbackQueryHandler(change_mobile, pattern="^change_mobile$"),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        PASSWORD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, password_input),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        CONFIRM: [
            CallbackQueryHandler(confirm_reg, pattern="^confirm_reg$"),
            CallbackQueryHandler(change_mobile, pattern="^change_mobile$"),
            CallbackQueryHandler(cancel, pattern="^cancel_reg$"),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(main_menu, pattern="^main_menu$")],
    allow_reentry=True,
)


REPLY_KEYBOARD_ROUTES = {
    "🎰 Register": start,
    "🧮 Calculator": calc_cmd,
    "🔄 Convert": convert_cmd,
    "💱 Currency": currency_cmd,
    "☁️ Weather": weather_cmd,
    "📰 News": news_cmd,
    "📝 Notes": note_cmd,
    "✅ To-Do": todo_cmd,
    "⏰ Reminder": remind_cmd,
    "😂 Fun": joke_cmd,
    "📄 Image→PDF": makepdf_cmd,
    "💻 Code Snippets": snippet_cmd,
    "🌐 Language": language_cmd,
    "🆘 Support": support_cmd,
}


async def freeform_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles: reply-keyboard button taps, an active number-guess game, then falls back to FAQ matching."""
    text = (update.message.text or "").strip()

    # 1. Persistent reply-keyboard button taps (these arrive as plain text messages)
    handler = REPLY_KEYBOARD_ROUTES.get(text)
    if handler:
        context.args = []
        await handler(update, context)
        return

    # 2. Active number-guessing game takes priority over FAQ matching
    if "guess_target" in context.user_data:
        if text.lstrip("-").isdigit():
            guess = int(text)
            context.user_data["guess_tries"] += 1
            target = context.user_data["guess_target"]
            if guess == target:
                tries = context.user_data.pop("guess_tries")
                context.user_data.pop("guess_target")
                await update.message.reply_text(f"🎉 Correct! It was {target}. You got it in {tries} tries.")
            elif guess < target:
                await update.message.reply_text("📈 Higher!")
            else:
                await update.message.reply_text("📉 Lower!")
        else:
            await update.message.reply_text("🎯 Type a number to keep guessing, or /cancel to stop the game.")
        return

    # 3. FAQ keyword matcher
    answer = match_faq(text)
    if answer:
        await update.message.reply_text(f"🆘 {esc(answer)}", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=back_keyboard())
    # if no match, stay silent so we don't spam replies to random chatter


def main():
    if not BOT_TOKEN or BOT_TOKEN.count(":") != 1:
        raise SystemExit("BOT_TOKEN is missing or malformed. Set it via the BOT_TOKEN environment variable.")
    if not ADMIN_IDS:
        logger.warning("No ADMIN_IDS configured - admin commands (/broadcast, /export, /ban...) will be unusable.")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(False).build()

    # Core
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("my", my_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("language", language_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("support", support_cmd))

    # Daily-life utilities
    app.add_handler(CommandHandler("calc", calc_cmd))
    app.add_handler(CommandHandler("convert", convert_cmd))
    app.add_handler(CommandHandler("currency", currency_cmd))
    app.add_handler(CommandHandler("weather", weather_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("note", note_cmd))
    app.add_handler(CommandHandler("todo", todo_cmd))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("joke", joke_cmd))
    app.add_handler(CommandHandler("quote", quote_cmd))
    app.add_handler(CommandHandler("guess", guess_cmd))
    app.add_handler(CommandHandler("snippet", snippet_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))
    app.add_handler(CommandHandler("clearimages", clearimages_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo_collect))

    # Admin
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("adminusers", admin_users_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("export", export_cmd))

    # Conversation
    app.add_handler(conv_handler)

    # Buttons
    app.add_handler(CallbackQueryHandler(stats_cmd, pattern="^stats_btn$"))
    app.add_handler(CallbackQueryHandler(my_cmd, pattern="^my_btn$"))
    app.add_handler(CallbackQueryHandler(help_cmd, pattern="^help_btn$"))
    app.add_handler(CallbackQueryHandler(referral_cmd, pattern="^referral_btn$"))
    app.add_handler(CallbackQueryHandler(language_cmd, pattern="^lang_btn$"))
    app.add_handler(CallbackQueryHandler(set_language, pattern="^setlang_(en|hi)$"))
    app.add_handler(CallbackQueryHandler(support_cmd, pattern="^support_btn$"))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))

    # Fallback FAQ matcher for free text outside any flow (must be added last / lowest priority)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freeform_text_fallback), group=1)

    # Scheduled summaries (requires: pip install "python-telegram-bot[job-queue]")
    if app.job_queue is not None:
        app.job_queue.run_daily(daily_summary_job, time=dtime(hour=9, minute=0))
        app.job_queue.run_daily(weekly_summary_job, time=dtime(hour=9, minute=15), days=(6,))  # Sunday
    else:
        logger.warning("JobQueue unavailable - install python-telegram-bot[job-queue] for scheduled summaries.")

    app.add_error_handler(error_handler)

    logger.info("Premium bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Stars AVTO bot — bitta faylli pyTelegramBotAPI loyihasi.

O‘rnatish:
    py -m pip install pyTelegramBotAPI

Ishga tushirish:
    py main.py

Muhim:
1) BOT_TOKEN ni BotFather bergan yangi token bilan almashtiring.
2) ADMIN_IDS ichiga o‘zingizning Telegram ID raqamingizni yozing.
3) BOT_USERNAME ni @ belgisiz yozing.
"""

import html
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import telebot
from telebot import types


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")

_admin_ids_raw = os.getenv("ADMIN_IDS", "8261542613")
ADMIN_IDS = {
    int(value.strip())
    for value in _admin_ids_raw.split(",")
    if value.strip().lstrip("-").isdigit()
}

DB_NAME = os.getenv("DB_PATH", "stars_avto_bot.db").strip() or "stars_avto_bot.db"
DEFAULT_STAR_PRICE = 198
DEFAULT_CARD_NUMBER = "9860 0803 9457 0230"
DEFAULT_CARD_OWNER = "S/MAHMUDOVA"

MIN_TOPUP = 1000
MAX_TOPUP = 200_000

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Render Environment bo‘limida kiritilmagan.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS Render Environment bo‘limida kiritilmagan.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("stars_avto")


# =========================================================
# YORDAMCHI FUNKSIYALAR
# =========================================================

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except Exception:
        return str(value)


def esc(value) -> str:
    return html.escape(str(value or ""))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def clean_username(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
        return None
    return value


def safe_send(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception:
        logger.exception("Xabar yuborilmadi: %s", chat_id)
        return None


# =========================================================
# DATABASE
# =========================================================

_db_lock = threading.RLock()


@contextmanager
def db():
    with _db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                referral_stars INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                contest_points INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                price INTEGER NOT NULL,
                unit_amount INTEGER DEFAULT 1,
                min_qty INTEGER DEFAULT 1,
                max_qty INTEGER DEFAULT 1000000,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                item_id INTEGER,
                item_title TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                target TEXT,
                extra TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                refunded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                method TEXT NOT NULL,
                receipt_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'waiting_receipt',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_message_id INTEGER,
                text TEXT,
                file_id TEXT,
                file_type TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                admin_id INTEGER,
                answer_text TEXT,
                created_at TEXT NOT NULL,
                answered_at TEXT
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        defaults = {
            "star_price": str(DEFAULT_STAR_PRICE),
            "card_number": DEFAULT_CARD_NUMBER,
            "card_owner": DEFAULT_CARD_OWNER,
            "maintenance": "0",
            "contest_enabled": "1",
            "contest_title": "Stars AVTO konkursi",
            "contest_end": "Belgilanmagan",
            "support_username": "admin_username",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )

        # Eski bazadagi umumiy SMM xizmatlarini Telegram bo‘limiga ko‘chiramiz.
        conn.execute(
            "UPDATE catalog SET category='smm_telegram' WHERE category='smm'"
        )

        count = conn.execute("SELECT COUNT(*) c FROM catalog").fetchone()["c"]
        if count == 0:
            seed_catalog(conn)


def seed_catalog(conn):
    rows = [
        ("stars", "⭐ 50 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 9900, 50, 50, 50, 10),
        ("stars", "⭐ 75 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 14850, 75, 75, 75, 20),
        ("stars", "⭐ 100 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 19800, 100, 100, 100, 30),
        ("stars", "⭐ 150 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 29700, 150, 150, 150, 40),
        ("stars", "⭐ 200 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 39600, 200, 200, 200, 50),
        ("stars", "⭐ 250 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 49500, 250, 250, 250, 60),
        ("stars", "⭐ 300 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 59400, 300, 300, 300, 70),
        ("stars", "⭐ 350 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 69300, 350, 350, 350, 80),
        ("stars", "⭐ 500 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 99000, 500, 500, 500, 90),
        ("stars", "⭐ 1000 Stars", "Stars 1 daqiqa ichida o‘tkaziladi.", 198000, 1000, 1000, 1000, 100),

        ("gift", "💝 Gift", "Telegram profilingizga yuboriladi.", 2850, 1, 1, 100, 10),
        ("gift", "🧸 Gift", "Telegram profilingizga yuboriladi.", 2850, 1, 1, 100, 20),
        ("gift", "🎁 Gift", "Telegram profilingizga yuboriladi.", 4750, 1, 1, 100, 30),
        ("gift", "🌹 Gift", "Telegram profilingizga yuboriladi.", 4750, 1, 1, 100, 40),
        ("gift", "🎂 Gift", "Telegram profilingizga yuboriladi.", 9500, 1, 1, 100, 50),
        ("gift", "🚀 Gift", "Telegram profilingizga yuboriladi.", 9500, 1, 1, 100, 60),
        ("gift", "💎 Gift", "Telegram profilingizga yuboriladi.", 19000, 1, 1, 100, 70),
        ("gift", "🏆 Gift", "Telegram profilingizga yuboriladi.", 19000, 1, 1, 100, 80),

        ("premium", "🌟 Premium 1 oy", "Telegram Premium obunasi.", 44000, 1, 1, 1, 10),
        ("premium", "🌟 Premium 3 oy", "Admin orqali ulanadi.", 167000, 1, 1, 1, 20),
        ("premium", "🌟 Premium 6 oy", "Telegram Premium obunasi.", 210000, 1, 1, 1, 30),
        ("premium", "🌟 Premium 12 oy", "Telegram Premium obunasi.", 300000, 1, 1, 1, 40),

        ("pubg", "🛡 30 UC", "PUBG Mobile UC.", 6136, 30, 30, 30, 10),
        ("pubg", "🛡 60 UC", "PUBG Mobile UC.", 11949, 60, 60, 60, 20),
        ("pubg", "🛡 120 UC", "PUBG Mobile UC.", 24232, 120, 120, 120, 30),
        ("pubg", "🛡 180 UC", "PUBG Mobile UC.", 36400, 180, 180, 180, 40),
        ("pubg", "🛡 325 UC", "PUBG Mobile UC.", 59592, 325, 325, 325, 50),
        ("pubg", "🛡 355 UC", "PUBG Mobile UC.", 67876, 355, 355, 355, 60),

        ("number", "🇺🇿 UZ raqam", "Telegram uchun virtual raqam.", 10772, 1, 1, 1, 10),
        ("number", "🇧🇩 BD raqam", "Telegram uchun virtual raqam.", 5078, 1, 1, 1, 20),
        ("number", "🇺🇸 US raqam", "Telegram uchun virtual raqam.", 5386, 1, 1, 1, 30),
        ("number", "🇷🇺 RU raqam", "Telegram uchun virtual raqam.", 23083, 1, 1, 1, 40),

        ("smm_telegram", "👀 Telegram prosmotr", "15 daqiqa ichida qo‘shiladi.", 550, 1000, 100, 1000000, 10),
        ("smm_telegram", "❤️ Telegram reaksiya", "30 daqiqa ichida qo‘shiladi.", 880, 1000, 100, 1000000, 20),
        ("smm_telegram", "👤 Tezkor obunachi", "30 kun kafolatli servis.", 6050, 1000, 100, 1000000, 30),
        ("smm_telegram", "🤖 Bot obunachisi", "Bot referral havolasi uchun.", 4400, 1000, 100, 1000000, 40),
    ]

    for row in rows:
        conn.execute(
            """
            INSERT INTO catalog(
                category,title,description,price,unit_amount,min_qty,max_qty,
                sort_order,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (*row, now()),
        )


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_user(user_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def ensure_user(tg_user, referrer_id: Optional[int] = None):
    full_name = " ".join(
        x for x in [tg_user.first_name, tg_user.last_name] if x
    ).strip() or "Foydalanuvchi"

    with db() as conn:
        exists = conn.execute(
            "SELECT user_id FROM users WHERE user_id=?", (tg_user.id,)
        ).fetchone()

        conn.execute(
            """
            INSERT INTO users(
                user_id,username,full_name,referrer_id,created_at,last_seen
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_seen=excluded.last_seen
            """,
            (
                tg_user.id,
                tg_user.username,
                full_name,
                referrer_id if not exists else None,
                now(),
                now(),
            ),
        )

        if not exists and referrer_id and referrer_id != tg_user.id:
            ref = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?", (referrer_id,)
            ).fetchone()
            if ref:
                conn.execute(
                    """
                    UPDATE users
                    SET contest_points=contest_points+1
                    WHERE user_id=?
                    """,
                    (referrer_id,),
                )


def change_balance(user_id: int, amount: int, tx_type: str, comment: str):
    with db() as conn:
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return False
        new_balance = row["balance"] + amount
        if new_balance < 0:
            return False
        conn.execute(
            "UPDATE users SET balance=? WHERE user_id=?",
            (new_balance, user_id),
        )
        conn.execute(
            """
            INSERT INTO transactions(user_id,amount,type,comment,created_at)
            VALUES(?,?,?,?,?)
            """,
            (user_id, amount, tx_type, comment, now()),
        )
    return True


# =========================================================
# FOYDALANUVCHI HOLATLARI
# =========================================================

states = {}


def set_state(user_id: int, name: str, **data):
    states[user_id] = {"name": name, **data}


def get_state(user_id: int):
    return states.get(user_id)


def clear_state(user_id: int):
    states.pop(user_id, None)


# =========================================================
# KLAVIATURALAR
# =========================================================

def main_menu(user_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("🏆 Konkursda ishtirok etish"))
    kb.add(
        types.KeyboardButton("⭐ Stars olish"),
        types.KeyboardButton("💝 Gift olish"),
    )
    kb.add(
        types.KeyboardButton("🌟 Premium olish"),
        types.KeyboardButton("📱 Raqam olish"),
    )
    kb.add(
        types.KeyboardButton("💳 Hisob to‘ldirish"),
        types.KeyboardButton("💸 Stars yechish"),
    )
    kb.add(
        types.KeyboardButton("🌐 SMM xizmatlari"),
        types.KeyboardButton("🎮 PUBG UC olish"),
    )
    kb.add(
        types.KeyboardButton("📦 Buyurtmalarim"),
        types.KeyboardButton("🧑‍💻 Admin bilan aloqa"),
    )
    kb.add(types.KeyboardButton("🔄 Yangilash"))
    if is_admin(user_id):
        kb.add(types.KeyboardButton("⚙️ Admin panel"))
    return kb


def back_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("⬅️ Bosh menyu"))
    return kb


def cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🚫 Bekor qilish"))
    return kb


def inline_back(callback="main"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data=callback))
    return kb


# =========================================================
# ASOSIY EKRAN
# =========================================================

def send_home(chat_id: int, user_id: int):
    user = get_user(user_id)
    if not user:
        return

    text = (
        "💠 <b>Eng tezkor va ishonchli xizmatlardan foydalaning</b>\n\n"
        f"🔎 ID: <code>{user_id}</code>\n"
        f"💰 Hisobingiz: <b>{money(user['balance'])} so‘m</b>\n"
        f"🪙 Referal bonus: <b>{user['referral_stars']} Stars</b>\n"
        f"🏆 Konkurs ball: <b>{user['contest_points']} ball</b>"
    )
    safe_send(chat_id, text, reply_markup=main_menu(user_id))


def catalog_keyboard(category: str):
    with db() as conn:
        items = conn.execute(
            """
            SELECT * FROM catalog
            WHERE category=? AND active=1
            ORDER BY sort_order,id
            """,
            (category,),
        ).fetchall()

    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for item in items:
        buttons.append(
            types.InlineKeyboardButton(
                f"{item['title']} — {money(item['price'])} so‘m",
                callback_data=f"item:{item['id']}",
            )
        )
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    if category.startswith("smm_"):
        kb.add(types.InlineKeyboardButton("⬅️ SMM bo‘limlari", callback_data="smm_platforms"))
    else:
        kb.add(types.InlineKeyboardButton("⬅️ Bosh menyu", callback_data="main"))
    return kb


def send_catalog(chat_id: int, category: str):
    titles = {
        "stars": "⭐ <b>Qancha Stars olasiz?</b>\n\nKerakli miqdorni tanlang.",
        "gift": "💝 <b>Qanday Gift olmoqchisiz?</b>\n\nGift turini tanlang.",
        "premium": "🌟 <b>Telegram Premium</b>\n\nKerakli muddatni tanlang.",
        "pubg": "🎮 <b>PUBG Mobile UC</b>\n\nUC paketini tanlang.",
        "number": "📱 <b>Virtual raqamlar</b>\n\nDavlatni tanlang.",
        "smm_instagram": "📸 <b>Instagram xizmatlari</b>\n\nKerakli xizmatni tanlang.",
        "smm_telegram": "✈️ <b>Telegram xizmatlari</b>\n\nKerakli xizmatni tanlang.",
        "smm_youtube": "▶️ <b>YouTube xizmatlari</b>\n\nKerakli xizmatni tanlang.",
        "smm_tiktok": "🎵 <b>TikTok xizmatlari</b>\n\nKerakli xizmatni tanlang.",
        "smm_facebook": "📘 <b>Facebook xizmatlari</b>\n\nKerakli xizmatni tanlang.",
    }
    safe_send(
        chat_id,
        titles[category],
        reply_markup=catalog_keyboard(category),
    )


# =========================================================
# START VA ODDIY XABARLAR
# =========================================================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    referrer_id = None
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1]
        if payload.startswith("ball_"):
            try:
                referrer_id = int(payload.replace("ball_", "", 1))
            except ValueError:
                referrer_id = None

    ensure_user(message.from_user, referrer_id)
    user = get_user(message.from_user.id)

    if user["blocked"]:
        safe_send(message.chat.id, "⛔ Siz botdan foydalanishdan bloklangansiz.")
        return

    if get_setting("maintenance") == "1" and not is_admin(message.from_user.id):
        safe_send(message.chat.id, "🛠 Bot vaqtincha texnik rejimda.")
        return

    send_home(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["id"])
def cmd_id(message):
    ensure_user(message.from_user)
    safe_send(message.chat.id, f"Sizning ID: <code>{message.from_user.id}</code>")


@bot.message_handler(func=lambda m: m.text == "⬅️ Bosh menyu")
def back_home(message):
    clear_state(message.from_user.id)
    send_home(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda m: m.text in ("🔄 Yangilash", "🚫 Bekor qilish"))
def refresh(message):
    clear_state(message.from_user.id)
    safe_send(message.chat.id, "Yangilandi 🟢")
    send_home(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda m: m.text == "⭐ Stars olish")
def stars_menu(message):
    send_catalog(message.chat.id, "stars")


@bot.message_handler(func=lambda m: m.text == "💝 Gift olish")
def gift_menu(message):
    send_catalog(message.chat.id, "gift")


@bot.message_handler(func=lambda m: m.text == "🌟 Premium olish")
def premium_menu(message):
    send_catalog(message.chat.id, "premium")


@bot.message_handler(func=lambda m: m.text == "📱 Raqam olish")
def number_menu(message):
    send_catalog(message.chat.id, "number")


def smm_platform_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("📸 Instagram", callback_data="smm_cat:smm_instagram"),
        types.InlineKeyboardButton("✈️ Telegram", callback_data="smm_cat:smm_telegram"),
    )
    kb.row(
        types.InlineKeyboardButton("▶️ YouTube", callback_data="smm_cat:smm_youtube"),
        types.InlineKeyboardButton("🎵 TikTok", callback_data="smm_cat:smm_tiktok"),
    )
    kb.add(
        types.InlineKeyboardButton("📘 Facebook", callback_data="smm_cat:smm_facebook")
    )
    kb.add(types.InlineKeyboardButton("⬅️ Bosh menyu", callback_data="main"))
    return kb


def send_smm_platforms(chat_id: int):
    safe_send(
        chat_id,
        "🌐 <b>SMM xizmatlari</b>\n\nKerakli platformani tanlang.",
        reply_markup=smm_platform_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "🌐 SMM xizmatlari")
def smm_menu(message):
    send_smm_platforms(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "smm_platforms")
def smm_platforms_callback(call):
    bot.answer_callback_query(call.id)
    send_smm_platforms(call.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("smm_cat:"))
def smm_category_callback(call):
    category = call.data.split(":", 1)[1]
    allowed = {
        "smm_instagram",
        "smm_telegram",
        "smm_youtube",
        "smm_tiktok",
        "smm_facebook",
    }
    if category not in allowed:
        bot.answer_callback_query(call.id, "Bo‘lim topilmadi.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    send_catalog(call.message.chat.id, category)


@bot.message_handler(func=lambda m: m.text == "🎮 PUBG UC olish")
def pubg_menu(message):
    send_catalog(message.chat.id, "pubg")


# =========================================================
# BUYURTMA OQIMI
# =========================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("item:"))
def choose_item(call):
    item_id = int(call.data.split(":")[1])
    with db() as conn:
        item = conn.execute(
            "SELECT * FROM catalog WHERE id=? AND active=1",
            (item_id,),
        ).fetchone()

    if not item:
        bot.answer_callback_query(call.id, "Xizmat topilmadi.", show_alert=True)
        return

    set_state(
        call.from_user.id,
        "await_target",
        item_id=item_id,
        category=item["category"],
    )

    prompts = {
        "stars": "👤 Qaysi profilga olasiz?\n\nUsername’ni @ bilan yozing.",
        "gift": "👤 Gift qaysi profilga yuborilsin?\n\nUsername’ni @ bilan yozing.",
        "premium": "👤 Premium qaysi profilga ulanadi?\n\nUsername’ni @ bilan yozing.",
        "pubg": "🎮 PUBG Player ID yoki kerakli hisob ma’lumotini yozing.",
        "number": "📱 Raqam uchun buyurtmani tasdiqlashga o‘tamiz.\n\n<code>DAVOM</code> deb yozing.",
        "smm_instagram": "🔗 Instagram profil, post yoki video havolasini yuboring.",
        "smm_telegram": "🔗 Telegram kanal, guruh, bot yoki post havolasini yuboring.",
        "smm_youtube": "🔗 YouTube kanal yoki video havolasini yuboring.",
        "smm_tiktok": "🔗 TikTok profil yoki video havolasini yuboring.",
        "smm_facebook": "🔗 Facebook sahifa, profil yoki post havolasini yuboring.",
    }

    bot.answer_callback_query(call.id)
    safe_send(
        call.message.chat.id,
        f"{prompts[item['category']]}\n\n🚫 Bekor qilish mumkin.",
        reply_markup=cancel_kb(),
    )


def create_order_preview(message, item, target: str, quantity: int):
    if item["unit_amount"] <= 0:
        total = item["price"] * quantity
    else:
        total = round(item["price"] * quantity / item["unit_amount"])

    set_state(
        message.from_user.id,
        "await_order_confirm",
        item_id=item["id"],
        target=target,
        quantity=quantity,
        total=total,
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "✅ Tasdiqlayman",
            callback_data="order_confirm",
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data="order_cancel",
        )
    )

    text = (
        "🧾 <b>Buyurtmangiz</b>\n\n"
        f"📦 Xizmat: <b>{esc(item['title'])}</b>\n"
        f"🎯 Manzil: <code>{esc(target)}</code>\n"
        f"🔢 Miqdor: <b>{quantity}</b>\n"
        f"💳 Narx: <b>{money(total)} so‘m</b>\n\n"
        "<b>Buyurtmani tasdiqlaysizmi?</b>"
    )
    safe_send(message.chat.id, text, reply_markup=kb)


@bot.message_handler(
    func=lambda m: get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "await_target"
)
def receive_target(message):
    state = get_state(message.from_user.id)
    with db() as conn:
        item = conn.execute(
            "SELECT * FROM catalog WHERE id=?",
            (state["item_id"],),
        ).fetchone()

    category = item["category"]

    if category in ("stars", "gift", "premium"):
        target = clean_username(message.text)
        if not target:
            safe_send(
                message.chat.id,
                "❌ Username noto‘g‘ri. Masalan: <code>@behruz_00007</code>",
            )
            return
    else:
        target = (message.text or "").strip()
        if len(target) < 3:
            safe_send(message.chat.id, "❌ Ma’lumot juda qisqa.")
            return

    if category.startswith("smm_"):
        set_state(
            message.from_user.id,
            "await_quantity",
            item_id=item["id"],
            target=target,
        )
        safe_send(
            message.chat.id,
            f"❓ Qancha miqdor kerak?\n"
            f"Min: <b>{item['min_qty']}</b>, Max: <b>{item['max_qty']}</b>",
            reply_markup=cancel_kb(),
        )
        return

    create_order_preview(message, item, target, item["unit_amount"])


@bot.message_handler(
    func=lambda m: get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "await_quantity"
)
def receive_quantity(message):
    state = get_state(message.from_user.id)
    try:
        quantity = int((message.text or "").replace(" ", ""))
    except ValueError:
        safe_send(message.chat.id, "Faqat son kiriting.")
        return

    with db() as conn:
        item = conn.execute(
            "SELECT * FROM catalog WHERE id=?",
            (state["item_id"],),
        ).fetchone()

    if not item["min_qty"] <= quantity <= item["max_qty"]:
        safe_send(
            message.chat.id,
            f"Miqdor {item['min_qty']} dan {item['max_qty']} gacha bo‘lsin.",
        )
        return

    create_order_preview(message, item, state["target"], quantity)


@bot.callback_query_handler(func=lambda c: c.data in ("order_confirm", "order_cancel"))
def confirm_order(call):
    if call.data == "order_cancel":
        clear_state(call.from_user.id)
        bot.answer_callback_query(call.id, "Bekor qilindi.")
        safe_send(call.message.chat.id, "❌ Buyurtma bekor qilindi.")
        send_home(call.message.chat.id, call.from_user.id)
        return

    state = get_state(call.from_user.id)
    if not state or state["name"] != "await_order_confirm":
        bot.answer_callback_query(call.id, "Buyurtma eskirgan.", show_alert=True)
        return

    with db() as conn:
        item = conn.execute(
            "SELECT * FROM catalog WHERE id=?",
            (state["item_id"],),
        ).fetchone()
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (call.from_user.id,),
        ).fetchone()

    if user["balance"] < state["total"]:
        bot.answer_callback_query(
            call.id,
            "Hisobingizda mablag‘ yetarli emas.",
            show_alert=True,
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton(
                "💳 Hisob to‘ldirish",
                callback_data="topup",
            )
        )
        safe_send(
            call.message.chat.id,
            "❌ Hisobingizda mablag‘ yetarli emas. Hisobingizni to‘ldirib qayta buyurtma bering.",
            reply_markup=kb,
        )
        return

    if not change_balance(
        call.from_user.id,
        -state["total"],
        "order_payment",
        item["title"],
    ):
        bot.answer_callback_query(call.id, "Balans xatosi.", show_alert=True)
        return

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders(
                user_id,category,item_id,item_title,quantity,unit_price,
                total_price,target,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                call.from_user.id,
                item["category"],
                item["id"],
                item["title"],
                state["quantity"],
                item["price"],
                state["total"],
                state["target"],
                "pending",
                now(),
                now(),
            ),
        )
        order_id = cur.lastrowid

    clear_state(call.from_user.id)
    bot.answer_callback_query(call.id, "Buyurtma qabul qilindi.")

    safe_send(
        call.message.chat.id,
        f"✅ Buyurtma qabul qilindi.\n"
        f"🧾 Buyurtma ID: <code>#{order_id}</code>\n"
        f"⏳ Holat: tekshirilmoqda.",
        reply_markup=main_menu(call.from_user.id),
    )

    notify_admins_new_order(order_id)


def notify_admins_new_order(order_id: int):
    with db() as conn:
        order = conn.execute(
            """
            SELECT o.*,u.username,u.full_name
            FROM orders o
            JOIN users u ON u.user_id=o.user_id
            WHERE o.id=?
            """,
            (order_id,),
        ).fetchone()

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(
            "✅ Bajarildi",
            callback_data=f"admin_order_done:{order_id}",
        ),
        types.InlineKeyboardButton(
            "❌ Bekor",
            callback_data=f"admin_order_cancel:{order_id}",
        ),
    )
    kb.add(
        types.InlineKeyboardButton(
            "💬 Javob berish",
            callback_data=f"admin_reply_user:{order['user_id']}",
        )
    )

    text = (
        "🆕 <b>Yangi buyurtma</b>\n\n"
        f"🧾 ID: <code>#{order['id']}</code>\n"
        f"👤 User: {esc(order['full_name'])}\n"
        f"🔎 ID: <code>{order['user_id']}</code>\n"
        f"📦 Xizmat: <b>{esc(order['item_title'])}</b>\n"
        f"🔢 Miqdor: <b>{order['quantity']}</b>\n"
        f"🎯 Manzil: <code>{esc(order['target'])}</code>\n"
        f"💳 Narx: <b>{money(order['total_price'])} so‘m</b>"
    )

    for admin_id in ADMIN_IDS:
        safe_send(admin_id, text, reply_markup=kb)


# =========================================================
# HISOB TO‘LDIRISH
# =========================================================

@bot.message_handler(func=lambda m: m.text == "💳 Hisob to‘ldirish")
def topup_menu(message):
    show_topup_methods(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "topup")
def topup_callback(call):
    bot.answer_callback_query(call.id)
    show_topup_methods(call.message.chat.id)


def show_topup_methods(chat_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "💳 Karta orqali to‘ldirish",
            callback_data="topup_manual",
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🧑‍💻 Admin orqali to‘ldirish",
            callback_data="topup_admin",
        )
    )
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="main"))

    safe_send(
        chat_id,
        "💳 <b>Qaysi usulda hisob to‘ldirasiz?</b>\n\n"
        "Karta orqali to‘lovda chek admin tomonidan tasdiqlanadi.",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data in ("topup_manual", "topup_admin"))
def choose_topup(call):
    method = call.data
    set_state(call.from_user.id, "await_topup_amount", method=method)
    bot.answer_callback_query(call.id)
    safe_send(
        call.message.chat.id,
        f"💰 Hisobingizni qancha miqdorga to‘ldirmoqchisiz?\n"
        f"Min: {money(MIN_TOPUP)}, Max: {money(MAX_TOPUP)} so‘m",
        reply_markup=cancel_kb(),
    )


@bot.message_handler(
    func=lambda m: get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "await_topup_amount"
)
def receive_topup_amount(message):
    state = get_state(message.from_user.id)
    try:
        amount = int((message.text or "").replace(" ", ""))
    except ValueError:
        safe_send(message.chat.id, "Faqat son kiriting.")
        return

    if not MIN_TOPUP <= amount <= MAX_TOPUP:
        safe_send(
            message.chat.id,
            f"Miqdor {money(MIN_TOPUP)} dan {money(MAX_TOPUP)} so‘mgacha bo‘lsin.",
        )
        return

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO payments(user_id,amount,method,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                message.from_user.id,
                amount,
                state["method"],
                "waiting_receipt",
                now(),
                now(),
            ),
        )
        payment_id = cur.lastrowid

    set_state(
        message.from_user.id,
        "await_receipt",
        payment_id=payment_id,
        amount=amount,
    )

    card = get_setting("card_number")
    owner = get_setting("card_owner")

    safe_send(
        message.chat.id,
        f"💳 <code>{esc(card)}</code>\n"
        f"👤 <b>{esc(owner)}</b>\n\n"
        f"Ushbu kartaga <b>{money(amount)} so‘m</b> o‘tkazing.\n"
        "So‘ng to‘lov chekini rasm yoki fayl ko‘rinishida yuboring.\n\n"
        "⏳ Kutish vaqti: 10 daqiqa.",
        reply_markup=cancel_kb(),
    )


@bot.message_handler(
    content_types=["photo", "document"],
    func=lambda m: get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "await_receipt",
)
def receive_receipt(message):
    state = get_state(message.from_user.id)

    if message.photo:
        file_id = message.photo[-1].file_id
    else:
        file_id = message.document.file_id

    with db() as conn:
        conn.execute(
            """
            UPDATE payments
            SET receipt_file_id=?,status='pending',updated_at=?
            WHERE id=?
            """,
            (file_id, now(), state["payment_id"]),
        )

    clear_state(message.from_user.id)

    safe_send(
        message.chat.id,
        "✅ Chek qabul qilindi. Admin tasdiqlashini kuting.",
        reply_markup=main_menu(message.from_user.id),
    )

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(
            "✅ Tasdiqlash",
            callback_data=f"pay_ok:{state['payment_id']}",
        ),
        types.InlineKeyboardButton(
            "❌ Rad etish",
            callback_data=f"pay_no:{state['payment_id']}",
        ),
    )
    kb.add(
        types.InlineKeyboardButton(
            "💬 Javob berish",
            callback_data=f"admin_reply_user:{message.from_user.id}",
        )
    )

    caption = (
        "💳 <b>Yangi to‘lov cheki</b>\n\n"
        f"To‘lov ID: <code>#{state['payment_id']}</code>\n"
        f"User ID: <code>{message.from_user.id}</code>\n"
        f"Miqdor: <b>{money(state['amount'])} so‘m</b>"
    )

    for admin_id in ADMIN_IDS:
        try:
            if message.photo:
                bot.send_photo(admin_id, file_id, caption=caption, reply_markup=kb)
            else:
                bot.send_document(admin_id, file_id, caption=caption, reply_markup=kb)
        except Exception:
            logger.exception("Chek adminga yuborilmadi")


# =========================================================
# KONKURS
# =========================================================

@bot.message_handler(func=lambda m: m.text == "🏆 Konkursda ishtirok etish")
def contest(message):
    user = get_user(message.from_user.id)
    link = f"https://t.me/{BOT_USERNAME}?start=ball_{message.from_user.id}"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Reytingni ko‘rish", callback_data="contest_rating"),
        types.InlineKeyboardButton("ℹ️ Konkurs ma’lumoti", callback_data="contest_info"),
    )

    safe_send(
        message.chat.id,
        "✨ <b>Konkurs bo‘limiga xush kelibsiz!</b>\n\n"
        f"👤 Sizning ID: <code>{message.from_user.id}</code>\n"
        f"🏆 Hozirgi balingiz: <b>{user['contest_points']} ball</b>\n\n"
        f"🔗 Sizning konkurs havolangiz:\n<code>{link}</code>\n\n"
        "Ushbu havola orqali kirgan har bir yangi foydalanuvchi uchun "
        "sizga avtomatik +1 ball qo‘shiladi.",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "contest_rating")
def contest_rating(call):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT user_id,full_name,contest_points
            FROM users
            WHERE contest_points>0
            ORDER BY contest_points DESC,user_id ASC
            LIMIT 20
            """
        ).fetchall()

    text = "🏆 <b>KONKURS REYTINGI</b>\n\n"
    if not rows:
        text += "Hozircha ball to‘plagan foydalanuvchi yo‘q."
    else:
        for i, row in enumerate(rows, 1):
            text += (
                f"{i}. {esc(row['full_name'])}\n"
                f"   ID: <code>{row['user_id']}</code> — "
                f"<b>{row['contest_points']} ball</b>\n\n"
            )

    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, text, reply_markup=inline_back("main"))


@bot.callback_query_handler(func=lambda c: c.data == "contest_info")
def contest_info(call):
    bot.answer_callback_query(call.id)
    safe_send(
        call.message.chat.id,
        "ℹ️ <b>Konkurs haqida</b>\n\n"
        "Shaxsiy havolangizni do‘stlaringizga yuboring. "
        "Havola orqali botga birinchi marta kirgan foydalanuvchi uchun +1 ball beriladi.\n\n"
        f"📅 Yakunlanish vaqti: <b>{esc(get_setting('contest_end'))}</b>",
        reply_markup=inline_back("main"),
    )


# =========================================================
# BUYURTMALARIM
# =========================================================

@bot.message_handler(func=lambda m: m.text == "📦 Buyurtmalarim")
def my_orders(message):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 15
            """,
            (message.from_user.id,),
        ).fetchall()

    if not rows:
        safe_send(message.chat.id, "📦 Sizda hali buyurtmalar yo‘q.")
        return

    names = {
        "pending": "⏳ Tekshirilmoqda",
        "done": "✅ Bajarildi",
        "cancelled": "❌ Bekor qilindi",
    }

    text = "📦 <b>So‘nggi buyurtmalaringiz</b>\n\n"
    for row in rows:
        text += (
            f"🧾 <code>#{row['id']}</code> — {esc(row['item_title'])}\n"
            f"💳 {money(row['total_price'])} so‘m\n"
            f"📌 {names.get(row['status'], row['status'])}\n\n"
        )
    safe_send(message.chat.id, text)


# =========================================================
# SUPPORT
# =========================================================

@bot.message_handler(func=lambda m: m.text == "🧑‍💻 Admin bilan aloqa")
def support_start(message):
    set_state(message.from_user.id, "await_support")
    safe_send(
        message.chat.id,
        "🧑‍💻 Savolingizni yozing yoki rasm/fayl yuboring.\n"
        "Admin sizga shu bot orqali javob qaytaradi.",
        reply_markup=cancel_kb(),
    )


@bot.message_handler(
    content_types=["text", "photo", "document", "video"],
    func=lambda m: get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "await_support",
)
def support_receive(message):
    text = message.text if message.content_type == "text" else None
    file_id = None
    file_type = message.content_type

    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.video:
        file_id = message.video.file_id

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO support_messages(
                user_id,user_message_id,text,file_id,file_type,status,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                message.from_user.id,
                message.message_id,
                text,
                file_id,
                file_type,
                "new",
                now(),
            ),
        )
        support_id = cur.lastrowid

    clear_state(message.from_user.id)

    safe_send(
        message.chat.id,
        "✅ Savolingiz adminga yuborildi. Javob shu bot orqali keladi.",
        reply_markup=main_menu(message.from_user.id),
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "💬 Javob berish",
            callback_data=f"support_reply:{support_id}",
        )
    )

    caption = (
        "📩 <b>Yangi savol</b>\n\n"
        f"Support ID: <code>#{support_id}</code>\n"
        f"User ID: <code>{message.from_user.id}</code>\n"
        f"Ism: {esc(message.from_user.full_name)}\n"
        f"Username: @{esc(message.from_user.username or 'yoq')}"
    )

    for admin_id in ADMIN_IDS:
        try:
            if message.content_type == "text":
                bot.send_message(
                    admin_id,
                    caption + f"\n\n💬 {esc(text)}",
                    reply_markup=kb,
                )
            elif message.photo:
                bot.send_photo(admin_id, file_id, caption=caption, reply_markup=kb)
            elif message.document:
                bot.send_document(admin_id, file_id, caption=caption, reply_markup=kb)
            elif message.video:
                bot.send_video(admin_id, file_id, caption=caption, reply_markup=kb)
        except Exception:
            logger.exception("Support adminga yuborilmadi")


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📊 Admin statistika"),
        types.KeyboardButton("📦 Kutilayotgan buyurtmalar"),
    )
    kb.add(
        types.KeyboardButton("💳 Kutilayotgan to‘lovlar"),
        types.KeyboardButton("💬 Yangi savollar"),
    )
    kb.add(
        types.KeyboardButton("💰 Balans boshqarish"),
        types.KeyboardButton("💵 Narxlar boshqaruvi"),
    )
    kb.add(
        types.KeyboardButton("📢 Hammaga xabar"),
        types.KeyboardButton("👤 Foydalanuvchiga xabar"),
    )
    kb.add(
        types.KeyboardButton("🛠 Texnik rejim"),
        types.KeyboardButton("💳 Karta sozlamasi"),
    )
    kb.add(types.KeyboardButton("⬅️ Bosh menyu"))
    return kb


@bot.message_handler(func=lambda m: m.text == "⚙️ Admin panel")
def open_admin(message):
    if not is_admin(message.from_user.id):
        return
    safe_send(message.chat.id, "⚙️ <b>Admin panel</b>", reply_markup=admin_menu())


@bot.message_handler(func=lambda m: m.text == "📊 Admin statistika")
def admin_stats(message):
    if not is_admin(message.from_user.id):
        return

    with db() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        balance = conn.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"]
        orders = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        done = conn.execute("SELECT COUNT(*) c FROM orders WHERE status='done'").fetchone()["c"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total_price),0) s FROM orders WHERE status='done'"
        ).fetchone()["s"]
        pending_pay = conn.execute(
            "SELECT COUNT(*) c FROM payments WHERE status='pending'"
        ).fetchone()["c"]
        support = conn.execute(
            "SELECT COUNT(*) c FROM support_messages WHERE status='new'"
        ).fetchone()["c"]

    safe_send(
        message.chat.id,
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"💰 Foydalanuvchilar balansi: <b>{money(balance)} so‘m</b>\n"
        f"📦 Jami buyurtmalar: <b>{orders}</b>\n"
        f"✅ Bajarilgan: <b>{done}</b>\n"
        f"💳 Jami tushum: <b>{money(revenue)} so‘m</b>\n"
        f"🧾 Kutilayotgan to‘lovlar: <b>{pending_pay}</b>\n"
        f"💬 Yangi savollar: <b>{support}</b>",
    )


@bot.message_handler(func=lambda m: m.text == "📦 Kutilayotgan buyurtmalar")
def admin_pending_orders(message):
    if not is_admin(message.from_user.id):
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT id FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 20"
        ).fetchall()

    if not rows:
        safe_send(message.chat.id, "Kutilayotgan buyurtma yo‘q.")
        return

    for row in rows:
        notify_admins_new_order(row["id"])


@bot.message_handler(func=lambda m: m.text == "💳 Kutilayotgan to‘lovlar")
def admin_pending_payments(message):
    if not is_admin(message.from_user.id):
        return
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM payments
            WHERE status='pending'
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()

    if not rows:
        safe_send(message.chat.id, "Kutilayotgan to‘lov yo‘q.")
        return

    for p in rows:
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"pay_ok:{p['id']}"),
            types.InlineKeyboardButton("❌ Rad etish", callback_data=f"pay_no:{p['id']}"),
        )
        safe_send(
            message.chat.id,
            f"To‘lov #{p['id']}\n"
            f"User: <code>{p['user_id']}</code>\n"
            f"Miqdor: <b>{money(p['amount'])} so‘m</b>",
            reply_markup=kb,
        )


@bot.message_handler(func=lambda m: m.text == "💬 Yangi savollar")
def admin_new_support(message):
    if not is_admin(message.from_user.id):
        return
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM support_messages
            WHERE status='new'
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()

    if not rows:
        safe_send(message.chat.id, "Yangi savol yo‘q.")
        return

    for row in rows:
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton(
                "💬 Javob berish",
                callback_data=f"support_reply:{row['id']}",
            )
        )
        safe_send(
            message.chat.id,
            f"📩 Savol #{row['id']}\n"
            f"User: <code>{row['user_id']}</code>\n\n"
            f"{esc(row['text'] or '[fayl yuborilgan]')}",
            reply_markup=kb,
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("support_reply:"))
def admin_support_reply(call):
    if not is_admin(call.from_user.id):
        return
    support_id = int(call.data.split(":")[1])
    set_state(call.from_user.id, "admin_support_reply", support_id=support_id)
    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, "Javob matnini yozing.", reply_markup=cancel_kb())


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_reply_user:"))
def admin_reply_user(call):
    if not is_admin(call.from_user.id):
        return
    user_id = int(call.data.split(":")[1])
    set_state(call.from_user.id, "admin_direct_reply", target_user_id=user_id)
    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, "Foydalanuvchiga yuboriladigan xabarni yozing.")


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] in ("admin_support_reply", "admin_direct_reply")
)
def admin_reply_text(message):
    state = get_state(message.from_user.id)

    if state["name"] == "admin_support_reply":
        with db() as conn:
            support = conn.execute(
                "SELECT * FROM support_messages WHERE id=?",
                (state["support_id"],),
            ).fetchone()
        if not support:
            safe_send(message.chat.id, "Savol topilmadi.")
            clear_state(message.from_user.id)
            return
        target_user_id = support["user_id"]
        with db() as conn:
            conn.execute(
                """
                UPDATE support_messages
                SET status='answered',admin_id=?,answer_text=?,answered_at=?
                WHERE id=?
                """,
                (
                    message.from_user.id,
                    message.text,
                    now(),
                    state["support_id"],
                ),
            )
    else:
        target_user_id = state["target_user_id"]

    result = safe_send(
        target_user_id,
        "🧑‍💻 <b>Admin javobi</b>\n\n" + esc(message.text),
    )
    clear_state(message.from_user.id)

    if result:
        safe_send(message.chat.id, "✅ Javob yuborildi.", reply_markup=admin_menu())
    else:
        safe_send(message.chat.id, "❌ Xabar yuborilmadi.", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith(("pay_ok:", "pay_no:")))
def admin_payment_action(call):
    if not is_admin(call.from_user.id):
        return

    payment_id = int(call.data.split(":")[1])
    approve = call.data.startswith("pay_ok:")

    with db() as conn:
        payment = conn.execute(
            "SELECT * FROM payments WHERE id=?",
            (payment_id,),
        ).fetchone()

    if not payment or payment["status"] != "pending":
        bot.answer_callback_query(call.id, "To‘lov avval ko‘rib chiqilgan.", show_alert=True)
        return

    if approve:
        change_balance(
            payment["user_id"],
            payment["amount"],
            "topup",
            f"To‘lov #{payment_id}",
        )
        status = "approved"
        text = (
            f"✅ To‘lovingiz tasdiqlandi.\n"
            f"Balansingizga <b>{money(payment['amount'])} so‘m</b> qo‘shildi."
        )
    else:
        status = "rejected"
        text = "❌ To‘lov chekingiz rad etildi. Admin bilan bog‘laning."

    with db() as conn:
        conn.execute(
            "UPDATE payments SET status=?,updated_at=? WHERE id=?",
            (status, now(), payment_id),
        )

    safe_send(payment["user_id"], text, reply_markup=main_menu(payment["user_id"]))
    bot.answer_callback_query(call.id, "Bajarildi.")
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(("admin_order_done:", "admin_order_cancel:"))
)
def admin_order_action(call):
    if not is_admin(call.from_user.id):
        return

    order_id = int(call.data.split(":")[1])
    done = call.data.startswith("admin_order_done:")

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?",
            (order_id,),
        ).fetchone()

    if not order or order["status"] != "pending":
        bot.answer_callback_query(call.id, "Buyurtma avval ko‘rib chiqilgan.", show_alert=True)
        return

    if done:
        with db() as conn:
            conn.execute(
                "UPDATE orders SET status='done',updated_at=? WHERE id=?",
                (now(), order_id),
            )
        safe_send(
            order["user_id"],
            f"✅ Buyurtmangiz bajarildi.\n🧾 ID: <code>#{order_id}</code>",
        )
    else:
        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status='cancelled',refunded=1,updated_at=?
                WHERE id=?
                """,
                (now(), order_id),
            )
        change_balance(
            order["user_id"],
            order["total_price"],
            "refund",
            f"Buyurtma #{order_id} qaytarimi",
        )
        safe_send(
            order["user_id"],
            f"❌ Buyurtmangiz bekor qilindi.\n"
            f"💰 {money(order['total_price'])} so‘m balansingizga qaytarildi.",
        )

    bot.answer_callback_query(call.id, "Bajarildi.")
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass


@bot.message_handler(func=lambda m: m.text == "💰 Balans boshqarish")
def admin_balance_start(message):
    if not is_admin(message.from_user.id):
        return
    set_state(message.from_user.id, "admin_balance_user")
    safe_send(message.chat.id, "Foydalanuvchi ID raqamini yuboring.", reply_markup=cancel_kb())


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_balance_user"
)
def admin_balance_user(message):
    try:
        user_id = int(message.text)
    except ValueError:
        safe_send(message.chat.id, "ID faqat son bo‘lsin.")
        return

    user = get_user(user_id)
    if not user:
        safe_send(message.chat.id, "Foydalanuvchi topilmadi.")
        return

    set_state(message.from_user.id, "admin_balance_amount", target_user_id=user_id)
    safe_send(
        message.chat.id,
        f"Joriy balans: {money(user['balance'])} so‘m\n\n"
        "Qo‘shish uchun musbat son, ayirish uchun manfiy son kiriting.\n"
        "Masalan: <code>50000</code> yoki <code>-10000</code>",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_balance_amount"
)
def admin_balance_amount(message):
    state = get_state(message.from_user.id)
    try:
        amount = int(message.text.replace(" ", ""))
    except ValueError:
        safe_send(message.chat.id, "Faqat son kiriting.")
        return

    if not change_balance(
        state["target_user_id"],
        amount,
        "admin_change",
        f"Admin {message.from_user.id}",
    ):
        safe_send(message.chat.id, "❌ Amal bajarilmadi. Balans manfiy bo‘lib qolishi mumkin.")
        return

    clear_state(message.from_user.id)
    safe_send(message.chat.id, "✅ Balans yangilandi.", reply_markup=admin_menu())
    safe_send(
        state["target_user_id"],
        f"💰 Balansingiz admin tomonidan {money(amount)} so‘mga o‘zgartirildi.",
    )


@bot.message_handler(func=lambda m: m.text == "💵 Narxlar boshqaruvi")
def admin_prices(message):
    if not is_admin(message.from_user.id):
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    categories = [
        ("⭐ Stars", "stars"),
        ("💝 Gift", "gift"),
        ("🌟 Premium", "premium"),
        ("🎮 PUBG", "pubg"),
        ("📱 Raqam", "number"),
        ("📸 Instagram", "smm_instagram"),
        ("✈️ Telegram", "smm_telegram"),
        ("▶️ YouTube", "smm_youtube"),
        ("🎵 TikTok", "smm_tiktok"),
        ("📘 Facebook", "smm_facebook"),
    ]
    for title, category in categories:
        kb.add(
            types.InlineKeyboardButton(
                title,
                callback_data=f"admin_cat:{category}",
            )
        )
    kb.add(
        types.InlineKeyboardButton(
            "➕ Yangi xizmat qo‘shish",
            callback_data="admin_add_item",
        )
    )
    safe_send(message.chat.id, "Qaysi bo‘lim narxlarini boshqarasiz?", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_cat:"))
def admin_category_items(call):
    if not is_admin(call.from_user.id):
        return
    category = call.data.split(":")[1]

    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM catalog WHERE category=? ORDER BY sort_order,id",
            (category,),
        ).fetchall()

    kb = types.InlineKeyboardMarkup()
    for row in rows:
        status = "🟢" if row["active"] else "🔴"
        kb.add(
            types.InlineKeyboardButton(
                f"{status} {row['title']} — {money(row['price'])}",
                callback_data=f"admin_item:{row['id']}",
            )
        )
    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, f"<b>{category}</b> xizmatlari:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_item:"))
def admin_item_menu(call):
    if not is_admin(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])

    with db() as conn:
        item = conn.execute("SELECT * FROM catalog WHERE id=?", (item_id,)).fetchone()

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "💵 Narxni o‘zgartirish",
            callback_data=f"admin_item_price:{item_id}",
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🟢/🔴 Yoqish-o‘chirish",
            callback_data=f"admin_item_toggle:{item_id}",
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🗑 O‘chirish",
            callback_data=f"admin_item_delete:{item_id}",
        )
    )

    bot.answer_callback_query(call.id)
    safe_send(
        call.message.chat.id,
        f"📦 {esc(item['title'])}\n"
        f"💵 Narx: {money(item['price'])} so‘m\n"
        f"🔢 Birlik: {item['unit_amount']}\n"
        f"Holat: {'faol' if item['active'] else 'o‘chirilgan'}",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_item_price:"))
def admin_item_price_start(call):
    if not is_admin(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])
    set_state(call.from_user.id, "admin_item_price", item_id=item_id)
    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, "Yangi narxni so‘mda kiriting.")


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_item_price"
)
def admin_item_price_save(message):
    state = get_state(message.from_user.id)
    try:
        price = int(message.text.replace(" ", ""))
        if price < 0:
            raise ValueError
    except ValueError:
        safe_send(message.chat.id, "To‘g‘ri narx kiriting.")
        return

    with db() as conn:
        conn.execute("UPDATE catalog SET price=? WHERE id=?", (price, state["item_id"]))
    clear_state(message.from_user.id)
    safe_send(message.chat.id, "✅ Narx yangilandi.", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_item_toggle:"))
def admin_item_toggle(call):
    if not is_admin(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])
    with db() as conn:
        conn.execute(
            "UPDATE catalog SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
            (item_id,),
        )
    bot.answer_callback_query(call.id, "Holat o‘zgartirildi.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_item_delete:"))
def admin_item_delete(call):
    if not is_admin(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])
    with db() as conn:
        conn.execute("DELETE FROM catalog WHERE id=?", (item_id,))
    bot.answer_callback_query(call.id, "Xizmat o‘chirildi.")
    safe_send(call.message.chat.id, "🗑 Xizmat o‘chirildi.")


@bot.callback_query_handler(func=lambda c: c.data == "admin_add_item")
def admin_add_item_start(call):
    if not is_admin(call.from_user.id):
        return
    set_state(call.from_user.id, "admin_add_category")
    bot.answer_callback_query(call.id)
    safe_send(
        call.message.chat.id,
        "Kategoriya yozing:\n"
        "<code>stars</code>, <code>gift</code>, <code>premium</code>, "
        "<code>pubg</code>, <code>number</code>,\n"
        "<code>smm_instagram</code>, <code>smm_telegram</code>, "
        "<code>smm_youtube</code>, <code>smm_tiktok</code>, "
        "<code>smm_facebook</code>",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"].startswith("admin_add_")
)
def admin_add_item_flow(message):
    state = get_state(message.from_user.id)
    name = state["name"]

    if name == "admin_add_category":
        category = message.text.strip().lower()
        if category not in {
            "stars",
            "gift",
            "premium",
            "pubg",
            "number",
            "smm_instagram",
            "smm_telegram",
            "smm_youtube",
            "smm_tiktok",
            "smm_facebook",
        }:
            safe_send(message.chat.id, "Kategoriya noto‘g‘ri.")
            return
        set_state(message.from_user.id, "admin_add_title", category=category)
        safe_send(message.chat.id, "Xizmat nomini yozing.")
        return

    if name == "admin_add_title":
        set_state(
            message.from_user.id,
            "admin_add_price",
            category=state["category"],
            title=message.text.strip(),
        )
        safe_send(message.chat.id, "Narxini yozing.")
        return

    if name == "admin_add_price":
        try:
            price = int(message.text.replace(" ", ""))
        except ValueError:
            safe_send(message.chat.id, "Faqat son kiriting.")
            return
        set_state(
            message.from_user.id,
            "admin_add_unit",
            category=state["category"],
            title=state["title"],
            price=price,
        )
        safe_send(
            message.chat.id,
            "Birlik miqdorini yozing.\n"
            "Masalan: 50 Stars uchun 50; 1000 ta SMM uchun 1000; oddiy xizmat uchun 1.",
        )
        return

    if name == "admin_add_unit":
        try:
            unit = int(message.text.replace(" ", ""))
            if unit <= 0:
                raise ValueError
        except ValueError:
            safe_send(message.chat.id, "To‘g‘ri son kiriting.")
            return

        with db() as conn:
            conn.execute(
                """
                INSERT INTO catalog(
                    category,title,description,price,unit_amount,min_qty,max_qty,
                    active,sort_order,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    state["category"],
                    state["title"],
                    "",
                    state["price"],
                    unit,
                    1,
                    1000000,
                    1,
                    999,
                    now(),
                ),
            )

        clear_state(message.from_user.id)
        safe_send(message.chat.id, "✅ Yangi xizmat qo‘shildi.", reply_markup=admin_menu())


@bot.message_handler(func=lambda m: m.text == "📢 Hammaga xabar")
def admin_broadcast_start(message):
    if not is_admin(message.from_user.id):
        return
    set_state(message.from_user.id, "admin_broadcast")
    safe_send(
        message.chat.id,
        "Hammaga yuboriladigan xabarni yuboring.\n"
        "Matn, rasm, video yoki fayl bo‘lishi mumkin.",
        reply_markup=cancel_kb(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document"],
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_broadcast",
)
def admin_broadcast_send(message):
    with db() as conn:
        users = conn.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()

    sent = 0
    failed = 0

    for row in users:
        try:
            bot.copy_message(
                row["user_id"],
                message.chat.id,
                message.message_id,
            )
            sent += 1
            time.sleep(0.04)
        except Exception:
            failed += 1

    clear_state(message.from_user.id)
    safe_send(
        message.chat.id,
        f"📢 Tarqatish tugadi.\n✅ Yuborildi: {sent}\n❌ Xato: {failed}",
        reply_markup=admin_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "👤 Foydalanuvchiga xabar")
def admin_direct_start(message):
    if not is_admin(message.from_user.id):
        return
    set_state(message.from_user.id, "admin_direct_user")
    safe_send(message.chat.id, "Foydalanuvchi ID raqamini kiriting.")


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_direct_user"
)
def admin_direct_user(message):
    try:
        user_id = int(message.text)
    except ValueError:
        safe_send(message.chat.id, "ID faqat son bo‘lsin.")
        return

    if not get_user(user_id):
        safe_send(message.chat.id, "Foydalanuvchi topilmadi.")
        return

    set_state(message.from_user.id, "admin_direct_reply", target_user_id=user_id)
    safe_send(message.chat.id, "Yuboriladigan xabarni yozing.")


@bot.message_handler(func=lambda m: m.text == "🛠 Texnik rejim")
def admin_maintenance(message):
    if not is_admin(message.from_user.id):
        return
    current = get_setting("maintenance")
    new_value = "0" if current == "1" else "1"
    set_setting("maintenance", new_value)
    safe_send(
        message.chat.id,
        f"🛠 Texnik rejim {'yoqildi' if new_value == '1' else 'o‘chirildi'}.",
    )


@bot.message_handler(func=lambda m: m.text == "💳 Karta sozlamasi")
def admin_card_start(message):
    if not is_admin(message.from_user.id):
        return
    set_state(message.from_user.id, "admin_card_number")
    safe_send(
        message.chat.id,
        f"Joriy karta: <code>{esc(get_setting('card_number'))}</code>\n\n"
        "Yangi karta raqamini yozing.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_card_number"
)
def admin_card_number(message):
    set_state(
        message.from_user.id,
        "admin_card_owner",
        card_number=message.text.strip(),
    )
    safe_send(message.chat.id, "Karta egasi nomini yozing.")


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and get_state(m.from_user.id)
    and get_state(m.from_user.id)["name"] == "admin_card_owner"
)
def admin_card_owner(message):
    state = get_state(message.from_user.id)
    set_setting("card_number", state["card_number"])
    set_setting("card_owner", message.text.strip())
    clear_state(message.from_user.id)
    safe_send(message.chat.id, "✅ Karta ma’lumotlari yangilandi.", reply_markup=admin_menu())


# =========================================================
# STARS YECHISH
# =========================================================

@bot.message_handler(func=lambda m: m.text == "💸 Stars yechish")
def stars_withdraw(message):
    safe_send(
        message.chat.id,
        "💸 <b>Stars yechish</b>\n\n"
        "Stars yechish so‘rovi admin orqali ko‘rib chiqiladi.\n"
        "Admin bilan aloqa bo‘limidan Stars miqdori va profilingizni yuboring.",
    )


# =========================================================
# CALLBACK ORQAGA
# =========================================================

@bot.callback_query_handler(func=lambda c: c.data == "main")
def callback_main(call):
    bot.answer_callback_query(call.id)
    clear_state(call.from_user.id)
    send_home(call.message.chat.id, call.from_user.id)


# =========================================================
# FALLBACK
# =========================================================

@bot.message_handler(content_types=["text"])
def fallback(message):
    ensure_user(message.from_user)

    user = get_user(message.from_user.id)
    if user and user["blocked"]:
        return

    safe_send(
        message.chat.id,
        "Kerakli bo‘limni pastdagi tugmalardan tanlang.",
        reply_markup=main_menu(message.from_user.id),
    )


# =========================================================
# ISHGA TUSHIRISH — POLLING
# =========================================================

def start_polling():
    global BOT_USERNAME

    init_db()

    # Oldin webhook ishlatilgan bo‘lsa, polling bilan to‘qnashmasligi uchun o‘chiriladi.
    

    me = bot.get_me()
    BOT_USERNAME = me.username or BOT_USERNAME

    logger.info("Bot ishga tushdi: @%s", BOT_USERNAME)
    logger.info("Ishlash rejimi: polling")
    logger.info("Adminlar: %s", ", ".join(map(str, ADMIN_IDS)))

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    start_polling()

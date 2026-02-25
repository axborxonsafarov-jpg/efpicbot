import asyncio
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.sessions import StringSession

# ══════════════════════════════════════════════
#                SOZLAMALAR
# ══════════════════════════════════════════════
BOT_TOKEN         = "8434963162:AAExAV5fIQBqMys_bCkzoF1MfeFk4bsFx-A"
ADMIN_IDS         = [6302762403]

ADMIN_API_ID      = 39206752
ADMIN_API_HASH    = "82b55fc7b6349fe4e68205c6a29e6af6"
ADMIN_SESSION_STR = os.environ.get("ADMIN_SESSION_STR", "")
CARD_NUMBER       = "8600 1234 5678 9012"
CARD_OWNER        = "FAMILIYA ISM"
PAYMENT_TIME      = 5 * 60
MIN_AMOUNT        = 1_000
MAX_AMOUNT        = 10_000_000
SHOP_PRICE        = 1_000
SHOP_DURATION     = 30
HUMO_BOT_USERNAME = "HUMOcardbot"

# ══════════════════════════════════════════════
#                   LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("paybot")

# ══════════════════════════════════════════════
#                  DATABASE
# ══════════════════════════════════════════════
def db(sql, params=(), *, one=False, fetch=False):
    con = sqlite3.connect("pay.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    if one:
        row = cur.fetchone(); con.close(); return row
    if fetch:
        rows = cur.fetchall(); con.close(); return rows
    lastid = cur.lastrowid
    con.commit(); con.close()
    return lastid


def init_db():
    db("""CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY,
        username   TEXT DEFAULT '',
        name       TEXT DEFAULT '',
        balance    REAL DEFAULT 0,
        is_banned  INTEGER DEFAULT 0,
        lang       TEXT DEFAULT 'uz',
        reg        TEXT DEFAULT (datetime('now','localtime'))
    )""")
    db("""CREATE TABLE IF NOT EXISTS orders (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        amount      REAL,
        base_amount REAL,
        status      TEXT DEFAULT 'pending',
        order_type  TEXT DEFAULT 'topup',
        shop_id     INTEGER,
        created     TEXT DEFAULT (datetime('now','localtime')),
        paid        TEXT
    )""")
    db("""CREATE TABLE IF NOT EXISTS shops (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER,
        shop_name      TEXT,
        card_number    TEXT,
        api_id         INTEGER,
        api_hash       TEXT,
        phone          TEXT,
        string_session TEXT,
        api_key        TEXT UNIQUE,
        status         TEXT DEFAULT 'active',
        expires        TEXT,
        created        TEXT DEFAULT (datetime('now','localtime'))
    )""")
    db("""CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""")
    db("""CREATE TABLE IF NOT EXISTS channels (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT UNIQUE,
        title      TEXT,
        username   TEXT,
        required   INTEGER DEFAULT 1,
        added      TEXT DEFAULT (datetime('now','localtime'))
    )""")
    db("""CREATE TABLE IF NOT EXISTS broadcasts (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        text     TEXT,
        sent     INTEGER DEFAULT 0,
        failed   INTEGER DEFAULT 0,
        created  TEXT DEFAULT (datetime('now','localtime'))
    )""")
    # Default sozlamalar
    for key, val in [
        ("shop_price", str(SHOP_PRICE)),
        ("bot_active", "1"),
        ("welcome_text", "👋 <b>Salom, {name}!</b>\n\nBu bot orqali botlaringizga\n<b>Humo karta avto to'lov</b> tizimini ulashingiz mumkin! ⚡"),
    ]:
        if not db(f"SELECT value FROM settings WHERE key=?", (key,), one=True):
            db("INSERT INTO settings(key,value) VALUES(?,?)", (key, val))
    log.info("✅ DB tayyor")


def get_setting(key: str, default: str = "") -> str:
    row = db("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return row["value"] if row else default


def set_setting(key: str, value: str):
    db("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def get_user(uid: int, username: str = "", name: str = ""):
    row = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not row:
        db("INSERT INTO users(id,username,name) VALUES(?,?,?)", (uid, username, name))
    return db("SELECT * FROM users WHERE id=?", (uid,), one=True)


def get_shop(user_id: int):
    return db("SELECT * FROM shops WHERE user_id=?", (user_id,), one=True)


def get_shop_by_api_key(api_key: str):
    return db(
        "SELECT * FROM shops WHERE api_key=? AND status='active'",
        (api_key,), one=True
    )


def get_channels():
    return db("SELECT * FROM channels WHERE required=1", fetch=True)


def fmts(n) -> str:
    return f"{float(n):,.0f} UZS".replace(",", " ")


def days_left(expires_str: str) -> int:
    if not expires_str:
        return 0
    try:
        exp   = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        delta = exp - datetime.now()
        return max(0, delta.days)
    except Exception:
        return 0


# ══════════════════════════════════════════════
#         KANAL OBUNA TEKSHIRISH
# ══════════════════════════════════════════════
async def check_subscriptions(user_id: int) -> list:
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxati"""
    channels = get_channels()
    not_subscribed = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch["channel_id"], user_id)
            if member.status in ["left", "kicked", "banned"]:
                not_subscribed.append(ch)
        except Exception:
            not_subscribed.append(ch)
    return not_subscribed


def kb_subscribe(channels: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        title = ch["title"] or ch["channel_id"]
        url   = f"https://t.me/{ch['username']}" if ch["username"] else f"https://t.me/c/{str(ch['channel_id']).replace('-100', '')}"
        rows.append([InlineKeyboardButton(text=f"📢 {title}", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════════
#              PENDING TO'LOVLAR
# ══════════════════════════════════════════════
PENDING: dict = {}


def unique_amount(base: float) -> float:
    active = {
        p["amount"] for p in PENDING.values()
        if not p.get("confirmed") and time.time() < p["expires"]
    }
    if base not in active:
        return base
    amt = base + 1
    while amt in active:
        amt += 1
    return amt


# ══════════════════════════════════════════════
#   FASTAPI — EXTERNAL API
# ══════════════════════════════════════════════
api = FastAPI(title="Avto To'lov API", version="9.0")


class CreateOrderRequest(BaseModel):
    api_key:     str
    user_id:     str
    amount:      float
    webhook_url: str


class CreateOrderResponse(BaseModel):
    ok:          bool
    order_id:    Optional[int]   = None
    amount:      Optional[float] = None
    card_number: Optional[str]   = None
    expires_in:  Optional[int]   = None
    error:       Optional[str]   = None


@api.post("/api/create_order", response_model=CreateOrderResponse)
async def api_create_order(req: CreateOrderRequest):
    shop = get_shop_by_api_key(req.api_key)
    if not shop:
        return CreateOrderResponse(ok=False, error="API key noto'g'ri yoki do'kon aktiv emas")
    if days_left(shop["expires"]) <= 0:
        return CreateOrderResponse(ok=False, error="Do'kon obunasi tugagan")
    if req.amount < MIN_AMOUNT:
        return CreateOrderResponse(ok=False, error=f"Minimal summa: {MIN_AMOUNT} UZS")
    if req.amount > MAX_AMOUNT:
        return CreateOrderResponse(ok=False, error=f"Maksimal summa: {MAX_AMOUNT} UZS")

    amount  = unique_amount(float(req.amount))
    expires = time.time() + PAYMENT_TIME

    oid = db(
        "INSERT INTO orders(user_id, amount, base_amount, order_type, shop_id) VALUES(?,?,?,?,?)",
        (shop["user_id"], amount, req.amount, "external", shop["id"])
    )

    PENDING[oid] = {
        "user_id":     shop["user_id"],
        "ext_user_id": str(req.user_id),
        "amount":      amount,
        "base_amount": req.amount,
        "chat_id":     None,
        "expires":     expires,
        "confirmed":   False,
        "order_type":  "external",
        "shop_id":     shop["id"],
        "webhook_url": req.webhook_url,
        "card_number": shop["card_number"],
    }

    log.info(f"[API] Order #{oid} | shop={shop['shop_name']} | ext_user={req.user_id} | summa={amount}")

    return CreateOrderResponse(
        ok=True, order_id=oid, amount=amount,
        card_number=shop["card_number"], expires_in=PAYMENT_TIME,
    )


@api.get("/api/status/{order_id}")
async def api_order_status(order_id: int, api_key: str):
    shop = get_shop_by_api_key(api_key)
    if not shop:
        raise HTTPException(status_code=403, detail="API key noto'g'ri")
    order = db("SELECT * FROM orders WHERE id=? AND shop_id=?", (order_id, shop["id"]), one=True)
    if not order:
        raise HTTPException(status_code=404, detail="Order topilmadi")
    p = PENDING.get(order_id)
    if p and not p.get("confirmed"):
        remaining = max(0, int(p["expires"] - time.time()))
        return {"ok": True, "status": "pending", "remaining": remaining,
                "amount": p["amount"], "card_number": p["card_number"]}
    return {"ok": True, "status": order["status"], "paid_at": order["paid"]}


@api.get("/api/shops/info")
async def api_shop_info(api_key: str):
    shop = get_shop_by_api_key(api_key)
    if not shop:
        raise HTTPException(status_code=403, detail="API key noto'g'ri")
    return {"ok": True, "shop_name": shop["shop_name"], "card_number": shop["card_number"],
            "expires": shop["expires"], "days_left": days_left(shop["expires"])}


@api.get("/api/ping")
async def api_ping():
    return {"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ══════════════════════════════════════════════
#   WEBHOOK
# ══════════════════════════════════════════════
async def send_webhook(webhook_url: str, data: dict):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json=data)
            log.info(f"[WEBHOOK] {webhook_url} → {resp.status_code}")
    except Exception as e:
        log.error(f"[WEBHOOK] Xato: {e}")


# ══════════════════════════════════════════════
#         SHOP TELETHON CLIENTLAR
# ══════════════════════════════════════════════
SHOP_CLIENTS:  dict = {}
admin_userbot: Optional[TelegramClient] = None
SETUP_PENDING: dict = {}
PENDING_AUTH:  dict = {}

# ══════════════════════════════════════════════
#               BOT
# ══════════════════════════════════════════════
bot    = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp     = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ══════════════════════════════════════════════
#                   FSM STATES
# ══════════════════════════════════════════════
class S(StatesGroup):
    amount           = State()
    admin_price      = State()
    admin_broadcast  = State()
    admin_add_channel = State()
    admin_user_search = State()
    admin_user_action = State()
    admin_balance_add = State()
    admin_welcome_text = State()


# ══════════════════════════════════════════════
#        REPLY KEYBOARD (asosiy menyu)
# ══════════════════════════════════════════════
def rkb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Do'konlarim"),   KeyboardButton(text="👤 Profil")],
            [KeyboardButton(text="💰 Pul kiritish"),   KeyboardButton(text="⚙️ API tizimi")],
            [KeyboardButton(text="🤖 Bot haqida")],
        ],
        resize_keyboard=True
    )


def rkb_admin() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"),     KeyboardButton(text="👥 Foydalanuvchilar")],
            [KeyboardButton(text="🛒 Do'konlar"),       KeyboardButton(text="📢 Kanallar")],
            [KeyboardButton(text="📨 Xabar yuborish"),  KeyboardButton(text="💰 Narx o'zgartirish")],
            [KeyboardButton(text="⚙️ Bot sozlamalari"), KeyboardButton(text="🏠 Asosiy menyu")],
        ],
        resize_keyboard=True
    )


# ══════════════════════════════════════════════
#                  INLINE KLAVIATURALAR
# ══════════════════════════════════════════════
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Do'konlarim",  callback_data="shops")],
        [
            InlineKeyboardButton(text="👤 Profil",       callback_data="profile"),
            InlineKeyboardButton(text="💰 Pul kiritish", callback_data="topup"),
        ],
        [
            InlineKeyboardButton(text="🤖 Bot haqida",   callback_data="about"),
            InlineKeyboardButton(text="⚙️ API tizimi",   callback_data="api_info"),
        ],
    ])


def kb_back(to="back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=to)]
    ])


def kb_pay(oid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'lov qildim", callback_data=f"paid_{oid}")],
        [InlineKeyboardButton(text="❌ Bekor qilish",  callback_data=f"cancel_{oid}")],
    ])


def kb_shops(shop) -> InlineKeyboardMarkup:
    if shop:
        rows = [
            [InlineKeyboardButton(text="📊 Do'kon ma'lumotlari", callback_data="shop_info")],
            [InlineKeyboardButton(text="🔑 API key ko'rish",     callback_data="shop_apikey")],
            [InlineKeyboardButton(text="💳 Obunani uzaytirish",  callback_data="shop_renew")],
            [InlineKeyboardButton(text="🔙 Orqaga",              callback_data="back")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="➕ Do'kon ochish", callback_data="shop_open")],
            [InlineKeyboardButton(text="🔙 Orqaga",        callback_data="back")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_users_page(page: int, total: int, uid_list: list) -> InlineKeyboardMarkup:
    rows = []
    start = page * 10
    end   = min(start + 10, len(uid_list))
    for uid in uid_list[start:end]:
        u = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if u:
            name = u["name"] or str(uid)
            ban  = "🚫" if u["is_banned"] else "✅"
            rows.append([InlineKeyboardButton(
                text=f"{ban} {name[:20]} | {uid}",
                callback_data=f"auser_{uid}"
            )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"users_page_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{(total-1)//10+1}", callback_data="noop"))
    if end < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"users_page_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔍 Qidirish", callback_data="admin_user_search")])
    rows.append([InlineKeyboardButton(text="🔙 Orqaga",   callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_user_detail(uid: int, is_banned: int) -> InlineKeyboardMarkup:
    ban_text = "✅ Banni olib tashlash" if is_banned else "🚫 Banlash"
    ban_cb   = f"unban_{uid}" if is_banned else f"ban_{uid}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Balans qo'shish", callback_data=f"addbal_{uid}")],
        [InlineKeyboardButton(text=ban_text,              callback_data=ban_cb)],
        [InlineKeyboardButton(text="📨 Xabar yuborish",  callback_data=f"msguser_{uid}")],
        [InlineKeyboardButton(text="🔙 Orqaga",          callback_data="admin_users_list")],
    ])


# ══════════════════════════════════════════════
#              ASOSIY HANDLERLAR
# ══════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    u = get_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name or "")

    # Ban tekshirish
    if u and u["is_banned"]:
        await msg.answer("🚫 Siz botdan bloklangansiz!")
        return

    # Kanal obuna tekshirish
    not_sub = await check_subscriptions(msg.from_user.id)
    if not_sub:
        await msg.answer(
            "📢 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>",
            reply_markup=kb_subscribe(not_sub)
        )
        return

    welcome = get_setting("welcome_text").replace("{name}", msg.from_user.first_name or "")
    await msg.answer(
        welcome + "\n\n▪️ Do'kon oching — kartangizni ulang\n"
        "▪️ API key oling — botingizga ulang\n"
        "▪️ To'lovlar avtomatik tasdiqlanadi",
        reply_markup=rkb_main(),
    )


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    u = get_user(call.from_user.id)
    if u and u["is_banned"]:
        await call.answer("🚫 Siz botdan bloklangansiz!", show_alert=True)
        return
    not_sub = await check_subscriptions(call.from_user.id)
    if not_sub:
        await call.answer("❌ Hali ham obuna bo'lmadingiz!", show_alert=True)
    else:
        await call.message.delete()
        welcome = get_setting("welcome_text").replace("{name}", call.from_user.first_name or "")
        await call.message.answer(
            welcome + "\n\n▪️ Do'kon oching — kartangizni ulang\n"
            "▪️ API key oling — botingizga ulang\n"
            "▪️ To'lovlar avtomatik tasdiqlanadi",
            reply_markup=rkb_main(),
        )


@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    PENDING_AUTH.pop(call.from_user.id, None)
    SETUP_PENDING.pop(call.from_user.id, None)
    await call.message.edit_text("👋 <b>Bosh menyu</b>", reply_markup=kb_main())


@router.callback_query(F.data == "admin_back")
async def cb_admin_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id not in ADMIN_IDS:
        return
    await show_admin_stats(call.message, edit=True)


# ── Reply keyboard handlerlari ──

@router.message(F.text == "🛒 Do'konlarim")
async def rk_shops(msg: Message):
    await process_shops(msg)


@router.message(F.text == "👤 Profil")
async def rk_profile(msg: Message):
    await process_profile(msg)


@router.message(F.text == "💰 Pul kiritish")
async def rk_topup(msg: Message, state: FSMContext):
    await process_topup_start(msg, state)


@router.message(F.text == "⚙️ API tizimi")
async def rk_api(msg: Message):
    await process_api_info(msg)


@router.message(F.text == "🤖 Bot haqida")
async def rk_about(msg: Message):
    await msg.answer(
        "🤖 <b>Bot haqida</b>\n\n"
        "✅ Humo karta orqali avto to'lov\n"
        "✅ 5 daqiqada avtomatik tasdiqlash\n"
        "✅ Do'kon tizimi — oylik obuna\n"
        "✅ API integratsiya — bot.php ga ulash\n"
        "✅ Webhook orqali avtomatik bildirishnoma"
    )


@router.message(F.text == "🏠 Asosiy menyu")
async def rk_main(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("🏠 <b>Asosiy menyu</b>", reply_markup=rkb_main())


# ── Admin reply keyboard handlerlari ──

@router.message(F.text == "📊 Statistika")
async def rk_admin_stats(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await show_admin_stats(msg)


@router.message(F.text == "👥 Foydalanuvchilar")
async def rk_admin_users(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await show_users_list(msg, page=0)


@router.message(F.text == "🛒 Do'konlar")
async def rk_admin_shops(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await show_admin_shops_list(msg)


@router.message(F.text == "📢 Kanallar")
async def rk_admin_channels(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await show_channels_list(msg)


@router.message(F.text == "📨 Xabar yuborish")
async def rk_admin_broadcast(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(S.admin_broadcast)
    users_count = db("SELECT COUNT(*) AS c FROM users WHERE is_banned=0", one=True)
    await msg.answer(
        f"📨 <b>Barcha foydalanuvchilarga xabar yuborish</b>\n\n"
        f"👥 Aktiv foydalanuvchilar: <b>{users_count['c']} ta</b>\n\n"
        f"Xabarni yuboring (matn, rasm, video qabul qilinadi):\n"
        f"<i>Bekor qilish: /cancel</i>"
    )


@router.message(F.text == "💰 Narx o'zgartirish")
async def rk_admin_price(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    price = get_setting("shop_price", str(SHOP_PRICE))
    await state.set_state(S.admin_price)
    await msg.answer(
        f"💰 <b>Do'kon narxini o'zgartirish</b>\n\n"
        f"Hozirgi narx: <b>{fmts(price)}</b> / oy\n\n"
        f"Yangi narxni kiriting (so'mda):\n<i>Bekor qilish: /cancel</i>"
    )


@router.message(F.text == "⚙️ Bot sozlamalari")
async def rk_admin_settings(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    bot_active = get_setting("bot_active", "1")
    status_txt = "✅ Aktiv" if bot_active == "1" else "❌ To'xtatilgan"
    await msg.answer(
        f"⚙️ <b>Bot sozlamalari</b>\n\n"
        f"Bot holati: {status_txt}\n\n"
        f"Kutilayotgan to'lovlar: <b>{len(PENDING)} ta</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Botni to'xtatish" if bot_active == "1" else "✅ Botni yoqish",
                callback_data="toggle_bot"
            )],
            [InlineKeyboardButton(text="✏️ Welcome xabar", callback_data="edit_welcome")],
            [InlineKeyboardButton(text="📊 Kutilayotgan to'lovlar", callback_data="admin_pending")],
        ])
    )


# ══════════════════════════════════════════════
#          HELPER FUNKSIYALAR
# ══════════════════════════════════════════════

async def show_admin_stats(msg_or_message, edit=False):
    price     = get_setting("shop_price", str(SHOP_PRICE))
    total     = db("SELECT COUNT(*) AS c, SUM(amount) AS s FROM orders WHERE status='paid'", one=True)
    shops_c   = db("SELECT COUNT(*) AS c FROM shops", one=True)
    users_c   = db("SELECT COUNT(*) AS c FROM users", one=True)
    banned_c  = db("SELECT COUNT(*) AS c FROM users WHERE is_banned=1", one=True)
    today     = datetime.now().strftime("%Y-%m-%d")
    today_c   = db("SELECT COUNT(*) AS c, SUM(amount) AS s FROM orders WHERE status='paid' AND paid LIKE ?", (today+"%",), one=True)
    ext_cnt   = db("SELECT COUNT(*) AS c FROM orders WHERE status='paid' AND order_type='external'", one=True)
    channels_c = db("SELECT COUNT(*) AS c FROM channels WHERE required=1", one=True)

    text = (
        f"📊 <b>Admin Panel — Statistika</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Jami foydalanuvchilar: <b>{users_c['c']}</b>\n"
        f"🚫 Banlangan: <b>{banned_c['c']}</b>\n"
        f"📢 Majburiy kanallar: <b>{channels_c['c']}</b>\n\n"
        f"🛒 Do'konlar: <b>{shops_c['c']}</b>\n"
        f"🔄 Kutilmoqda: <b>{len(PENDING)} ta</b>\n\n"
        f"💰 Bugun: <b>{today_c['c']} to'lov | {fmts(today_c['s'] or 0)}</b>\n"
        f"✅ Jami tasdiqlangan: <b>{total['c']} ta</b>\n"
        f"🤖 Bot.php to'lovlar: <b>{ext_cnt['c']} ta</b>\n"
        f"💵 Jami summa: <b>{fmts(total['s'] or 0)}</b>\n\n"
        f"💰 Do'kon narxi: <b>{fmts(price)}</b> / oy\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    if edit:
        await msg_or_message.edit_text(text)
    else:
        await msg_or_message.answer(text, reply_markup=rkb_admin())


async def show_users_list(msg, page: int = 0):
    users = db("SELECT id FROM users ORDER BY reg DESC", fetch=True)
    uid_list = [u["id"] for u in users]
    total    = len(uid_list)
    if total == 0:
        await msg.answer("👥 Foydalanuvchilar yo'q")
        return
    await msg.answer(
        f"👥 <b>Foydalanuvchilar</b> ({total} ta)",
        reply_markup=kb_admin_users_page(page, total, uid_list)
    )


async def show_admin_shops_list(msg):
    shops = db("SELECT * FROM shops ORDER BY created DESC", fetch=True)
    if not shops:
        await msg.answer("🛒 Do'konlar yo'q")
        return
    text = "🛒 <b>Do'konlar ro'yxati</b>\n\n"
    rows = []
    for s in shops:
        d      = days_left(s["expires"])
        status = "✅" if s["status"] == "active" and d > 0 else "❌"
        text  += f"{status} <b>{s['shop_name']}</b> | 👤 <code>{s['user_id']}</code> | {d} kun\n"
        rows.append([InlineKeyboardButton(
            text=f"{status} {s['shop_name']} ({d} kun)",
            callback_data=f"ashop_{s['id']}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_back")])
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def show_channels_list(msg):
    channels = db("SELECT * FROM channels", fetch=True)
    text = "📢 <b>Majburiy kanallar</b>\n\n"
    rows = []
    if channels:
        for ch in channels:
            status = "✅" if ch["required"] else "❌"
            text  += f"{status} {ch['title']} (<code>{ch['channel_id']}</code>)\n"
            rows.append([InlineKeyboardButton(
                text=f"{'✅' if ch['required'] else '❌'} {ch['title']}",
                callback_data=f"ch_toggle_{ch['id']}"
            ), InlineKeyboardButton(
                text="🗑 O'chirish",
                callback_data=f"ch_del_{ch['id']}"
            )])
    else:
        text += "Hozircha kanal qo'shilmagan\n"

    text += "\n<i>Bot admin bo'lishi shart emas, lekin kanal public bo'lishi kerak</i>"
    rows.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="add_channel")])
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def process_shops(msg: Message):
    shop  = get_shop(msg.from_user.id)
    price = get_setting("shop_price", str(SHOP_PRICE))
    if not shop:
        await msg.answer(
            f"🛒 <b>Do'konlarim</b>\n\n"
            f"Sizda hali do'kon yo'q.\n\n"
            f"📦 <b>Do'kon ochish:</b>\n"
            f"• Oylik obuna: <b>{fmts(price)}</b>\n"
            f"• O'z Humo kartangiz orqali to'lovlar qabul qilasiz\n"
            f"• Avtomatik tasdiqlash tizimi\n"
            f"• API key — bot.php ga ulash",
            reply_markup=kb_shops(None),
        )
    else:
        d      = days_left(shop["expires"])
        status = "✅ Aktiv" if shop["status"] == "active" and d > 0 else "❌ Muddati tugagan"
        await msg.answer(
            f"🛒 <b>Do'konlarim</b>\n\n"
            f"Do'kon: <b>{shop['shop_name']}</b>\n"
            f"Holat: {status}\n"
            f"⏳ Qolgan kun: <b>{d} kun</b>\n"
            f"💳 Karta: <code>{shop['card_number']}</code>",
            reply_markup=kb_shops(shop),
        )


async def process_profile(msg: Message):
    u    = get_user(msg.from_user.id)
    cnt  = db("SELECT COUNT(*) AS c FROM orders WHERE user_id=? AND status='paid'", (msg.from_user.id,), one=True)
    shop = get_shop(msg.from_user.id)
    shop_line = ""
    if shop:
        d = days_left(shop["expires"])
        shop_line = f"\n🛒 Do'kon: <b>{shop['shop_name']}</b> ({d} kun qoldi)"
    reg = u["reg"] if u else "-"
    await msg.answer(
        f"👤 <b>Profil</b>\n\n"
        f"Ism: <b>{msg.from_user.full_name}</b>\n"
        f"ID: <code>{msg.from_user.id}</code>\n"
        f"Username: @{msg.from_user.username or 'yoq'}\n"
        f"Ro'yxatdan o'tgan: <b>{reg[:10]}</b>\n\n"
        f"📦 To'lovlar: <b>{cnt['c']} ta</b>"
        f"{shop_line}"
    )


async def process_api_info(msg: Message):
    shop = get_shop(msg.from_user.id)
    if not shop:
        await msg.answer(
            "⚙️ <b>API Tizimi</b>\n\n"
            "API dan foydalanish uchun avval do'kon oching.\n\n"
            "Do'konlarim → Do'kon ochish"
        )
        return
    host = "http://SERVER_IP:8000"
    await msg.answer(
        f"⚙️ <b>API Tizimi</b>\n\n"
        f"🔑 API Key:\n<code>{shop['api_key']}</code>\n\n"
        f"📖 <b>Endpointlar:</b>\n\n"
        f"1️⃣ Order yaratish:\n<code>POST {host}/api/create_order</code>\n\n"
        f"2️⃣ Status tekshirish:\n<code>GET {host}/api/status/{{order_id}}?api_key=...</code>\n\n"
        f"3️⃣ Do'kon ma'lumoti:\n<code>GET {host}/api/shops/info?api_key=...</code>\n\n"
        f"📋 <b>bot.php ga ulash:</b>\n<code>HUMO_API_KEY = '{shop['api_key']}'</code>"
    )


async def process_topup_start(msg: Message, state: FSMContext):
    await state.set_state(S.amount)
    await msg.answer(
        f"💰 <b>Pul kiritish</b>\n\n"
        f"Karta: <code>{CARD_NUMBER}</code>\n"
        f"Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"Qancha pul kiritmoqchisiz? (so'mda)\n"
        f"Min: {fmts(MIN_AMOUNT)} | Max: {fmts(MAX_AMOUNT)}\n\n"
        f"<i>Bekor qilish: /cancel</i>"
    )


# ══════════════════════════════════════════════
#          CALLBACK HANDLERLARI
# ══════════════════════════════════════════════

@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    await process_profile(call.message)


@router.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    await call.message.edit_text(
        "🤖 <b>Bot haqida</b>\n\n"
        "✅ Humo karta orqali avto to'lov\n"
        "✅ 5 daqiqada avtomatik tasdiqlash\n"
        "✅ Do'kon tizimi — oylik obuna\n"
        "✅ API integratsiya — bot.php ga ulash\n"
        "✅ Webhook orqali avtomatik bildirishnoma",
        reply_markup=kb_back(),
    )


@router.callback_query(F.data == "api_info")
async def cb_api_info(call: CallbackQuery):
    await process_api_info(call.message)


@router.callback_query(F.data == "shops")
async def cb_shops(call: CallbackQuery):
    await process_shops(call.message)


@router.callback_query(F.data == "shop_info")
async def cb_shop_info(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True); return
    d = days_left(shop["expires"])
    await call.message.edit_text(
        f"📊 <b>Do'kon ma'lumotlari</b>\n\n"
        f"📌 Nom: <b>{shop['shop_name']}</b>\n"
        f"💳 Karta: <code>{shop['card_number']}</code>\n"
        f"📱 Telefon: <code>{shop['phone']}</code>\n"
        f"🔢 API ID: <code>{shop['api_id']}</code>\n"
        f"⏳ Obuna tugaydi: <b>{shop['expires'][:10]}</b> ({d} kun)\n"
        f"📅 Ochilgan: {shop['created'][:10]}\n"
        f"🔑 API key: <code>{shop['api_key']}</code>",
        reply_markup=kb_back("shops"),
    )


@router.callback_query(F.data == "shop_apikey")
async def cb_shop_apikey(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True); return
    await call.message.edit_text(
        f"🔑 <b>API Key</b>\n\n<code>{shop['api_key']}</code>\n\n"
        f"⚠️ Bu kalitni hech kimga bermang!",
        reply_markup=kb_back("shops"),
    )


@router.callback_query(F.data == "topup")
async def cb_topup(call: CallbackQuery, state: FSMContext):
    await state.set_state(S.amount)
    await call.message.edit_text(
        f"💰 <b>Pul kiritish</b>\n\n"
        f"Karta: <code>{CARD_NUMBER}</code>\n"
        f"Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"Qancha pul kiritmoqchisiz? (so'mda)\n"
        f"Min: {fmts(MIN_AMOUNT)} | Max: {fmts(MAX_AMOUNT)}",
        reply_markup=kb_back(),
    )


# ══════════════════════════════════════════════
#           DO'KON OCHISH
# ══════════════════════════════════════════════

@router.callback_query(F.data == "shop_open")
async def cb_shop_open(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if shop:
        await call.answer("Sizda allaqachon do'kon bor!", show_alert=True); return
    price = int(get_setting("shop_price", str(SHOP_PRICE)))
    await call.message.edit_text(
        f"➕ <b>Do'kon ochish</b>\n\n"
        f"Do'kon ochish uchun oylik obuna to'lashingiz kerak.\n\n"
        f"💰 Narx: <b>{fmts(price)}</b> / oy",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ To'lash", callback_data="shop_pay")],
            [InlineKeyboardButton(text="❌ Bekor",   callback_data="back")],
        ]),
    )


@router.callback_query(F.data == "shop_pay")
async def cb_shop_pay(call: CallbackQuery):
    price   = int(get_setting("shop_price", str(SHOP_PRICE)))
    amount  = unique_amount(float(price))
    extra   = int(amount - price)
    expires = time.time() + PAYMENT_TIME
    oid     = db("INSERT INTO orders(user_id, amount, base_amount, order_type) VALUES(?,?,?,?)",
                 (call.from_user.id, amount, price, "shop_sub"))
    PENDING[oid] = {
        "user_id":     call.from_user.id, "amount": amount, "base_amount": price,
        "chat_id":     call.message.chat.id, "msg_id": None,
        "expires":     expires, "confirmed": False, "order_type": "shop_sub",
    }
    exp_str    = datetime.fromtimestamp(expires).strftime("%H:%M:%S")
    extra_note = f"\n<i>({extra} so'm aniqlik uchun qo'shildi)</i>" if extra > 0 else ""
    pay_msg = await call.message.edit_text(
        f"💳 <b>Do'kon obuna to'lovi</b>\n\n"
        f"💰 To'lov summasi: <b>{fmts(amount)}</b>{extra_note}\n"
        f"🏦 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"⏳ Muddati: <b>{exp_str}</b> gacha\n\n"
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


# ── Obuna uzaytirish ──

@router.callback_query(F.data == "shop_renew")
async def cb_shop_renew(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True); return
    price   = int(get_setting("shop_price", str(SHOP_PRICE)))
    amount  = unique_amount(float(price))
    extra   = int(amount - price)
    expires = time.time() + PAYMENT_TIME
    oid     = db("INSERT INTO orders(user_id, amount, base_amount, order_type, shop_id) VALUES(?,?,?,?,?)",
                 (call.from_user.id, amount, price, "shop_renew", shop["id"]))
    PENDING[oid] = {
        "user_id":     call.from_user.id, "amount": amount, "base_amount": price,
        "chat_id":     call.message.chat.id, "msg_id": None,
        "expires":     expires, "confirmed": False,
        "order_type": "shop_renew", "shop_id": shop["id"],
    }
    exp_str    = datetime.fromtimestamp(expires).strftime("%H:%M:%S")
    extra_note = f"\n<i>({extra} so'm aniqlik uchun qo'shildi)</i>" if extra > 0 else ""
    d          = days_left(shop["expires"])
    pay_msg = await call.message.edit_text(
        f"💳 <b>Obunani uzaytirish</b>\n\n"
        f"Hozirgi holat: {d} kun qolgan\n\n"
        f"💰 To'lov summasi: <b>{fmts(amount)}</b>{extra_note}\n"
        f"🏦 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"⏳ Muddati: <b>{exp_str}</b> gacha\n\n"
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


# ══════════════════════════════════════════════
#          TO'LOV TUGMALARI
# ══════════════════════════════════════════════

@router.message(S.amount)
async def msg_amount(msg: Message, state: FSMContext):
    raw = msg.text.strip().replace(" ", "").replace(",", "")
    if not raw.isdigit():
        await msg.answer("❌ Faqat raqam kiriting!")
        return
    amt = int(raw)
    if amt < MIN_AMOUNT:
        await msg.answer(f"❌ Minimal summa: {fmts(MIN_AMOUNT)}")
        return
    if amt > MAX_AMOUNT:
        await msg.answer(f"❌ Maksimal summa: {fmts(MAX_AMOUNT)}")
        return
    await state.clear()
    amount  = unique_amount(float(amt))
    extra   = int(amount - amt)
    expires = time.time() + PAYMENT_TIME
    oid     = db("INSERT INTO orders(user_id, amount, base_amount, order_type) VALUES(?,?,?,?)",
                 (msg.from_user.id, amount, amt, "topup"))
    PENDING[oid] = {
        "user_id":     msg.from_user.id, "amount": amount, "base_amount": amt,
        "chat_id":     msg.chat.id, "msg_id": None,
        "expires":     expires, "confirmed": False, "order_type": "topup",
    }
    exp_str    = datetime.fromtimestamp(expires).strftime("%H:%M:%S")
    extra_note = f"\n<i>({extra} so'm aniqlik uchun qo'shildi)</i>" if extra > 0 else ""
    pay_msg = await msg.answer(
        f"💳 <b>To'lov</b>\n\n"
        f"💰 Summa: <b>{fmts(amount)}</b>{extra_note}\n"
        f"🏦 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"⏳ Muddati: <b>{exp_str}</b> gacha\n\n"
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


@router.callback_query(F.data.startswith("paid_"))
async def cb_paid(call: CallbackQuery):
    oid = int(call.data.split("_")[1])
    p   = PENDING.get(oid)
    if not p:
        await call.answer("❌ Order topilmadi yoki muddati o'tgan!", show_alert=True); return
    if p.get("confirmed"):
        await call.answer("✅ Allaqachon tasdiqlangan!", show_alert=True); return
    await call.answer("⏳ To'lov tekshirilmoqda...\n5 daqiqa ichida avtomatik tasdiqlanadi.", show_alert=True)


@router.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(call: CallbackQuery):
    oid = int(call.data.split("_")[1])
    p   = PENDING.pop(oid, None)
    if p:
        db("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    await call.message.edit_text("❌ <b>To'lov bekor qilindi.</b>")


async def _timer(oid: int):
    await asyncio.sleep(PAYMENT_TIME)
    p = PENDING.get(oid)
    if p and not p.get("confirmed"):
        PENDING.pop(oid, None)
        db("UPDATE orders SET status='expired' WHERE id=?", (oid,))
        cid = p.get("chat_id")
        mid = p.get("msg_id")
        if cid:
            try:
                if mid:
                    await bot.edit_message_text("⏰ <b>To'lov muddati tugadi.</b>\n\nQaytadan urinib ko'ring.",
                                                chat_id=cid, message_id=mid)
                else:
                    await bot.send_message(cid, "⏰ <b>To'lov muddati tugadi.</b>")
            except Exception:
                pass


# ══════════════════════════════════════════════
#           CONFIRM
# ══════════════════════════════════════════════

async def confirm(oid: int):
    p = PENDING.get(oid)
    if not p or p.get("confirmed"):
        return
    p["confirmed"] = True
    PENDING.pop(oid, None)
    uid         = p["user_id"]
    amount      = p["amount"]
    base_amount = p.get("base_amount", amount)
    cid         = p.get("chat_id")
    order_type  = p.get("order_type", "topup")
    now_dt      = datetime.now()
    now_str     = now_dt.strftime("%H:%M:%S %d.%m.%Y")
    db("UPDATE orders SET status='paid', paid=? WHERE id=?", (now_dt.strftime("%Y-%m-%d %H:%M:%S"), oid))

    if order_type == "topup":
        db("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
        if cid:
            try:
                await bot.send_message(cid,
                    f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
                    f"💰 Summa: <b>{fmts(amount)}</b>\n🧾 Order: <code>#{oid}</code>\n🕐 {now_str}")
            except Exception as e:
                log.error(f"[CONFIRM] xato: {e}")

    elif order_type == "shop_sub":
        if cid:
            await start_shop_setup(uid, cid)

    elif order_type == "external":
        webhook_url = p.get("webhook_url")
        ext_user_id = p.get("ext_user_id")
        shop_id     = p.get("shop_id")
        if webhook_url:
            await send_webhook(webhook_url, {
                "event": "payment_confirmed", "order_id": oid,
                "user_id": ext_user_id, "amount": base_amount,
                "shop_id": shop_id, "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })

    elif order_type == "shop_renew":
        shop_id = p.get("shop_id")
        if shop_id:
            shop = db("SELECT * FROM shops WHERE id=?", (shop_id,), one=True)
            if shop:
                try:
                    base_dt = datetime.strptime(shop["expires"], "%Y-%m-%d %H:%M:%S")
                    if base_dt < datetime.now():
                        base_dt = datetime.now()
                except Exception:
                    base_dt = datetime.now()
                new_exp = (base_dt + timedelta(days=SHOP_DURATION)).strftime("%Y-%m-%d %H:%M:%S")
                db("UPDATE shops SET expires=?, status='active' WHERE id=?", (new_exp, shop_id))
                if cid:
                    try:
                        await bot.send_message(cid,
                            f"✅ <b>Obuna uzaytirildi!</b>\n\n💰 Summa: <b>{fmts(amount)}</b>\n"
                            f"📅 Yangi tugash: <b>{new_exp[:10]}</b>\n🕐 {now_str}")
                    except Exception:
                        pass

    # Admin xabari
    for aid in ADMIN_IDS:
        try:
            u        = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
            name     = u["name"] if u else str(uid)
            type_txt = {
                "topup":      "Balans to'ldirish",
                "shop_sub":   "Do'kon ochish",
                "shop_renew": "Obuna uzaytirish",
                "external":   f"Bot.php to'lovi (user={p.get('ext_user_id')})",
            }.get(order_type, order_type)
            await bot.send_message(aid,
                f"💰 <b>Yangi to'lov!</b>\n\n"
                f"👤 {name} (<code>{uid}</code>)\n"
                f"📦 Tur: {type_txt}\n"
                f"💵 <b>{fmts(amount)}</b>\n"
                f"🧾 Order #{oid} | 🕐 {now_str}")
        except Exception:
            pass


# ══════════════════════════════════════════════
#              DO'KON SETUP
# ══════════════════════════════════════════════

async def start_shop_setup(user_id: int, chat_id: int):
    SETUP_PENDING[user_id] = {"step": "name", "chat_id": chat_id}
    await bot.send_message(chat_id,
        "✅ <b>To'lov tasdiqlandi! Do'kon sozlanmoqda...</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n📌 <b>1-qadam: Do'kon nomi</b>\n\n"
        "Do'kon nomini kiriting:\n<i>Misol: MyShop, Online Store...</i>")


async def _send_phone_code(user_id: int, chat_id: int, phone: str):
    setup = SETUP_PENDING.get(user_id)
    if not setup:
        return
    old_client = setup.get("client")
    if old_client:
        try:
            await old_client.disconnect()
        except Exception:
            pass
    client = TelegramClient(StringSession(), setup["api_id"], setup["api_hash"])
    await client.connect()
    sent = await client.send_code_request(phone)
    setup["client"] = client
    setup["phone"]  = phone
    setup["phone_code_hash"] = sent.phone_code_hash
    setup["step"] = "code"
    await bot.send_message(chat_id,
        f"✅ Kod yuborildi!\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>6-qadam: Telegram kodi</b>\n\n"
        f"📱 <code>{phone}</code> ga Telegram kodi keldi.\nKodni kiriting:")


async def _finish_shop_setup(msg: Message, client: TelegramClient, setup: dict):
    uid            = msg.from_user.id
    me             = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()
    shop_name = setup["shop_name"]
    card      = setup["card_number"]
    api_id    = setup["api_id"]
    api_hash  = setup["api_hash"]
    phone     = setup["phone"]
    api_key   = "sk_" + secrets.token_hex(16)
    expires   = (datetime.now() + timedelta(days=SHOP_DURATION)).strftime("%Y-%m-%d %H:%M:%S")
    db("INSERT OR REPLACE INTO shops(user_id,shop_name,card_number,api_id,api_hash,phone,string_session,api_key,status,expires)"
       " VALUES(?,?,?,?,?,?,?,?,?,?)",
       (uid, shop_name, card, api_id, api_hash, phone, session_string, api_key, "active", expires))
    SETUP_PENDING.pop(uid, None)
    PENDING_AUTH.pop(uid, None)
    shop = get_shop(uid)
    asyncio.create_task(start_shop_poller(shop))
    host = "http://SERVER_IP:8000"
    await msg.answer(
        f"🎉 <b>Do'kon muvaffaqiyatli ochildi!</b>\n\n"
        f"📌 Nom: <b>{shop_name}</b>\n💳 Karta: <code>{card}</code>\n"
        f"📱 Telefon: <code>{phone}</code>\n👤 Telegram: {me.first_name}\n"
        f"📅 Obuna tugaydi: <b>{expires[:10]}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>API Key:</b>\n<code>{api_key}</code>\n\n"
        f"🌐 <b>API URL:</b>\n<code>{host}/api/create_order</code>\n\n"
        f"⚠️ API keyni saqlang!",
        reply_markup=rkb_main(),
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid,
                f"🛒 <b>Yangi do'kon ochildi!</b>\n\n"
                f"👤 {msg.from_user.full_name} (<code>{uid}</code>)\n"
                f"📌 {shop_name}\n💳 {card}\n📱 {phone}")
        except Exception:
            pass


@router.message(F.text, StateFilter(None))
async def setup_message_handler(msg: Message, state: FSMContext):
    uid   = msg.from_user.id
    setup = SETUP_PENDING.get(uid)
    if not setup:
        return
    step    = setup.get("step")
    chat_id = setup["chat_id"]
    text    = (msg.text or "").strip()

    if step == "name":
        if len(text) < 2 or len(text) > 50:
            await msg.answer("❌ Nom 2-50 ta belgi bo'lishi kerak!"); return
        setup["shop_name"] = text
        setup["step"]      = "card"
        await msg.answer(f"✅ Nom: <b>{text}</b>\n\n━━━━━━━━━━━━━━━━━━━━━━\n📌 <b>2-qadam: Karta raqami</b>\n\nHumo karta raqamingizni kiriting:\n<i>Misol: 9860 1234 5678 9012</i>")
        return

    if step == "card":
        card = text.replace(" ", "")
        if not card.isdigit() or len(card) != 16:
            await msg.answer("❌ Karta raqami 16 ta raqamdan iborat bo'lishi kerak!"); return
        formatted            = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
        setup["card_number"] = formatted
        setup["step"]        = "api_id"
        await msg.answer(f"✅ Karta: <code>{formatted}</code>\n\n━━━━━━━━━━━━━━━━━━━━━━\n📌 <b>3-qadam: Telegram API ID</b>\n\n<a href='https://my.telegram.org'>my.telegram.org</a> dan API ID oling:", disable_web_page_preview=True)
        return

    if step == "api_id":
        if not text.isdigit():
            await msg.answer("❌ API ID faqat raqamlardan iborat!"); return
        setup["api_id"] = int(text)
        setup["step"]   = "api_hash"
        await msg.answer(f"✅ API ID: <code>{text}</code>\n\n━━━━━━━━━━━━━━━━━━━━━━\n📌 <b>4-qadam: API HASH</b>\n\n<a href='https://my.telegram.org'>my.telegram.org</a> dan API HASH oling:", disable_web_page_preview=True)
        return

    if step == "api_hash":
        if len(text) < 10:
            await msg.answer("❌ API HASH noto'g'ri!"); return
        setup["api_hash"] = text
        setup["step"]     = "phone"
        await msg.answer("✅ API HASH qabul qilindi.\n\n━━━━━━━━━━━━━━━━━━━━━━\n📌 <b>5-qadam: Telefon raqami</b>\n\n<i>Misol: +998901234567</i>")
        return

    if step == "phone":
        if not text.startswith("+") or len(text) < 10:
            await msg.answer("❌ Telefon raqamini to'g'ri formatda kiriting!"); return
        wait_msg = await msg.answer("⏳ Kod yuborilmoqda...")
        try:
            await _send_phone_code(uid, chat_id, text)
            await wait_msg.delete()
        except Exception as e:
            await wait_msg.edit_text(f"❌ Xato:\n<code>{e}</code>\n\nQaytadan telefon raqamini kiriting:")
            setup["step"] = "phone"
        return

    if step == "code":
        code = text.replace(" ", "")
        if not code.isdigit():
            await msg.answer("❌ Faqat raqam kiriting!"); return
        client = setup.get("client")
        phone  = setup.get("phone")
        phone_code_hash = setup.get("phone_code_hash")
        if not client or not phone:
            await msg.answer("❌ Session muddati o'tgan. Telefon raqamini qaytadan kiriting:")
            setup["step"] = "phone"; return
        wait_msg = await msg.answer("⏳ Tekshirilmoqda...")
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await wait_msg.delete()
            await _finish_shop_setup(msg, client, setup)
        except SessionPasswordNeededError:
            setup["step"] = "twofa"
            await wait_msg.edit_text("🔐 <b>2FA parol kerak</b>\n\nParolni kiriting:")
        except PhoneCodeInvalidError:
            await wait_msg.edit_text("❌ Kod noto'g'ri! Qaytadan kiriting:")
        except PhoneCodeExpiredError:
            await wait_msg.edit_text("⏳ Kod eskirdi, yangi yuborilmoqda...")
            try:
                await _send_phone_code(uid, chat_id, phone)
                await wait_msg.delete()
            except Exception as e:
                await wait_msg.edit_text(f"❌ Xato:\n<code>{e}</code>")
                setup["step"] = "phone"
        except Exception as e:
            await wait_msg.edit_text(f"❌ Xato: <code>{e}</code>\n\nTelefon raqamini qaytadan kiriting:")
            setup["step"] = "phone"
        return

    if step == "twofa":
        client = setup.get("client")
        if not client:
            await msg.answer("❌ Session topilmadi.")
            setup["step"] = "phone"; return
        wait_msg = await msg.answer("⏳ Parol tekshirilmoqda...")
        try:
            await client.sign_in(password=text)
            await wait_msg.delete()
            await _finish_shop_setup(msg, client, setup)
        except Exception as e:
            await wait_msg.edit_text(f"❌ Parol xato:\n<code>{e}</code>\n\nQaytadan kiriting:")
        return


# ══════════════════════════════════════════════
#          ADMIN — TO'LIQ PANEL
# ══════════════════════════════════════════════

@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await show_admin_stats(msg)


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Bekor qilindi.", reply_markup=rkb_main() if msg.from_user.id not in ADMIN_IDS else rkb_admin())


# ── Admin FSM handlerlari ──

@router.message(S.admin_price)
async def msg_admin_price(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    raw = msg.text.strip().replace(" ", "").replace(",", "")
    if not raw.isdigit():
        await msg.answer("❌ Faqat raqam kiriting!"); return
    new_price = int(raw)
    set_setting("shop_price", str(new_price))
    await state.clear()
    await msg.answer(f"✅ Do'kon narxi o'zgartirildi!\nYangi narx: <b>{fmts(new_price)}</b> / oy")


@router.message(S.admin_broadcast)
async def msg_admin_broadcast(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    users = db("SELECT id FROM users WHERE is_banned=0", fetch=True)
    sent = failed = 0
    status_msg = await msg.answer(f"📨 Yuborilmoqda... 0/{len(users)}")
    for i, u in enumerate(users):
        try:
            await bot.copy_message(u["id"], msg.chat.id, msg.message_id)
            sent += 1
        except Exception:
            failed += 1
        if (i + 1) % 20 == 0:
            try:
                await status_msg.edit_text(f"📨 Yuborilmoqda... {i+1}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    bid = db("INSERT INTO broadcasts(admin_id, text, sent, failed) VALUES(?,?,?,?)",
             (msg.from_user.id, msg.text or "[media]", sent, failed))
    await status_msg.edit_text(
        f"✅ <b>Xabar yuborildi!</b>\n\n"
        f"📨 Yuborildi: <b>{sent} ta</b>\n"
        f"❌ Xato: <b>{failed} ta</b>\n"
        f"📊 Jami: <b>{len(users)} ta</b>"
    )


@router.message(S.admin_add_channel)
async def msg_admin_add_channel(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.strip()
    await state.clear()
    # Forward yoki username/ID
    channel_id = text
    title      = text
    username   = None
    if text.startswith("@"):
        username   = text[1:]
        channel_id = text
        try:
            chat = await bot.get_chat(text)
            title      = chat.title
            channel_id = str(chat.id)
            username   = chat.username
        except Exception as e:
            await msg.answer(f"❌ Kanal topilmadi:\n<code>{e}</code>"); return
    elif text.lstrip("-").isdigit():
        channel_id = text
        try:
            chat   = await bot.get_chat(int(text))
            title  = chat.title
            username = chat.username
        except Exception as e:
            await msg.answer(f"❌ Kanal topilmadi:\n<code>{e}</code>"); return
    else:
        await msg.answer("❌ Kanal username (@kanal) yoki ID (-100...) kiriting!"); return

    db("INSERT OR IGNORE INTO channels(channel_id, title, username, required) VALUES(?,?,?,1)",
       (channel_id, title, username or ""))
    await msg.answer(f"✅ <b>{title}</b> kanali qo'shildi!\n\nFoydalanuvchilar endi shu kanalga obuna bo'lishi shart.")


@router.message(S.admin_user_search)
async def msg_admin_user_search(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.strip()
    await state.clear()
    if text.isdigit():
        u = db("SELECT * FROM users WHERE id=?", (int(text),), one=True)
    else:
        query = f"%{text}%"
        u = db("SELECT * FROM users WHERE username LIKE ? OR name LIKE ?", (query, query), one=True)
    if not u:
        await msg.answer("❌ Foydalanuvchi topilmadi!"); return
    await show_user_detail(msg, u)


@router.message(S.admin_balance_add)
async def msg_admin_balance_add(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    uid  = data.get("target_uid")
    raw  = msg.text.strip().replace(" ", "").replace(",", "")
    if not raw.lstrip("-").isdigit():
        await msg.answer("❌ Faqat raqam kiriting!"); return
    amount = float(raw)
    await state.clear()
    db("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
    u = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    await msg.answer(
        f"✅ Balans o'zgartirildi!\n\n"
        f"👤 Foydalanuvchi: <code>{uid}</code>\n"
        f"💰 Qo'shildi: <b>{fmts(amount)}</b>\n"
        f"💳 Yangi balans: <b>{fmts(u['balance'] if u else 0)}</b>"
    )
    try:
        if amount > 0:
            await bot.send_message(uid, f"💰 <b>Balansingizga {fmts(amount)} qo'shildi!</b>\n\nAdmin tomonidan.")
    except Exception:
        pass


@router.message(S.admin_welcome_text)
async def msg_admin_welcome(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    set_setting("welcome_text", msg.text or "")
    await msg.answer("✅ Welcome xabar o'zgartirildi!\n\n<i>{name} — foydalanuvchi ismi</i>")


# ── Admin inline callback ──

@router.callback_query(F.data == "admin_pending")
async def cb_admin_pending(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    if not PENDING:
        await call.answer("Kutilayotgan to'lovlar yo'q!", show_alert=True); return
    text = "🔄 <b>Kutilayotgan to'lovlar</b>\n\n"
    for oid, p in list(PENDING.items()):
        rem  = max(0, int(p["expires"] - time.time()))
        text += f"#{oid} | {fmts(p['amount'])} | {rem}s qoldi | {p['order_type']}\n"
    await call.message.edit_text(text, reply_markup=kb_back("admin_back"))


@router.callback_query(F.data == "toggle_bot")
async def cb_toggle_bot(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = get_setting("bot_active", "1")
    new_val = "0" if current == "1" else "1"
    set_setting("bot_active", new_val)
    status = "✅ Yoqildi" if new_val == "1" else "❌ To'xtatildi"
    await call.answer(f"Bot {status}", show_alert=True)


@router.callback_query(F.data == "edit_welcome")
async def cb_edit_welcome(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = get_setting("welcome_text")
    await state.set_state(S.admin_welcome_text)
    await call.message.edit_text(
        f"✏️ <b>Welcome xabarni o'zgartirish</b>\n\n"
        f"Hozirgi:\n{current}\n\n"
        f"<i>{{name}} — foydalanuvchi ismi</i>\n\n"
        f"Yangi xabarni yuboring:"
    )


@router.callback_query(F.data == "add_channel")
async def cb_add_channel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(S.admin_add_channel)
    await call.message.edit_text(
        "➕ <b>Kanal qo'shish</b>\n\n"
        "Kanal username yoki ID kiriting:\n"
        "<i>Misol: @mening_kanal yoki -1001234567890</i>\n\n"
        "⚠️ Bot kanalning a'zosi bo'lishi shart (admin bo'lishi shart emas)"
    )


@router.callback_query(F.data.startswith("ch_toggle_"))
async def cb_ch_toggle(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    cid_row = int(call.data.split("_")[2])
    ch = db("SELECT * FROM channels WHERE id=?", (cid_row,), one=True)
    if ch:
        new_req = 0 if ch["required"] else 1
        db("UPDATE channels SET required=? WHERE id=?", (new_req, cid_row))
        status = "yoqildi" if new_req else "o'chirildi"
        await call.answer(f"✅ Obuna majburiyati {status}", show_alert=True)
    await show_channels_list(call.message)


@router.callback_query(F.data.startswith("ch_del_"))
async def cb_ch_del(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    cid_row = int(call.data.split("_")[2])
    db("DELETE FROM channels WHERE id=?", (cid_row,))
    await call.answer("🗑 Kanal o'chirildi", show_alert=True)
    await show_channels_list(call.message)


@router.callback_query(F.data == "admin_users_list")
async def cb_admin_users_list(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await show_users_list(call.message, page=0)


@router.callback_query(F.data.startswith("users_page_"))
async def cb_users_page(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    page  = int(call.data.split("_")[2])
    users = db("SELECT id FROM users ORDER BY reg DESC", fetch=True)
    uid_list = [u["id"] for u in users]
    await call.message.edit_reply_markup(
        reply_markup=kb_admin_users_page(page, len(uid_list), uid_list)
    )


@router.callback_query(F.data == "admin_user_search")
async def cb_admin_user_search(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(S.admin_user_search)
    await call.message.edit_text(
        "🔍 <b>Foydalanuvchi qidirish</b>\n\n"
        "ID, username yoki ism kiriting:"
    )


@router.callback_query(F.data.startswith("auser_"))
async def cb_auser(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    u   = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        await call.answer("Foydalanuvchi topilmadi!", show_alert=True); return
    await show_user_detail(call.message, u, edit=True)


async def show_user_detail(msg, u, edit=False):
    shop     = get_shop(u["id"])
    orders   = db("SELECT COUNT(*) AS c, SUM(amount) AS s FROM orders WHERE user_id=? AND status='paid'", (u["id"],), one=True)
    ban_icon = "🚫" if u["is_banned"] else "✅"
    text = (
        f"👤 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
        f"ID: <code>{u['id']}</code>\n"
        f"Ism: <b>{u['name'] or '-'}</b>\n"
        f"Username: @{u['username'] or 'yoq'}\n"
        f"Holat: {ban_icon} {'Bloklangan' if u['is_banned'] else 'Aktiv'}\n"
        f"💰 Balans: <b>{fmts(u['balance'])}</b>\n"
        f"📦 To'lovlar: <b>{orders['c']} ta | {fmts(orders['s'] or 0)}</b>\n"
        f"📅 Ro'yxatdan: {u['reg'][:10]}\n"
    )
    if shop:
        d     = days_left(shop["expires"])
        text += f"\n🛒 Do'kon: <b>{shop['shop_name']}</b> ({d} kun)"

    kb = kb_user_detail(u["id"], u["is_banned"])
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ban_"))
async def cb_ban_user(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    db("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
    await call.answer("🚫 Foydalanuvchi banlandi!", show_alert=True)
    u = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if u:
        await show_user_detail(call.message, u, edit=True)


@router.callback_query(F.data.startswith("unban_"))
async def cb_unban_user(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    db("UPDATE users SET is_banned=0 WHERE id=?", (uid,))
    await call.answer("✅ Ban olib tashlandi!", show_alert=True)
    u = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if u:
        await show_user_detail(call.message, u, edit=True)


@router.callback_query(F.data.startswith("addbal_"))
async def cb_addbal(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    await state.set_state(S.admin_balance_add)
    await state.update_data(target_uid=uid)
    u = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    await call.message.edit_text(
        f"💰 <b>Balans qo'shish</b>\n\n"
        f"Foydalanuvchi: <code>{uid}</code>\n"
        f"Hozirgi balans: <b>{fmts(u['balance'] if u else 0)}</b>\n\n"
        f"Qancha qo'shish? (manfiy raqam — ayirish)\n"
        f"<i>Bekor qilish: /cancel</i>"
    )


@router.callback_query(F.data.startswith("msguser_"))
async def cb_msguser(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    await state.update_data(broadcast_target=uid)
    await call.message.edit_text(
        f"📨 <b>Foydalanuvchiga xabar</b>\n\n"
        f"ID: <code>{uid}</code>\n\nXabarni yuboring:"
    )
    # Simple broadcast to one user via existing broadcast state
    await state.set_state(S.admin_broadcast)
    await state.update_data(broadcast_target=uid)


@router.callback_query(F.data.startswith("ashop_"))
async def cb_ashop(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    shop_id = int(call.data.split("_")[1])
    shop = db("SELECT * FROM shops WHERE id=?", (shop_id,), one=True)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True); return
    d = days_left(shop["expires"])
    await call.message.edit_text(
        f"🛒 <b>Do'kon ma'lumotlari</b>\n\n"
        f"📌 Nom: <b>{shop['shop_name']}</b>\n"
        f"👤 Egasi: <code>{shop['user_id']}</code>\n"
        f"💳 Karta: <code>{shop['card_number']}</code>\n"
        f"📱 Telefon: <code>{shop['phone']}</code>\n"
        f"⏳ Obuna: <b>{shop['expires'][:10]}</b> ({d} kun)\n"
        f"🔑 API: <code>{shop['api_key']}</code>\n"
        f"Holat: {'✅ Aktiv' if shop['status']=='active' and d>0 else '❌ Muddati tugagan'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ +30 kun qo'shish", callback_data=f"shop_adddays_{shop_id}")],
            [InlineKeyboardButton(text="🚫 Do'konni bloklash" if shop["status"]=="active" else "✅ Faollashtirish",
                                  callback_data=f"shop_toggle_{shop_id}")],
            [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"shop_del_{shop_id}")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_back")],
        ])
    )


@router.callback_query(F.data.startswith("shop_adddays_"))
async def cb_shop_adddays(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    shop_id = int(call.data.split("_")[2])
    shop = db("SELECT * FROM shops WHERE id=?", (shop_id,), one=True)
    if shop:
        try:
            base_dt = datetime.strptime(shop["expires"], "%Y-%m-%d %H:%M:%S")
            if base_dt < datetime.now():
                base_dt = datetime.now()
        except Exception:
            base_dt = datetime.now()
        new_exp = (base_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        db("UPDATE shops SET expires=?, status='active' WHERE id=?", (new_exp, shop_id))
        await call.answer(f"✅ +30 kun qo'shildi! Yangi: {new_exp[:10]}", show_alert=True)
        try:
            await bot.send_message(shop["user_id"],
                f"🎁 <b>Do'kon obunangiz uzaytirildi!</b>\n\n"
                f"Do'kon: <b>{shop['shop_name']}</b>\n"
                f"Yangi tugash: <b>{new_exp[:10]}</b>\n\n"
                f"Admin tomonidan qo'shildi.")
        except Exception:
            pass


@router.callback_query(F.data.startswith("shop_toggle_"))
async def cb_shop_toggle(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    shop_id = int(call.data.split("_")[2])
    shop = db("SELECT * FROM shops WHERE id=?", (shop_id,), one=True)
    if shop:
        new_status = "inactive" if shop["status"] == "active" else "active"
        db("UPDATE shops SET status=? WHERE id=?", (new_status, shop_id))
        status_txt = "bloklandi" if new_status == "inactive" else "faollashtirildi"
        await call.answer(f"Do'kon {status_txt}!", show_alert=True)


@router.callback_query(F.data.startswith("shop_del_"))
async def cb_shop_del(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    shop_id = int(call.data.split("_")[2])
    db("DELETE FROM shops WHERE id=?", (shop_id,))
    await call.answer("🗑 Do'kon o'chirildi!", show_alert=True)
    await show_admin_shops_list(call.message)


@router.callback_query(F.data == "admin_price")
async def cb_admin_price(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    price = get_setting("shop_price", str(SHOP_PRICE))
    await state.set_state(S.admin_price)
    await call.message.edit_text(
        f"💰 <b>Do'kon narxini o'zgartirish</b>\n\n"
        f"Hozirgi narx: <b>{fmts(price)}</b> / oy\n\n"
        f"Yangi narxni kiriting:", reply_markup=kb_back()
    )


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


# ══════════════════════════════════════════════
#           HUMO KARTA PARSER
# ══════════════════════════════════════════════

def humo_parse(text: str) -> Optional[float]:
    if not text:
        return None
    log.info(f"[PARSE] Xabar:\n{text}")
    lines = text.splitlines()
    is_incoming = False
    for line in lines:
        s = line.strip()
        if any(kw in s for kw in ["To'ldirish", "Тўлдириш", "Toʻldirish", "Пополнение", "🎉"]):
            is_incoming = True; break
        if any(kw in s for kw in ["To'lov", "Тўлов", "Toʻlov", "Платёж", "🔀"]):
            log.info("[PARSE] ❌ Chiquvchi to'lov"); return None
    if not is_incoming:
        log.info("[PARSE] ❌ Kiruvchi emas"); return None
    clean_lines = []
    for line in lines:
        s = re.sub(r"\*\*|__", "", line.strip())
        clean_lines.append(s.strip())
    for s in clean_lines:
        has_plus = s.startswith("+") or s.startswith("➕")
        if not has_plus or "UZS" not in s.upper():
            continue
        s = re.sub(r"^➕", "+", s)
        m = re.match(r"^\+\s*(\d{1,3}(?:\.\d{3})*,\d{1,2})\s*UZS", s, re.IGNORECASE)
        if m:
            raw = m.group(1)
            int_p, dec_p = raw.rsplit(",", 1)
            try:
                val = float(f"{int_p.replace('.', '')}.{dec_p}")
                if val > 0: return val
            except ValueError:
                pass
        m = re.match(r"^\+\s*(\d{1,3}(?:\s\d{3})*,\d{1,2})\s*UZS", s, re.IGNORECASE)
        if m:
            raw = m.group(1)
            int_p, dec_p = raw.rsplit(",", 1)
            try:
                val = float(f"{int_p.replace(' ', '')}.{dec_p}")
                if val > 0: return val
            except ValueError:
                pass
        m = re.match(r"^\+\s*(\d[\d\s]*)\s*UZS", s, re.IGNORECASE)
        if m:
            try:
                val = float(re.sub(r"\s", "", m.group(1)))
                if val > 0: return val
            except ValueError:
                pass
    log.info("[PARSE] ❌ Summa topilmadi")
    return None


def find_order(amount: float) -> Optional[int]:
    now, best_oid, best_diff = time.time(), None, float("inf")
    for oid, p in list(PENDING.items()):
        if p.get("confirmed") or now > p["expires"]:
            continue
        diff = abs(p["amount"] - amount)
        if diff < 1.0 and diff < best_diff:
            best_diff, best_oid = diff, oid
    return best_oid


# ══════════════════════════════════════════════
#           TELETHON POLLERLAR
# ══════════════════════════════════════════════
LAST_MSG_ID: dict = {}


async def run_poller(client: TelegramClient, label: str):
    humo_entity = None
    try:
        humo_entity = await client.get_entity(HUMO_BOT_USERNAME)
        log.info(f"[{label}] ✅ HUMOcardbot topildi")
    except Exception as e:
        log.error(f"[{label}] ❌ HUMOcardbot topilmadi: {e}"); return
    try:
        msgs = await client.get_messages(humo_entity, limit=1)
        if msgs:
            LAST_MSG_ID[label] = msgs[0].id
    except Exception:
        pass
    log.info(f"[{label}] ✅ Monitoring boshlandi")
    while True:
        try:
            if not client.is_connected():
                await client.connect()
            msgs = await client.get_messages(humo_entity, limit=5)
            for m in reversed(msgs):
                msg_id = m.id
                text   = m.text or ""
                last   = LAST_MSG_ID.get(label, 0)
                if msg_id <= last:
                    continue
                LAST_MSG_ID[label] = msg_id
                log.info(f"[{label}] 🆕 id={msg_id}")
                if not text:
                    continue
                amount = humo_parse(text)
                if amount is None:
                    continue
                log.info(f"[{label}] 💰 {amount} UZS")
                oid = find_order(amount)
                if oid is None:
                    log.info(f"[{label}] ❌ Mos order topilmadi"); continue
                log.info(f"[{label}] ✅ Order #{oid} → tasdiqlash")
                await confirm(oid)
        except Exception as e:
            log.error(f"[{label}] Xato: {type(e).__name__}: {e}")
        await asyncio.sleep(2)


async def start_shop_poller(shop):
    shop_id = shop["id"]
    if shop_id in SHOP_CLIENTS:
        return
    try:
        client = TelegramClient(StringSession(shop["string_session"]), shop["api_id"], shop["api_hash"])
        await client.connect()
        if not await client.is_user_authorized():
            log.error(f"[SHOP-{shop_id}] Session yaroqsiz!"); return
        me = await client.get_me()
        log.info(f"[SHOP-{shop_id}] ✅ {me.first_name}")
        SHOP_CLIENTS[shop_id] = client
        asyncio.create_task(run_poller(client, f"SHOP-{shop_id}"))
    except Exception as e:
        log.error(f"[SHOP-{shop_id}] Poller xato: {e}")


# ══════════════════════════════════════════════
#               ISHGA TUSHIRISH
# ══════════════════════════════════════════════

async def setup_admin_userbot() -> Optional[TelegramClient]:
    global ADMIN_SESSION_STR
    client = TelegramClient(StringSession(ADMIN_SESSION_STR), ADMIN_API_ID, ADMIN_API_HASH)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        log.info(f"✅ Admin userbot: {me.first_name} (@{me.username})")
        return client
    print("\n" + "═"*50)
    print("   📱 ADMIN USERBOT SESSION YARATISH")
    print("═"*50)
    print("\nTelefon raqamingizni kiriting (+998...):")
    phone = input(">>> ").strip()
    try:
        sent = await client.send_code_request(phone)
        print("\n📱 Telegram kodini kiriting:")
        code = input(">>> ").strip()
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            print("\n🔐 2FA paroli:")
            pwd = input(">>> ").strip()
            await client.sign_in(password=pwd)
        me             = await client.get_me()
        session_string = client.session.save()
        print(f"\n✅ Kirdi: {me.first_name}")
        print(f"\n💾 ADMIN_SESSION_STR:\n{session_string}\n")
        set_setting("admin_session", session_string)
        return client
    except Exception as e:
        print(f"\n❌ Xato: {e}")
        await client.disconnect()
        return None


async def subscription_checker():
    while True:
        await asyncio.sleep(3600)
        try:
            shops = db("SELECT * FROM shops WHERE status='active'", fetch=True)
            for shop in shops:
                if days_left(shop["expires"]) <= 0:
                    db("UPDATE shops SET status='expired' WHERE id=?", (shop["id"],))
                    try:
                        await bot.send_message(shop["user_id"],
                            f"⚠️ <b>Do'kon obunasi tugadi!</b>\n\n"
                            f"Do'kon: <b>{shop['shop_name']}</b>\n\n"
                            f"/start → Do'konlarim → Obunani uzaytirish")
                    except Exception:
                        pass
            # 3 kun qolganda ogohlantirish
            shops3 = db("SELECT * FROM shops WHERE status='active'", fetch=True)
            for shop in shops3:
                d = days_left(shop["expires"])
                if d == 3:
                    try:
                        await bot.send_message(shop["user_id"],
                            f"⏰ <b>Do'kon obunasi 3 kunda tugaydi!</b>\n\n"
                            f"Do'kon: <b>{shop['shop_name']}</b>\n\n"
                            f"/start → Do'konlarim → Obunani uzaytirish")
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"[SUB] Checker xato: {e}")


async def main():
    global admin_userbot
    init_db()

    saved_session = get_setting("admin_session", ADMIN_SESSION_STR)
    if saved_session:
        try:
            admin_userbot = TelegramClient(StringSession(saved_session), ADMIN_API_ID, ADMIN_API_HASH)
            await admin_userbot.connect()
            if not await admin_userbot.is_user_authorized():
                admin_userbot = None
        except Exception:
            admin_userbot = None

    if not admin_userbot:
        admin_userbot = await setup_admin_userbot()
        if not admin_userbot:
            print("❌ Admin userbot sessionsiz ishlay olmaydi!")
            return

    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(run_poller(admin_userbot, "ADMIN"))
    log.info("✅ Admin poller ishga tushdi")

    active_shops = db("SELECT * FROM shops WHERE status='active'", fetch=True)
    for shop in active_shops:
        if days_left(shop["expires"]) > 0:
            asyncio.create_task(start_shop_poller(shop))
            log.info(f"[BOOT] '{shop['shop_name']}' ishga tushdi")

    asyncio.create_task(subscription_checker())
    log.info("🚀 Bot ishga tushdi!")

    try:
        config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        await asyncio.gather(
            server.serve(),
            dp.start_polling(bot, skip_updates=True),
        )
    finally:
        if admin_userbot:
            await admin_userbot.disconnect()
        for client in SHOP_CLIENTS.values():
            await client.disconnect()
        log.info("👋 To'xtatildi")


if __name__ == "__main__":
    print()
    print("═" * 50)
    print("   💳  AVTO TO'LOV TIZIMI v9.0")
    print("   🤖  bot.php botlar uchun platforma")
    print("   🛒  Do'kon + API + Webhook + Admin Panel")
    print("═" * 50)
    print()
    print("O'rnatish: pip install aiogram telethon fastapi uvicorn httpx")
    print()
    asyncio.run(main())


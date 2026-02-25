import asyncio
import logging
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
)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.sessions import StringSession

BOT_TOKEN    = "8434963162:AAExAV5fIQBqMys_bCkzoF1MfeFk4bsFx-A"         # @BotFather dan oling
ADMIN_IDS    = [6302762403]              # Sizning Telegram ID

ADMIN_API_ID      = 39206752              # Admin o'z API ID si
ADMIN_API_HASH    = "82b55fc7b6349fe4e68205c6a29e6af6" # Admin o'z API HASH i
ADMIN_SESSION_STR = os.environ.get("ADMIN_SESSION_STR", "")

CARD_NUMBER  = "8600 1234 5678 9012"
CARD_OWNER   = "FAMILIYA ISM"
PAYMENT_TIME = 5 * 60
MIN_AMOUNT   = 1_000
MAX_AMOUNT   = 10_000_000

SHOP_PRICE      = 10_000
SHOP_DURATION   = 30
HUMO_BOT_USERNAME = "HUMOcardbot"


SHOP_PRICE    = 10_000
SHOP_DURATION = 30                 # kunlar

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
        id       INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        name     TEXT DEFAULT '',
        balance  REAL DEFAULT 0,
        reg      TEXT DEFAULT (datetime('now','localtime'))
    )""")
    db("""CREATE TABLE IF NOT EXISTS orders (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        amount     REAL,
        base_amount REAL,
        status     TEXT DEFAULT 'pending',
        order_type TEXT DEFAULT 'topup',
        shop_id    INTEGER,
        created    TEXT DEFAULT (datetime('now','localtime')),
        paid       TEXT
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
    existing = db("SELECT value FROM settings WHERE key='shop_price'", one=True)
    if not existing:
        db("INSERT INTO settings(key,value) VALUES('shop_price',?)", (str(SHOP_PRICE),))
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
#              PENDING TO'LOVLAR
# ══════════════════════════════════════════════
PENDING: dict = {}


def unique_amount(base: float) -> float:
    """Agar shu summa PENDING da bo'lsa +1 qo'shadi"""
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
#   bot.php bu endpointlardan foydalanadi
# ══════════════════════════════════════════════
api = FastAPI(title="Avto To'lov API", version="8.0")


# ─── Modellar ────────────────────────────────

class CreateOrderRequest(BaseModel):
    api_key:     str            # Do'konning API key (bot.py da berilgan)
    user_id:     str            # bot.php dagi foydalanuvchi Telegram ID
    amount:      float          # To'lov summasi (so'mda)
    webhook_url: str            # bot.php dagi webhook URL


class CreateOrderResponse(BaseModel):
    ok:          bool
    order_id:    Optional[int]   = None
    amount:      Optional[float] = None   # Unique summa (foydalanuvchiga ko'rsating)
    card_number: Optional[str]   = None   # Pul tashlash uchun karta
    expires_in:  Optional[int]   = None   # Necha sekundda tugaydi
    error:       Optional[str]   = None


# ─── Endpointlar ─────────────────────────────

@api.post("/api/create_order", response_model=CreateOrderResponse)
async def api_create_order(req: CreateOrderRequest):
    """
    bot.php bu endpointni chaqiradi:
    1. Foydalanuvchi "Hisob to'ldirish" bosadi
    2. bot.php bu yerga so'rov yuboradi
    3. Unique summa va karta raqami qaytariladi
    4. bot.php foydalanuvchiga ko'rsatadi
    5. To'lov kelganda webhook_url ga xabar yuboriladi
    """
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
        "INSERT INTO orders(user_id, amount, base_amount, order_type, shop_id)"
        " VALUES(?,?,?,?,?)",
        (shop["user_id"], amount, req.amount, "external", shop["id"])
    )

    PENDING[oid] = {
        "user_id":     shop["user_id"],       # Do'kon egasining ID si
        "ext_user_id": str(req.user_id),      # bot.php foydalanuvchisining ID si
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

    log.info(
        f"[API] Yangi order #{oid} | shop={shop['shop_name']} "
        f"| ext_user={req.user_id} | summa={amount}"
    )

    return CreateOrderResponse(
        ok=True,
        order_id=oid,
        amount=amount,
        card_number=shop["card_number"],
        expires_in=PAYMENT_TIME,
    )


@api.get("/api/status/{order_id}")
async def api_order_status(order_id: int, api_key: str):
    """
    To'lov holatini tekshirish.
    bot.php har 5-10 sekundda bu yerga so'rov yuborishi mumkin.
    """
    shop = get_shop_by_api_key(api_key)
    if not shop:
        raise HTTPException(status_code=403, detail="API key noto'g'ri")

    order = db(
        "SELECT * FROM orders WHERE id=? AND shop_id=?",
        (order_id, shop["id"]), one=True
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order topilmadi")

    p = PENDING.get(order_id)
    if p and not p.get("confirmed"):
        remaining = max(0, int(p["expires"] - time.time()))
        return {
            "ok": True,
            "status": "pending",
            "remaining": remaining,
            "amount": p["amount"],
            "card_number": p["card_number"],
        }

    return {
        "ok": True,
        "status": order["status"],
        "paid_at": order["paid"],
    }


@api.get("/api/shops/info")
async def api_shop_info(api_key: str):
    """Do'kon ma'lumotlarini olish"""
    shop = get_shop_by_api_key(api_key)
    if not shop:
        raise HTTPException(status_code=403, detail="API key noto'g'ri")
    return {
        "ok":          True,
        "shop_name":   shop["shop_name"],
        "card_number": shop["card_number"],
        "expires":     shop["expires"],
        "days_left":   days_left(shop["expires"]),
    }


@api.get("/api/ping")
async def api_ping():
    return {"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ══════════════════════════════════════════════
#   WEBHOOK — bot.php ga xabar yuborish
#   To'lov tasdiqlanganda chaqiriladi
# ══════════════════════════════════════════════

async def send_webhook(webhook_url: str, data: dict):
    """
    bot.php dagi webhook.php ga POST yuboradi.
    bot.php shu xabarni qabul qilib MySQL da balansni yangilaydi.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json=data)
            log.info(f"[WEBHOOK] {webhook_url} → {resp.status_code} | {resp.text[:100]}")
    except Exception as e:
        log.error(f"[WEBHOOK] Xato: {e}")


# ══════════════════════════════════════════════
#         SHOP TELETHON CLIENTLAR
# ══════════════════════════════════════════════
SHOP_CLIENTS: dict = {}
admin_userbot: Optional[TelegramClient] = None

# ══════════════════════════════════════════════
#       SETUP PENDING (do'kon ro'yxatdan o'tish)
# ══════════════════════════════════════════════
SETUP_PENDING: dict = {}
PENDING_AUTH:  dict = {}

# ══════════════════════════════════════════════
#               BOT (aiogram 3.x)
# ══════════════════════════════════════════════
bot    = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp     = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ══════════════════════════════════════════════
#                   FSM STATES
# ══════════════════════════════════════════════
class S(StatesGroup):
    amount      = State()
    admin_price = State()


# ══════════════════════════════════════════════
#                  KLAVIATURALAR
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


# ══════════════════════════════════════════════
#              ASOSIY HANDLERLAR
# ══════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    get_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name or "")
    await msg.answer(
        f"👋 <b>Salom, {msg.from_user.first_name}!</b>\n\n"
        "Bu bot orqali bot.php kabi botlaringizga\n"
        "<b>Humo karta avto to'lov</b> tizimini ulashingiz mumkin! ⚡\n\n"
        "▪️ Do'kon oching — kartangizni ulang\n"
        "▪️ API key oling — botingizga ulang\n"
        "▪️ To'lovlar avtomatik tasdiqlanadi",
        reply_markup=kb_main(),
    )


@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    PENDING_AUTH.pop(call.from_user.id, None)
    SETUP_PENDING.pop(call.from_user.id, None)
    await call.message.edit_text("👋 <b>Bosh menyu</b>", reply_markup=kb_main())


@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u   = get_user(call.from_user.id)
    cnt = db(
        "SELECT COUNT(*) AS c FROM orders WHERE user_id=? AND status='paid'",
        (call.from_user.id,), one=True
    )
    shop = get_shop(call.from_user.id)
    shop_line = ""
    if shop:
        d = days_left(shop["expires"])
        shop_line = f"\n🛒 Do'kon: <b>{shop['shop_name']}</b> ({d} kun qoldi)"
    await call.message.edit_text(
        f"👤 <b>Profil</b>\n\n"
        f"Ism: <b>{call.from_user.full_name}</b>\n"
        f"ID: <code>{call.from_user.id}</code>\n"
        f"Username: @{call.from_user.username or 'yoq'}\n\n"
        f"📦 To'lovlar: <b>{cnt['c']} ta</b>"
        f"{shop_line}",
        reply_markup=kb_back(),
    )


@router.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    await call.message.edit_text(
        "🤖 <b>Bot haqida</b>\n\n"
        "✅ Humo karta orqali avto to'lov\n"
        "✅ 5 daqiqada avtomatik tasdiqlash\n"
        "✅ Do'kon tizimi — oylik obuna\n"
        "✅ API integratsiya — bot.php ga ulash\n"
        "✅ Har kim o'z Telegram accountini ulaydi\n"
        "✅ Webhook orqali avtomatik bildirishnoma",
        reply_markup=kb_back(),
    )


@router.callback_query(F.data == "api_info")
async def cb_api_info(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.message.edit_text(
            "⚙️ <b>API Tizimi</b>\n\n"
            "API dan foydalanish uchun avval do'kon oching.\n\n"
            "Do'konlarim → Do'kon ochish",
            reply_markup=kb_back(),
        )
        return

    host = "http://SERVER_IP:8000"   # Serveringiz IP yoki domen
    await call.message.edit_text(
        f"⚙️ <b>API Tizimi</b>\n\n"
        f"🔑 API Key:\n<code>{shop['api_key']}</code>\n\n"
        f"📖 <b>Endpointlar:</b>\n\n"
        f"1️⃣ Order yaratish:\n"
        f"<code>POST {host}/api/create_order</code>\n\n"
        f"2️⃣ Status tekshirish:\n"
        f"<code>GET {host}/api/status/{{order_id}}?api_key=...</code>\n\n"
        f"3️⃣ Do'kon ma'lumoti:\n"
        f"<code>GET {host}/api/shops/info?api_key=...</code>\n\n"
        f"📋 <b>bot.php ga ulash:</b>\n"
        f"Quyidagi kodni bot.php ga qo'shing:\n"
        f"<code>HUMO_API_KEY = '{shop['api_key']}'</code>",
        reply_markup=kb_back(),
    )


# ══════════════════════════════════════════════
#               DO'KON TIZIMI
# ══════════════════════════════════════════════

@router.callback_query(F.data == "shops")
async def cb_shops(call: CallbackQuery):
    shop  = get_shop(call.from_user.id)
    price = get_setting("shop_price", str(SHOP_PRICE))
    if not shop:
        await call.message.edit_text(
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
        await call.message.edit_text(
            f"🛒 <b>Do'konlarim</b>\n\n"
            f"Do'kon: <b>{shop['shop_name']}</b>\n"
            f"Holat: {status}\n"
            f"⏳ Qolgan kun: <b>{d} kun</b>\n"
            f"💳 Karta: <code>{shop['card_number']}</code>",
            reply_markup=kb_shops(shop),
        )


@router.callback_query(F.data == "shop_info")
async def cb_shop_info(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True)
        return
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
        await call.answer("Do'kon topilmadi!", show_alert=True)
        return
    await call.message.edit_text(
        f"🔑 <b>API Key</b>\n\n"
        f"<code>{shop['api_key']}</code>\n\n"
        f"⚠️ Bu kalitni hech kimga bermang!\n"
        f"Bot.php ga ulash uchun shu kalitni ishlating.",
        reply_markup=kb_back("shops"),
    )


# ══════════════════════════════════════════════
#       DO'KON OCHISH — TO'LOV + SETUP
# ══════════════════════════════════════════════

@router.callback_query(F.data == "shop_open")
async def cb_shop_open(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if shop:
        await call.answer("Sizda allaqachon do'kon bor!", show_alert=True)
        return
    price = int(get_setting("shop_price", str(SHOP_PRICE)))
    await call.message.edit_text(
        f"➕ <b>Do'kon ochish</b>\n\n"
        f"Do'kon ochish uchun oylik obuna to'lashingiz kerak.\n\n"
        f"💰 Narx: <b>{fmts(price)}</b> / oy\n\n"
        f"To'lovni amalga oshirish uchun quyidagi tugmani bosing:",
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

    oid = db(
        "INSERT INTO orders(user_id, amount, base_amount, order_type) VALUES(?,?,?,?)",
        (call.from_user.id, amount, price, "shop_sub")
    )

    PENDING[oid] = {
        "user_id":    call.from_user.id,
        "amount":     amount,
        "base_amount": price,
        "chat_id":    call.message.chat.id,
        "msg_id":     None,
        "expires":    expires,
        "confirmed":  False,
        "order_type": "shop_sub",
    }

    exp_str    = datetime.fromtimestamp(expires).strftime("%H:%M:%S")
    extra_note = f"\n<i>({extra} so'm aniqlik uchun qo'shildi)</i>" if extra > 0 else ""

    pay_msg = await call.message.edit_text(
        f"💳 <b>Do'kon obuna to'lovi</b>\n\n"
        f"💰 To'lov summasi: <b>{fmts(amount)}</b>{extra_note}\n"
        f"🏦 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"⏳ Muddati: <b>{exp_str}</b> gacha\n\n"
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!\n"
        f"To'lovdan so'ng tugmani bosing:",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


# ── Topup (faqat CMD testlash uchun, asosiy — API orqali) ─

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

    oid = db(
        "INSERT INTO orders(user_id, amount, base_amount, order_type) VALUES(?,?,?,?)",
        (msg.from_user.id, amount, amt, "topup")
    )

    PENDING[oid] = {
        "user_id":    msg.from_user.id,
        "amount":     amount,
        "base_amount": amt,
        "chat_id":    msg.chat.id,
        "msg_id":     None,
        "expires":    expires,
        "confirmed":  False,
        "order_type": "topup",
    }

    exp_str    = datetime.fromtimestamp(expires).strftime("%H:%M:%S")
    extra_note = f"\n<i>({extra} so'm aniqlik uchun qo'shildi)</i>" if extra > 0 else ""

    pay_msg = await msg.answer(
        f"💳 <b>To'lov</b>\n\n"
        f"💰 Summa: <b>{fmts(amount)}</b>{extra_note}\n"
        f"🏦 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: <b>{CARD_OWNER}</b>\n\n"
        f"⏳ Muddati: <b>{exp_str}</b> gacha\n\n"
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!\n"
        f"To'lovdan so'ng tugmani bosing:",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


# ── To'lov tugmalari ──────────────────────────

@router.callback_query(F.data.startswith("paid_"))
async def cb_paid(call: CallbackQuery):
    oid = int(call.data.split("_")[1])
    p   = PENDING.get(oid)
    if not p:
        await call.answer("❌ Order topilmadi yoki muddati o'tgan!", show_alert=True)
        return
    if p.get("confirmed"):
        await call.answer("✅ Allaqachon tasdiqlangan!", show_alert=True)
        return
    await call.answer(
        "⏳ To'lov tekshirilmoqda...\n"
        "Avtomatik tasdiqlash 5 daqiqa ichida bo'ladi.",
        show_alert=True
    )


@router.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(call: CallbackQuery):
    oid = int(call.data.split("_")[1])
    p   = PENDING.pop(oid, None)
    if p:
        db("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    await call.message.edit_text("❌ <b>To'lov bekor qilindi.</b>", reply_markup=kb_main())


async def _timer(oid: int):
    """5 daqiqa kutadi, tasdiqlanmasa — bekor qiladi"""
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
                    await bot.edit_message_text(
                        "⏰ <b>To'lov muddati tugadi.</b>\n\nQaytadan urinib ko'ring.",
                        chat_id=cid, message_id=mid,
                    )
                else:
                    await bot.send_message(
                        cid, "⏰ <b>To'lov muddati tugadi.</b>\n\nQaytadan urinib ko'ring."
                    )
            except Exception:
                pass


# ══════════════════════════════════════════════
#   CONFIRM — TO'LOV TASDIQLASH
#   Poller to'lovni aniqlasa shu funksiya chaqiriladi
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

    db("UPDATE orders SET status='paid', paid=? WHERE id=?",
       (now_dt.strftime("%Y-%m-%d %H:%M:%S"), oid))

    # ── topup: faqat CMD testlash (asosiy — external) ──
    if order_type == "topup":
        db("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
        if cid:
            try:
                await bot.send_message(
                    cid,
                    f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
                    f"💰 Summa: <b>{fmts(amount)}</b>\n"
                    f"🧾 Order: <code>#{oid}</code>\n"
                    f"🕐 {now_str}",
                )
            except Exception as e:
                log.error(f"[CONFIRM] topup xabar xato: {e}")

    # ── shop_sub: do'kon ochish to'lovi ──
    elif order_type == "shop_sub":
        if cid:
            await start_shop_setup(uid, cid)

    # ── external: bot.php foydalanuvchisi to'lovi ──
    elif order_type == "external":
        webhook_url  = p.get("webhook_url")
        ext_user_id  = p.get("ext_user_id")
        shop_id      = p.get("shop_id")

        if webhook_url:
            await send_webhook(webhook_url, {
                "event":       "payment_confirmed",
                "order_id":    oid,
                "user_id":     ext_user_id,   # bot.php foydalanuvchisining ID si
                "amount":      base_amount,    # Asl summa (aniqlik uchun qo'shilgansiz)
                "shop_id":     shop_id,
                "timestamp":   now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })
            log.info(
                f"[CONFIRM] external webhook yuborildi: "
                f"user={ext_user_id} amount={base_amount} shop={shop_id}"
            )
        else:
            log.error(f"[CONFIRM] external order #{oid} uchun webhook_url yo'q!")

    # ── shop_renew: obuna uzaytirish ──
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
                        await bot.send_message(
                            cid,
                            f"✅ <b>Obuna uzaytirildi!</b>\n\n"
                            f"💰 Summa: <b>{fmts(amount)}</b>\n"
                            f"📅 Yangi tugash: <b>{new_exp[:10]}</b>\n"
                            f"🕐 {now_str}",
                        )
                    except Exception:
                        pass

    # ── Adminga xabar ──
    for aid in ADMIN_IDS:
        try:
            u    = db("SELECT * FROM users WHERE id=?", (uid,), one=True)
            name = u["name"] if u else str(uid)
            type_txt = {
                "topup":      "Balans to'ldirish (CMD test)",
                "shop_sub":   "Do'kon ochish",
                "shop_renew": "Obuna uzaytirish",
                "external":   f"Bot.php to'lovi (user={p.get('ext_user_id')})",
            }.get(order_type, order_type)
            await bot.send_message(
                aid,
                f"💰 <b>Yangi to'lov tasdiqlandi!</b>\n\n"
                f"👤 {name} (<code>{uid}</code>)\n"
                f"📦 Tur: {type_txt}\n"
                f"💵 <b>{fmts(amount)}</b>\n"
                f"🧾 Order #{oid} | 🕐 {now_str}",
            )
        except Exception:
            pass


# ══════════════════════════════════════════════
#   DO'KON SETUP (ro'yxatdan o'tish)
#   SETUP_PENDING dict orqali (FSMsiz)
# ══════════════════════════════════════════════

async def start_shop_setup(user_id: int, chat_id: int):
    """To'lov tasdiqlangandan keyin do'kon sozlash boshlaydi"""
    SETUP_PENDING[user_id] = {"step": "name", "chat_id": chat_id}
    await bot.send_message(
        chat_id,
        "✅ <b>To'lov tasdiqlandi! Do'kon sozlanmoqda...</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>1-qadam: Do'kon nomi</b>\n\n"
        "Do'kon nomini kiriting:\n"
        "<i>Misol: MyShop, Online Store...</i>"
    )


async def _send_phone_code(user_id: int, chat_id: int, phone: str):
    """Yangi kod yuboradi"""
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

    setup["client"]          = client
    setup["phone"]           = phone
    setup["phone_code_hash"] = sent.phone_code_hash
    setup["step"]            = "code"

    await bot.send_message(
        chat_id,
        f"✅ Kod yuborildi!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>6-qadam: Telegram kodi</b>\n\n"
        f"📱 <code>{phone}</code> ga Telegram kodi keldi.\n\n"
        f"Kodni kiriting (misol: <code>12345</code>):"
    )


@router.message(F.text, StateFilter(None))
async def setup_message_handler(msg: Message, state: FSMContext):
    """SETUP_PENDING da bo'lsa — do'kon sozlash, aks holda — o'tkazib yuboradi"""
    uid   = msg.from_user.id
    setup = SETUP_PENDING.get(uid)
    if not setup:
        return

    step    = setup.get("step")
    chat_id = setup["chat_id"]
    text    = (msg.text or "").strip()

    # ── 1. Do'kon nomi ──
    if step == "name":
        if len(text) < 2 or len(text) > 50:
            await msg.answer("❌ Nom 2-50 ta belgi bo'lishi kerak!")
            return
        setup["shop_name"] = text
        setup["step"]      = "card"
        await msg.answer(
            f"✅ Nom: <b>{text}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>2-qadam: Karta raqami</b>\n\n"
            f"Humo karta raqamingizni kiriting:\n"
            f"<i>Misol: 9860 1234 5678 9012</i>"
        )
        return

    # ── 2. Karta raqami ──
    if step == "card":
        card = text.replace(" ", "")
        if not card.isdigit() or len(card) != 16:
            await msg.answer(
                "❌ Karta raqami 16 ta raqamdan iborat bo'lishi kerak!\n"
                "Misol: <code>9860123456789012</code>"
            )
            return
        formatted            = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
        setup["card_number"] = formatted
        setup["step"]        = "api_id"
        await msg.answer(
            f"✅ Karta: <code>{formatted}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>3-qadam: Telegram API ID</b>\n\n"
            f"API ID olish:\n"
            f"1️⃣ <a href='https://my.telegram.org'>my.telegram.org</a> ga kiring\n"
            f"2️⃣ Telefon raqamingizni kiriting\n"
            f"3️⃣ <b>API development tools</b> ga bosing\n"
            f"4️⃣ <b>App api_id</b> ni ko'chiring\n\n"
            f"API ID ni kiriting (faqat raqam):\n"
            f"<i>Misol: 12345678</i>",
            disable_web_page_preview=True,
        )
        return

    # ── 3. API ID ──
    if step == "api_id":
        if not text.isdigit():
            await msg.answer("❌ API ID faqat raqamlardan iborat!\n<i>Misol: 12345678</i>")
            return
        setup["api_id"] = int(text)
        setup["step"]   = "api_hash"
        await msg.answer(
            f"✅ API ID: <code>{text}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>4-qadam: Telegram API HASH</b>\n\n"
            f"<a href='https://my.telegram.org'>my.telegram.org</a> dan\n"
            f"<b>App api_hash</b> ni ko'chiring:\n\n"
            f"<i>Misol: a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6</i>",
            disable_web_page_preview=True,
        )
        return

    # ── 4. API HASH ──
    if step == "api_hash":
        if len(text) < 10:
            await msg.answer("❌ API HASH noto'g'ri!\nmy.telegram.org dan to'g'ri ko'chiring.")
            return
        setup["api_hash"] = text
        setup["step"]     = "phone"
        await msg.answer(
            f"✅ API HASH qabul qilindi.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>5-qadam: Telefon raqami</b>\n\n"
            f"Telegram accountingizga bog'liq telefon:\n\n"
            f"<i>Misol: +998901234567</i>"
        )
        return

    # ── 5. Telefon raqami ──
    if step == "phone":
        if not text.startswith("+") or len(text) < 10:
            await msg.answer(
                "❌ Telefon raqamini to'g'ri formatda kiriting!\n"
                "<i>Misol: +998901234567</i>"
            )
            return
        wait_msg = await msg.answer("⏳ Kod yuborilmoqda...")
        try:
            await _send_phone_code(uid, chat_id, text)
            await wait_msg.delete()
        except Exception as e:
            await wait_msg.edit_text(
                f"❌ Xato:\n<code>{e}</code>\n\n"
                f"API ID va API HASH to'g'riligini tekshiring.\n"
                f"Qaytadan telefon raqamini kiriting:"
            )
            setup["step"] = "phone"
        return

    # ── 6. SMS/Telegram kodi ──
    if step == "code":
        code = text.replace(" ", "")
        if not code.isdigit():
            await msg.answer("❌ Faqat raqam kiriting!\n<i>Misol: 12345</i>")
            return

        client          = setup.get("client")
        phone           = setup.get("phone")
        phone_code_hash = setup.get("phone_code_hash")

        if not client or not phone:
            await msg.answer("❌ Session muddati o'tgan. Telefon raqamini qaytadan kiriting:")
            setup["step"] = "phone"
            return

        wait_msg = await msg.answer("⏳ Tekshirilmoqda...")
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await wait_msg.delete()
            await _finish_shop_setup(msg, client, setup)

        except SessionPasswordNeededError:
            setup["step"] = "twofa"
            await wait_msg.edit_text(
                "🔐 <b>2FA parol kerak</b>\n\n"
                "Telegram accountingizda ikki bosqichli tasdiqlash yoqilgan.\n\n"
                "Parolni kiriting:"
            )

        except PhoneCodeInvalidError:
            await wait_msg.edit_text("❌ Kod noto'g'ri!\n\nQaytadan kodni kiriting:")

        except PhoneCodeExpiredError:
            await wait_msg.edit_text("⏳ Kod eskirdi, yangi kod yuborilmoqda...")
            try:
                await _send_phone_code(uid, chat_id, phone)
                await wait_msg.delete()
            except Exception as e:
                await wait_msg.edit_text(
                    f"❌ Yangi kod yuborishda xato:\n<code>{e}</code>\n\n"
                    f"Telefon raqamini qaytadan kiriting:"
                )
                setup["step"] = "phone"

        except Exception as e:
            await wait_msg.edit_text(
                f"❌ Xato: <code>{e}</code>\n\n"
                f"Telefon raqamini qaytadan kiriting:"
            )
            setup["step"] = "phone"
        return

    # ── 7. 2FA parol ──
    if step == "twofa":
        client = setup.get("client")
        if not client:
            await msg.answer("❌ Session topilmadi. Telefon raqamini qaytadan kiriting:")
            setup["step"] = "phone"
            return

        wait_msg = await msg.answer("⏳ Parol tekshirilmoqda...")
        try:
            await client.sign_in(password=text)
            await wait_msg.delete()
            await _finish_shop_setup(msg, client, setup)
        except Exception as e:
            await wait_msg.edit_text(
                f"❌ Parol xato:\n<code>{e}</code>\n\nQaytadan kiriting:"
            )
        return


async def _finish_shop_setup(msg: Message, client: TelegramClient, setup: dict):
    """Session tasdiqlandi — do'konni DB ga saqlash"""
    uid = msg.from_user.id

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

    db(
        "INSERT OR REPLACE INTO shops"
        "(user_id, shop_name, card_number, api_id, api_hash, phone, string_session, api_key, status, expires)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (uid, shop_name, card, api_id, api_hash, phone,
         session_string, api_key, "active", expires)
    )

    SETUP_PENDING.pop(uid, None)
    PENDING_AUTH.pop(uid, None)

    shop = get_shop(uid)
    asyncio.create_task(start_shop_poller(shop))

    host = "http://SERVER_IP:8000"   # Serveringiz IP yoki domen

    await msg.answer(
        f"🎉 <b>Do'kon muvaffaqiyatli ochildi!</b>\n\n"
        f"📌 Nom: <b>{shop_name}</b>\n"
        f"💳 Karta: <code>{card}</code>\n"
        f"📱 Telefon: <code>{phone}</code>\n"
        f"👤 Telegram: {me.first_name} (@{me.username or 'yoq'})\n"
        f"📅 Obuna tugaydi: <b>{expires[:10]}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>API Key (bot.php ga ulash uchun):</b>\n"
        f"<code>{api_key}</code>\n\n"
        f"🌐 <b>API URL:</b>\n"
        f"<code>{host}/api/create_order</code>\n\n"
        f"⚠️ API keyni saqlang — hech kimga bermang!\n\n"
        f"/start — bosh menyuga qaytish",
        reply_markup=kb_main(),
    )

    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"🛒 <b>Yangi do'kon ochildi!</b>\n\n"
                f"👤 {msg.from_user.full_name} (<code>{uid}</code>)\n"
                f"📌 {shop_name}\n"
                f"💳 {card}\n"
                f"📱 {phone}\n"
                f"🔢 API ID: {api_id}"
            )
        except Exception:
            pass


# ── Obunani uzaytirish ────────────────────────

@router.callback_query(F.data == "shop_renew")
async def cb_shop_renew(call: CallbackQuery):
    shop = get_shop(call.from_user.id)
    if not shop:
        await call.answer("Do'kon topilmadi!", show_alert=True)
        return

    price   = int(get_setting("shop_price", str(SHOP_PRICE)))
    amount  = unique_amount(float(price))
    extra   = int(amount - price)
    expires = time.time() + PAYMENT_TIME

    oid = db(
        "INSERT INTO orders(user_id, amount, base_amount, order_type, shop_id) VALUES(?,?,?,?,?)",
        (call.from_user.id, amount, price, "shop_renew", shop["id"])
    )

    PENDING[oid] = {
        "user_id":    call.from_user.id,
        "amount":     amount,
        "base_amount": price,
        "chat_id":    call.message.chat.id,
        "msg_id":     None,
        "expires":    expires,
        "confirmed":  False,
        "order_type": "shop_renew",
        "shop_id":    shop["id"],
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
        f"⚠️ Aynan <b>{fmts(amount)}</b> summani o'tkazing!\n"
        f"To'lovdan so'ng tugmani bosing:",
        reply_markup=kb_pay(oid),
    )
    PENDING[oid]["msg_id"] = pay_msg.message_id
    asyncio.create_task(_timer(oid))


# ══════════════════════════════════════════════
#               ADMIN PANEL
# ══════════════════════════════════════════════

@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    price   = get_setting("shop_price", str(SHOP_PRICE))
    total   = db("SELECT COUNT(*) AS c, SUM(amount) AS s FROM orders WHERE status='paid'", one=True)
    shops   = db("SELECT COUNT(*) AS c FROM shops", one=True)
    users   = db("SELECT COUNT(*) AS c FROM users", one=True)
    ext_cnt = db(
        "SELECT COUNT(*) AS c FROM orders WHERE status='paid' AND order_type='external'",
        one=True
    )
    await msg.answer(
        f"🔐 <b>Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users['c']}</b>\n"
        f"🛒 Do'konlar: <b>{shops['c']}</b>\n"
        f"✅ Tasdiqlangan: <b>{total['c']} ta</b>\n"
        f"🤖 Bot.php to'lovlar: <b>{ext_cnt['c']} ta</b>\n"
        f"💵 Jami: <b>{fmts(total['s'] or 0)}</b>\n"
        f"🔄 Kutilmoqda: <b>{len(PENDING)} ta</b>\n\n"
        f"💰 Do'kon narxi: <b>{fmts(price)}</b> / oy",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Do'kon narxini o'zgartirish", callback_data="admin_price")],
            [InlineKeyboardButton(text="🛒 Do'konlar ro'yxati",          callback_data="admin_shops")],
        ]),
    )


@router.callback_query(F.data == "admin_price")
async def cb_admin_price(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    price = get_setting("shop_price", str(SHOP_PRICE))
    await state.set_state(S.admin_price)
    await call.message.edit_text(
        f"💰 <b>Do'kon narxini o'zgartirish</b>\n\n"
        f"Hozirgi narx: <b>{fmts(price)}</b> / oy\n\n"
        f"Yangi narxni kiriting (so'mda):",
        reply_markup=kb_back(),
    )


@router.message(S.admin_price)
async def msg_admin_price(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    raw = msg.text.strip().replace(" ", "").replace(",", "")
    if not raw.isdigit():
        await msg.answer("❌ Faqat raqam kiriting!")
        return
    new_price = int(raw)
    set_setting("shop_price", str(new_price))
    await state.clear()
    await msg.answer(f"✅ Do'kon narxi o'zgartirildi!\nYangi narx: <b>{fmts(new_price)}</b> / oy")


@router.callback_query(F.data == "admin_shops")
async def cb_admin_shops(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    shops = db("SELECT * FROM shops ORDER BY created DESC LIMIT 20", fetch=True)
    if not shops:
        await call.message.edit_text("🛒 Do'konlar yo'q", reply_markup=kb_back())
        return
    text = "🛒 <b>Do'konlar ro'yxati</b>\n\n"
    for s in shops:
        d      = days_left(s["expires"])
        status = "✅" if s["status"] == "active" and d > 0 else "❌"
        text  += (
            f"{status} <b>{s['shop_name']}</b>\n"
            f"   👤 <code>{s['user_id']}</code> | 📱 {s['phone']} | {d} kun\n\n"
        )
    await call.message.edit_text(text, reply_markup=kb_back())


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
            is_incoming = True
            break
        if any(kw in s for kw in ["To'lov", "Тўлов", "Toʻlov", "Платёж", "🔀"]):
            log.info("[PARSE] ❌ Chiquvchi to'lov")
            return None

    if not is_incoming:
        log.info("[PARSE] ❌ Kiruvchi emas")
        return None

    clean_lines = []
    for line in lines:
        s = re.sub(r"\*\*|__", "", line.strip())
        clean_lines.append(s.strip())

    for s in clean_lines:
        has_plus = s.startswith("+") or s.startswith("➕")
        if not has_plus or "UZS" not in s.upper():
            continue
        s = re.sub(r"^➕", "+", s)

        # Format A: "+ 1.000,00 UZS"
        m = re.match(r"^\+\s*(\d{1,3}(?:\.\d{3})*,\d{1,2})\s*UZS", s, re.IGNORECASE)
        if m:
            raw = m.group(1)
            int_p, dec_p = raw.rsplit(",", 1)
            try:
                val = float(f"{int_p.replace('.', '')}.{dec_p}")
                if val > 0:
                    return val
            except ValueError:
                pass

        # Format B: "+ 1 000,00 UZS"
        m = re.match(r"^\+\s*(\d{1,3}(?:\s\d{3})*,\d{1,2})\s*UZS", s, re.IGNORECASE)
        if m:
            raw = m.group(1)
            int_p, dec_p = raw.rsplit(",", 1)
            try:
                val = float(f"{int_p.replace(' ', '')}.{dec_p}")
                if val > 0:
                    return val
            except ValueError:
                pass

        # Format C: "+ 50000 UZS"
        m = re.match(r"^\+\s*(\d[\d\s]*)\s*UZS", s, re.IGNORECASE)
        if m:
            try:
                val = float(re.sub(r"\s", "", m.group(1)))
                if val > 0:
                    return val
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
    """
    HUMOcardbot dan kelgan xabarlarni kuzatadi.
    Har bir do'kon uchun alohida poller ishlatiladi.
    """
    humo_entity = None
    try:
        humo_entity = await client.get_entity(HUMO_BOT_USERNAME)
        log.info(f"[{label}] ✅ HUMOcardbot topildi")
    except Exception as e:
        log.error(f"[{label}] ❌ HUMOcardbot topilmadi: {e}")
        return

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
                log.info(f"[{label}] 🆕 Yangi xabar id={msg_id}")

                if not text:
                    continue

                amount = humo_parse(text)
                if amount is None:
                    continue

                log.info(f"[{label}] 💰 {amount} UZS keldi")
                oid = find_order(amount)
                if oid is None:
                    log.info(f"[{label}] ❌ Mos order topilmadi")
                    continue

                log.info(f"[{label}] ✅ MATCH Order #{oid} → tasdiqlash")
                await confirm(oid)

        except Exception as e:
            log.error(f"[{label}] Xato: {type(e).__name__}: {e}")

        await asyncio.sleep(2)


async def start_shop_poller(shop):
    shop_id = shop["id"]
    if shop_id in SHOP_CLIENTS:
        return
    try:
        client = TelegramClient(
            StringSession(shop["string_session"]),
            shop["api_id"],
            shop["api_hash"],
        )
        await client.connect()
        if not await client.is_user_authorized():
            log.error(f"[SHOP-{shop_id}] Session yaroqsiz!")
            return
        me = await client.get_me()
        log.info(f"[SHOP-{shop_id}] ✅ {me.first_name} — poller boshlandi")
        SHOP_CLIENTS[shop_id] = client
        asyncio.create_task(run_poller(client, f"SHOP-{shop_id}"))
    except Exception as e:
        log.error(f"[SHOP-{shop_id}] Poller xato: {e}")


# ══════════════════════════════════════════════
#               ISHGA TUSHIRISH
# ══════════════════════════════════════════════

async def setup_admin_userbot() -> Optional[TelegramClient]:
    """Admin userbot sessionini sozlash (do'kon obuna to'lovi uchun)"""
    global ADMIN_SESSION_STR

    client = TelegramClient(StringSession(ADMIN_SESSION_STR), ADMIN_API_ID, ADMIN_API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        log.info(f"✅ Admin userbot: {me.first_name} (@{me.username})")
        return client

    print()
    print("═" * 50)
    print("   📱 ADMIN USERBOT SESSION YARATISH")
    print("   (Bu faqat birinchi marta so'raladi)")
    print("═" * 50)
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
        print(f"\n💾 ADMIN_SESSION_STR ga saqlang:\n{session_string}\n")
        set_setting("admin_session", session_string)
        return client

    except Exception as e:
        print(f"\n❌ Xato: {e}")
        await client.disconnect()
        return None


async def subscription_checker():
    """Har soatda obunalarni tekshiradi"""
    while True:
        await asyncio.sleep(3600)
        try:
            shops = db("SELECT * FROM shops WHERE status='active'", fetch=True)
            for shop in shops:
                if days_left(shop["expires"]) <= 0:
                    db("UPDATE shops SET status='expired' WHERE id=?", (shop["id"],))
                    log.info(f"[SUB] Do'kon #{shop['id']} muddati tugadi")
                    try:
                        await bot.send_message(
                            shop["user_id"],
                            f"⚠️ <b>Do'kon obunasi tugadi!</b>\n\n"
                            f"Do'kon: <b>{shop['shop_name']}</b>\n\n"
                            f"Bot.php botingiz to'lovlarni qabul qilishni to'xtatdi!\n"
                            f"Davom etish uchun:\n"
                            f"/start → Do'konlarim → Obunani uzaytirish"
                        )
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"[SUB] Checker xato: {e}")


async def main():
    global admin_userbot
    init_db()

    # Admin session DB dan olish
    saved_session = get_setting("admin_session", ADMIN_SESSION_STR)
    if saved_session:
        try:
            admin_userbot = TelegramClient(
                StringSession(saved_session), ADMIN_API_ID, ADMIN_API_HASH
            )
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

    # Admin poller (do'kon obuna to'lovlari uchun — admin kartasini kuzatadi)
    asyncio.create_task(run_poller(admin_userbot, "ADMIN"))
    log.info("✅ Admin poller ishga tushdi")

    # Barcha aktiv do'konlar pollerlarini ishga tushirish
    # (har bir do'kon o'z kartasini kuzatadi — bot.php foydalanuvchilari uchun)
    active_shops = db("SELECT * FROM shops WHERE status='active'", fetch=True)
    for shop in active_shops:
        if days_left(shop["expires"]) > 0:
            asyncio.create_task(start_shop_poller(shop))
            log.info(f"[BOOT] '{shop['shop_name']}' poller ishga tushdi")

    asyncio.create_task(subscription_checker())

    log.info("🚀 Bot ishga tushdi!")
    log.info(f"🌐 API server: http://0.0.0.0:8000")

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
    print("   💳  AVTO TO'LOV TIZIMI v8.0")
    print("   🤖  bot.php botlar uchun platforma")
    print("   🛒  Do'kon + API + Webhook")
    print("═" * 50)
    print()
    print("O'rnatish: pip install aiogram telethon fastapi uvicorn httpx")
    print()
    asyncio.run(main())



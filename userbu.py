from pyrogram import Client, filters, enums
from pyrogram.types import Message
import asyncio
import base64
import io
import time
import os
import sys
import random
import logging
import requests
from datetime import datetime
from time import gmtime
from typing import List, Optional, Union
from PIL import Image, ImageDraw

# ==================== KONFIGURATSIYA ====================
API_ID = "39206752"
API_HASH = "82b55fc7b6349fe4e68205c6a29e6af6"
SESSION_NAME = "userbot_ultimate"
PREFIX = "."

# xAI (Grok) API
XAI_API_KEY = "xai-PGNl847nN0LbrOHZ8kRrqnEAOrB7fP8fVdmCUbOpoUYsJYsf3mxQSkgXTo84OX3wbPZUT6BACJRt65NQ"
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL   = "grok-3"          # asosiy
XAI_MODELS  = ["grok-3", "grok-3-mini", "grok-2-1212"]  # fallback ro'yxati

# ==================== KONFIGURATSIYA ====================

# yg_quotes (LyoSU/quote-api) standart ranglari
QUOTE_BG_COLOR  = "#1b1429"          # standart to'q binafsha-qora
QUOTE_BG_DARK   = "#1b1429"          # qora tema
QUOTE_BG_LIGHT  = "#ffffff"          # oq tema  
QUOTE_BG_GRAD   = "#1b1429/#2d1b69"  # gradient (qora → binafsha)
QUOTE_BG_BLUE   = "#17212b"          # Telegram dark uslubi
QUOTE_BG_RANDOM = "random"           # tasodifiy rang

QUOTE_ENDPOINT = "https://bot.lyo.su/quote/generate"  # rasmiy endpoint
QUOTE_WIDTH    = 512
QUOTE_HEIGHT   = 768
QUOTE_SCALE    = 2
QUOTE_EMOJI    = "apple"
QUOTE_MAX_MSGS = 15

logger = logging.getLogger(__name__)

# ==================== CLIENT ====================
# Session fayli mavjud bo'lsa qayta kiritish shart emas
SESSION_FILE = f"{SESSION_NAME}.session"
app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=os.path.dirname(os.path.abspath(__file__)) or "."
)

start_time = time.time()
afk_status = {"is_afk": False, "reason": None, "time": None}
ai_auto_reply = {
    "enabled": False,
    "chat_history": {},
    "exclude_chats": [],
    "min_delay": 3,
    "max_delay": 8,
}

print("🚀 USERBOT ULTIMATE")
print("=" * 60)


# ==================== QUOTE UTILS ====================

def parse_entities(entities) -> List[dict]:
    """Pyrogram entitylarni JSON formatga"""
    result = []
    if not entities:
        return result

    type_map = {
        "bold": "bold", "italic": "italic", "underline": "underline",
        "strikethrough": "strikethrough", "code": "code", "pre": "pre",
        "text_link": "text_link", "url": "url", "email": "email",
        "phone_number": "phone_number", "mention": "mention",
        "text_mention": "text_mention", "hashtag": "hashtag",
        "cashtag": "cashtag", "bot_command": "bot_command",
        "spoiler": "spoiler", "custom_emoji": "custom_emoji",
    }

    for entity in entities:
        try:
            etype = entity.type.value if hasattr(entity.type, "value") else str(entity.type)
            etype = etype.replace("MessageEntityType.", "").lower()
            mapped = type_map.get(etype, etype)
            item = {"type": mapped, "offset": entity.offset, "length": entity.length}
            if hasattr(entity, "url") and entity.url:
                item["url"] = entity.url
            if hasattr(entity, "user") and entity.user:
                item["user"] = {"id": entity.user.id}
            if hasattr(entity, "custom_emoji_id") and entity.custom_emoji_id:
                item["custom_emoji_id"] = str(entity.custom_emoji_id)
            if hasattr(entity, "language") and entity.language:
                item["language"] = entity.language
            result.append(item)
        except Exception:
            continue

    return result


def format_duration(seconds: Union[int, float]) -> str:
    t = gmtime(seconds)
    if t.tm_hour > 0:
        return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
    return f"{t.tm_min:02d}:{t.tm_sec:02d}"


def get_media_description(message: Message, reply_context: bool = False) -> str:
    if message.photo and reply_context:
        return "📷 Фото"
    if message.sticker and reply_context:
        return f"{message.sticker.emoji or '🗿'} Стикер"
    if message.video_note and reply_context:
        return "📹 Видеосообщение"
    if message.video and reply_context:
        return "📹 Видео"
    if message.animation:
        return "🖼 GIF"
    if message.poll:
        return "📊 Опрос"
    if message.location:
        return "📍 Местоположение"
    if message.contact:
        return "👤 Контакт"
    if message.voice:
        return f"🎵 Голосовое сообщение: {format_duration(message.voice.duration or 0)}"
    if message.audio:
        dur  = message.audio.duration or 0
        perf = message.audio.performer or ""
        title= message.audio.title or ""
        return f"🎧 Музыка: {format_duration(dur)} | {perf} - {title}"
    if message.document:
        return f"💾 Файл: {message.document.file_name or 'Файл'}"
    if message.dice:
        return f"{message.dice.emoji} Кость: {message.dice.value}"
    return ""


def has_preview(message: Message) -> bool:
    return bool(
        message.photo or message.sticker or message.video
        or message.video_note or message.animation or message.web_page
    )


async def process_image(image_bytes: bytes, circular: bool = False) -> Optional[str]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        if circular:
            size = min(image.size)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            sq = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            sq.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
            image = Image.composite(sq, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode()}"
    except Exception as e:
        logger.error(f"process_image: {e}")
        return None


async def process_sticker(sticker_bytes: bytes) -> Optional[str]:
    try:
        image = Image.open(io.BytesIO(sticker_bytes))
        if image.mode not in ("RGBA", "LA"):
            image = image.convert("RGBA")
        elif image.mode == "LA":
            image = image.convert("RGBA")
        out = io.BytesIO()
        image.save(out, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode()}"
    except Exception as e:
        logger.error(f"process_sticker: {e}")
        return None


async def get_avatar(client: Client, user_id: int) -> Optional[str]:
    try:
        async for photo in client.get_chat_photos(user_id, limit=1):
            b = await client.download_media(photo.file_id, in_memory=True)
            return f"data:image/jpeg;base64,{base64.b64encode(b.getvalue()).decode()}"
    except Exception:
        pass
    return None


async def get_media_data(client: Client, message: Message) -> Optional[dict]:
    try:
        if message.sticker:
            b = await client.download_media(message.sticker.file_id, in_memory=True)
            if b:
                d = await process_sticker(b.getvalue())
                return {"url": d} if d else None

        media = (message.photo or message.video or message.video_note
                 or message.animation or message.document)
        if media:
            fid = getattr(media, "file_id", None)
            if fid:
                b = await client.download_media(fid, in_memory=True)
                if b:
                    d = await process_image(b.getvalue(), circular=bool(message.video_note))
                    return {"url": d} if d else None
    except Exception as e:
        logger.error(f"get_media_data: {e}")
    return None


def get_display_name(user) -> str:
    if not user:
        return "Unknown"
    parts = []
    if getattr(user, "first_name", None):
        parts.append(user.first_name)
    if getattr(user, "last_name", None):
        parts.append(user.last_name)
    return " ".join(parts) or getattr(user, "username", None) or "Unknown"


async def send_to_api(url: str, data: dict):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: requests.post(url, json=data, timeout=30)
        )
    except Exception as e:
        logger.error(f"API: {e}")
        return None


# ==================== XABAR TO'PLASH ====================

async def get_user_info(client: Client, message: Message):
    try:
        if message.forward_from:
            return message.forward_from
        if message.forward_from_chat:
            return message.forward_from_chat
        if message.from_user:
            return message.from_user
        if message.sender_chat:
            return message.sender_chat
    except Exception:
        pass
    return message.from_user


async def collect_messages(client: Client, trigger_message: Message, count: int) -> Optional[List[dict]]:
    try:
        reply = trigger_message.reply_to_message
        if not reply:
            return None

        if count == 1:
            # Bir xabar bo'lsa to'liq yuklash
            try:
                full_reply = await client.get_messages(trigger_message.chat.id, reply.id)
                messages = [full_reply if full_reply else reply]
            except Exception:
                messages = [reply]
        else:
            messages = []
            async for msg in client.get_chat_history(
                trigger_message.chat.id,
                limit=count,
                offset_id=reply.id - 1,
            ):
                # reply_to_message to'liq yuklanmagan bo'lsa qayta olish
                if msg.reply_to_message_id and not msg.reply_to_message:
                    try:
                        full_msg = await client.get_messages(trigger_message.chat.id, msg.id)
                        messages.append(full_msg if full_msg else msg)
                    except Exception:
                        messages.append(msg)
                else:
                    messages.append(msg)
            messages.reverse()

        result = []
        for msg in messages:
            try:
                user_info = await get_user_info(client, msg)
                if not user_info:
                    continue

                display_name = get_display_name(user_info)
                avatar = await get_avatar(client, user_info.id) if getattr(user_info, "id", None) else None

               
                reply_data = None
                if msg.reply_to_message:
                    try:
                        rm = msg.reply_to_message
                        rs = rm.from_user or rm.sender_chat
                        rname = get_display_name(rs) if rs else "Unknown"
                        rtext = get_media_description(rm, True)
                        if rm.text:
                            rtext = f"{rtext}. {rm.text}" if rtext else rm.text
                        elif rm.caption:
                            rtext = f"{rtext}. {rm.caption}" if rtext else rm.caption
                        ravatar = await get_avatar(client, rs.id) if rs and getattr(rs, "id", None) else None
                        reply_data = {
                            "name": rname,
                            "text": rtext or "",
                            "entities": parse_entities(rm.entities or rm.caption_entities),
                            "chatId": getattr(rs, "id", msg.chat.id),
                            "from": {
                                "id": getattr(rs, "id", 0),
                                "name": rname,
                                "photo": {"url": ravatar} if ravatar else {}
                            }
                        }
                    except Exception:
                        reply_data = None
                # Media
                media_data = None
                if has_preview(msg):
                    media_data = await get_media_data(client, msg)

                text = msg.text or msg.caption or ""
                desc = get_media_description(msg)
                if desc:
                    text = f"{text}\n\n{desc}" if text else desc

                obj = {
                    "from": {
                        "id": getattr(user_info, "id", 0),
                        "first_name": getattr(user_info, "first_name", "") or "",
                        "last_name": getattr(user_info, "last_name", "") or "",
                        "username": getattr(user_info, "username", None),
                        "name": display_name,
                        "photo": {"url": avatar} if avatar else {}
                    },
                    "text": text,
                    "entities": parse_entities(msg.entities or msg.caption_entities),
                    "avatar": True
                }

                if media_data:
                    obj["media"] = media_data
                if reply_data:
                    obj["replyMessage"] = reply_data

                result.append(obj)
            except Exception:
                continue

        return result if result else None

    except Exception as e:
        logger.error(f"collect_messages: {e}")
        return None


async def create_fake_messages(client: Client, raw_text: str, reply: Optional[Message]) -> List[dict]:
    async def parse_user(chunk: str):
        parts = chunk.split()
        if not parts:
            return None, ""
        ident = parts[0].lstrip("@")
        text  = chunk.split(maxsplit=1)[1] if len(parts) > 1 else ""
        try:
            uid  = int(ident) if ident.isdigit() else ident
            user = await client.get_users(uid)
            return user, text
        except Exception:
            return None, text

    # Faqat reply
    if reply and not raw_text:
        user = reply.from_user or reply.sender_chat
        name = get_display_name(user)
        av   = await get_avatar(client, user.id) if user else None
        return [{
            "from": {
                "id": getattr(user, "id", 0),
                "first_name": getattr(user, "first_name", "") or "",
                "last_name": getattr(user, "last_name", "") or "",
                "username": getattr(user, "username", None),
                "name": name,
                "photo": {"url": av} if av else {}
            },
            "text": "", "entities": [], "avatar": True
        }]

    # Reply + matn: reply userga matn yozish
    if reply and raw_text:
        user = reply.from_user or reply.sender_chat
        uid  = getattr(user, "id", "")
        return await create_fake_messages(client, f"{uid} {raw_text}", None)

    # Ko'p fake: ajratuvchi " ; "
    result = []
    for part in raw_text.split("; "):
        try:
            rdata = None
            if " -r " in part:
                main, rpart = part.split(" -r ", 1)
                u1, t1 = await parse_user(main)
                u2, t2 = await parse_user(rpart)
            else:
                u1, t1 = await parse_user(part)
                u2, t2 = None, None

            if not u1:
                continue

            n1  = get_display_name(u1)
            av1 = await get_avatar(client, u1.id)

            if u2:
                n2  = get_display_name(u2)
                av2 = await get_avatar(client, u2.id)
                rdata = {
                    "name": n2, "text": t2, "entities": [],
                    "chatId": u2.id,
                    "from": {"name": n2, "photo": {"url": av2} if av2 else {}}
                }

            obj = {
                "from": {
                    "id": u1.id,
                    "first_name": getattr(u1, "first_name", "") or "",
                    "last_name": getattr(u1, "last_name", "") or "",
                    "username": getattr(u1, "username", None),
                    "name": n1,
                    "photo": {"url": av1} if av1 else {}
                },
                "text": t1, "entities": [], "avatar": True
            }
            if rdata:
                obj["replyMessage"] = rdata
            result.append(obj)
        except Exception:
            continue

    return result


async def send_quote(client: Client, message: Message, messages_data: List[dict],
                     bg_color: str = QUOTE_BG_COLOR, as_file: bool = False):
    payload = {
        "backgroundColor": bg_color,
        "width": QUOTE_WIDTH,
        "height": QUOTE_HEIGHT,
        "scale": QUOTE_SCALE,
        "emojiBrand": QUOTE_EMOJI,
        "messages": messages_data,
        "format": "webp",
        "type": "quote"
    }

    response = await send_to_api(f"{QUOTE_ENDPOINT}.webp", payload)

    if not response or response.status_code != 200:
        err = "Неизвестная ошибка"
        if response:
            try:
                err = response.json().get("error", f"HTTP {response.status_code}")
            except Exception:
                err = f"HTTP {response.status_code}"
        return False, err

    # WebP -> stiker sifatida yuborish (to'g'ridan-to'g'ri, konvertatsiyasiz)
    ts  = int(asyncio.get_event_loop().time())
    rid = message.id if message.reply_to_message else None

    webp_buf = io.BytesIO(response.content)
    webp_buf.name = f"quote_{ts}.webp"

    try:
        if as_file:
            # !file rejimida PNG dokument sifatida
            try:
                img = Image.open(io.BytesIO(response.content))
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                png_buf = io.BytesIO()
                img.save(png_buf, format="PNG")
                png_buf.seek(0)
                png_buf.name = f"quote_{ts}.png"
            except Exception as e:
                return False, f"PNG konvertatsiya xatoligi: {e}"
            await client.send_document(message.chat.id, document=png_buf, caption="",
                                       reply_to_message_id=rid, force_document=True)
        else:
            # Oddiy rejimda stiker sifatida
            await client.send_sticker(message.chat.id, sticker=webp_buf,
                                      reply_to_message_id=rid)
    except (ValueError, Exception):
        # Peer xatoligi yoki boshqa — reply_to siz qayta urinish
        webp_buf.seek(0)
        try:
            if as_file:
                await client.send_document(message.chat.id, document=png_buf, caption="",
                                           force_document=True)
            else:
                await client.send_sticker(message.chat.id, sticker=webp_buf)
        except Exception as e2:
            return False, str(e2)
    return True, None


# ==================== QUOTE BUYRUQLARI ====================
@app.on_message(filters.command("q", prefixes=PREFIX) & filters.me)
async def q_handler(client: Client, message: Message):
    """
    .q           — standart rang (#1b1429)
    .q 3         — 3 ta xabar
    .q light     — oq fon
    .q dark      — qora fon
    .q gradient  — gradient
    .q random    — tasodifiy rang
    .q #hex      — o'z ranging
    .q !file     — fayl sifatida
    """
    if not message.reply_to_message:
        await message.edit("🙂 Xabarni reply qiling")
        return

    args     = message.text.split()[1:]
    as_file  = "!file" in args
    count    = 1
    bg_color = QUOTE_BG_COLOR  # default: #1b1429

    # Rang shortcut-lari
    COLOR_MAP = {
        "dark":     QUOTE_BG_DARK,
        "light":    QUOTE_BG_LIGHT,
        "gradient": QUOTE_BG_GRAD,
        "blue":     QUOTE_BG_BLUE,
        "random":   QUOTE_BG_RANDOM,
    }

    for arg in args:
        if arg == "!file":
            continue
        elif arg.isdigit() and int(arg) > 0:
            count = int(arg)
        elif arg in COLOR_MAP:
            bg_color = COLOR_MAP[arg]
        elif arg.startswith("#") or arg.startswith("//") or "/" in arg:
            bg_color = arg  # HEX yoki gradient (#111/#222)

    if count > QUOTE_MAX_MSGS:
        await message.edit(f"🙂 Maksimum {QUOTE_MAX_MSGS} ta xabar")
        return

    status = await message.edit("🙂 Обработка…")
    messages_data = await collect_messages(client, message, count)

    if not messages_data:
        await status.edit("🙂 Xabarlarni to'plab bo'lmadi")
        return

    await status.edit("🙂 Ожидание ответа API…")
    ok, err = await send_quote(client, message, messages_data, bg_color, as_file)

    if not ok:
        await status.edit(f"🙂 Ошибка API: {err}")
        return

    await status.delete()

@app.on_message(filters.command("fq", prefixes=PREFIX) & filters.me)
async def fq_handler(client: Client, message: Message):
    """
    .fq @user matn              — Fake quote
    .fq 123456 matn             — ID bilan
    .fq @u1 m1 ; @u2 m2        — Ko'p fake (ajratuvchi: ' ; ')
    .fq @u1 m -r @u2 replym    — Reply bilan fake
    (Reply ustida) .fq matn    — Reply qilingan odamga
    """
    raw   = " ".join(message.text.split()[1:])
    reply = message.reply_to_message

    if not raw and not reply:
        await message.edit(
            "🙂 Format:\n"
            "`.fq @username matn`\n"
            "`.fq 123456 matn`\n"
            "Ko'p: `.fq @u1 m1 ; @u2 m2`\n"
            "Reply bilan fake: `.fq @u1 m -r @u2 reply`"
        )
        return

    status = await message.edit("🙂 Обработка…")

    try:
        messages_data = await create_fake_messages(client, raw, reply)
    except Exception as e:
        await status.edit(f"🙂 Xatolik: {e}")
        return

    if not messages_data:
        await status.edit("🙂 Foydalanuvchi topilmadi")
        return

    if len(messages_data) > QUOTE_MAX_MSGS:
        await status.edit(f"🙂 Maksimum {QUOTE_MAX_MSGS} ta xabar")
        return

    await status.edit("🙂 Ожидание ответа API…")
    ok, err = await send_quote(client, message, messages_data)

    if not ok:
        await status.edit(f"🙂 Ошибка API: {err}")
        return

    await status.delete()


# ==================== xAI GROK ====================

def get_grok_response(text: str, chat_id: int = None) -> str:
    history = ai_auto_reply["chat_history"].get(str(chat_id), [])
    history.append({"role": "user", "content": text})

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Sen o'zbek tilida gaplashadigan do'stona yordamchisan. "
                "Qisqa va tabiiy javob ber. Rasmiy emas, oddiy suhbat uslubida."
            )
        }
    ] + history[-10:]

    # Har bir modelni sinab ko'rish
    for model in XAI_MODELS:
        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 300,
            }
            response = requests.post(XAI_API_URL, headers=headers, json=payload, timeout=20)

            if response.status_code == 200:
                ai_text = response.json()["choices"][0]["message"]["content"].strip()
                history.append({"role": "assistant", "content": ai_text})
                ai_auto_reply["chat_history"][str(chat_id)] = history[-20:]
                # Keyingi safar shu model birinchi bo'lsin
                if model != XAI_MODELS[0]:
                    XAI_MODELS.remove(model)
                    XAI_MODELS.insert(0, model)
                print(f"✅ Grok model: {model}")
                return ai_text
            elif response.status_code == 404 or "Model not found" in response.text:
                print(f"⚠️ Model topilmadi: {model}, keyingisi sinab ko'rilmoqda...")
                continue
            elif response.status_code == 403:
                print(f"Grok 403: API key ruxsati yetarli emas")
                return _fallback(text)
            else:
                print(f"Grok {response.status_code} ({model}): {response.text[:100]}")
                continue
        except Exception as e:
            print(f"Grok xatolik ({model}): {e}")
            continue

    print("Barcha modellar ishlamadi, fallback ishlatilmoqda")
    return _fallback(text)


def _fallback(text: str) -> str:
    tl = text.lower()
    if any(w in tl for w in ["salom", "hi", "hello"]):
        return random.choice(["Salom! 😊", "Hey!", "Assalomu alaykum!"])
    if any(w in tl for w in ["rahmat", "raxmat", "thanks"]):
        return random.choice(["Arzimaydi 😊", "Hech qisi yo'q!"])
    return random.choice(["Xo'p 👍", "Tushundim!", "Yaxshi!"])


# ==================== AI BUYRUQLARI ====================

@app.on_message(filters.private & filters.incoming & ~filters.bot & ~filters.service)
async def ai_reply_handler(client: Client, message: Message):
    if not ai_auto_reply["enabled"]:
        return
    if message.chat.id in ai_auto_reply["exclude_chats"]:
        return
    if not message.text or message.text.startswith(PREFIX):
        return
    try:
        await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(ai_auto_reply["min_delay"], ai_auto_reply["max_delay"]))
        reply = get_grok_response(message.text, message.chat.id)
        await message.reply_text(reply)
        print(f"🤖 Grok -> {message.chat.first_name}: {reply[:50]}")
    except Exception as e:
        print(f"AI xatolik: {e}")


@app.on_message(filters.command("aion", prefixes=PREFIX) & filters.me)
async def aion(client: Client, message: Message):
    ai_auto_reply["enabled"] = True
    await message.edit("🤖 **AI yoqildi!** (Grok xAI)\n🔴 O'chirish: `.aioff`")


@app.on_message(filters.command("aioff", prefixes=PREFIX) & filters.me)
async def aioff(client: Client, message: Message):
    ai_auto_reply["enabled"] = False
    await message.edit("🔴 **AI o'chirildi!**")


@app.on_message(filters.command("aiclear", prefixes=PREFIX) & filters.me)
async def aiclear(client: Client, message: Message):
    ai_auto_reply["chat_history"] = {}
    await message.edit("🧹 AI chat tarixi tozalandi!")


@app.on_message(filters.command("aistatus", prefixes=PREFIX) & filters.me)
async def aistatus(client: Client, message: Message):
    s = "🟢 Yoqilgan" if ai_auto_reply["enabled"] else "🔴 O'chirilgan"
    await message.edit(
        f"🤖 **AI STATUS**\n\n"
        f"Holat: {s}\n"
        f"Model: Grok (xAI)\n"
        f"Chatlar: {len(ai_auto_reply['chat_history'])}\n"
        f"Delay: {ai_auto_reply['min_delay']}-{ai_auto_reply['max_delay']}s"
    )


@app.on_message(filters.command("ask", prefixes=PREFIX) & filters.me)
async def ask_handler(client: Client, message: Message):
    """.ask <savol> — Grokdan javob"""
    if len(message.command) < 2:
        await message.edit("❌ `.ask <savol>`")
        return
    q      = message.text.split(None, 1)[1]
    status = await message.edit("🤔 Grok o'ylamoqda…")
    ans    = get_grok_response(q, message.chat.id)
    await status.edit(f"🧠 **Grok:**\n{ans}")


# ==================== ASOSIY BUYRUQLAR ====================

@app.on_message(filters.command("help", prefixes=PREFIX) & filters.me)
async def help_handler(client: Client, message: Message):
    await message.edit("""
🤖 **USERBOT ULTIMATE**
**📸 QUOTES**
- `.q` — standart rang (#1b1429)
- `.q 3` — 3 ta xabar
- `.q dark` — qora fon
- `.q light` — oq fon  
- `.q gradient` — binafsha gradient
- `.q blue` — Telegram dark
- `.q random` — tasodifiy rang
- `.q #hex` — o'z ranging
- `.q #111/#222` — gradient
- `.q !file` — fayl sifatida
- `.fq @user matn` — fake quote

**🧠 AI (Grok xAI)**
• `.aion` / `.aioff` — Yoq/o'chir
• `.aistatus` — Holat
• `.aiclear` — Tarixni tozala
• `.ask <savol>` — Grokdan javob

**🎯 ASOSIY**
• `.alive` `.ping` `.restart`

**📝 XABAR**
• `.del` `.purge`
• `.spam <n> <txt>` `.type <txt>`

**🎨 MATN**
• `.reverse` `.upper` `.lower` `.mock`

**🎮 O'YIN**
• `.dice` 🎲 `.dart` 🎯 `.slot` 🎰

**💤 AFK** • `.afk [sabab]`
""")


@app.on_message(filters.command("alive", prefixes=PREFIX) & filters.me)
async def alive_handler(client: Client, message: Message):
    uptime = int(time.time() - start_time)
    ai_s   = "🟢 Grok" if ai_auto_reply["enabled"] else "🔴 Off"
    await message.edit(
        f"🤖 **USERBOT ULTIMATE**\n\n"
        f"⏰ Uptime: {uptime}s\n"
        f"🐍 Python: {sys.version_info.major}.{sys.version_info.minor}\n"
        f"🧠 AI: {ai_s}\n✅ Online"
    )


@app.on_message(filters.command("ping", prefixes=PREFIX) & filters.me)
async def ping_handler(client: Client, message: Message):
    s = time.time()
    await message.edit("🏓…")
    ms = round((time.time() - s) * 1000, 2)
    await message.edit(f"🏓 **Pong!** `{ms}ms`")


@app.on_message(filters.command("restart", prefixes=PREFIX) & filters.me)
async def restart_handler(client: Client, message: Message):
    await message.edit("🔄 Restarting…")
    await asyncio.sleep(1)
    os.execl(sys.executable, sys.executable, *sys.argv)


@app.on_message(filters.command("del", prefixes=PREFIX) & filters.me)
async def del_handler(client: Client, message: Message):
    if message.reply_to_message:
        await message.reply_to_message.delete()
    await message.delete()


@app.on_message(filters.command("purge", prefixes=PREFIX) & filters.me)
async def purge_handler(client: Client, message: Message):
    if not message.reply_to_message:
        await message.edit("❌ Reply!")
        return
    deleted = 0
    for mid in range(message.reply_to_message.id, message.id + 1):
        try:
            await client.delete_messages(message.chat.id, mid)
            deleted += 1
        except Exception:
            pass
    s = await message.reply(f"🗑️ {deleted} ta o'chirildi")
    await asyncio.sleep(2)
    await s.delete()


@app.on_message(filters.command("spam", prefixes=PREFIX) & filters.me)
async def spam_handler(client: Client, message: Message):
    try:
        args  = message.text.split(None, 2)
        count = min(int(args[1]), 50)
        text  = args[2]
        await message.delete()
        for _ in range(count):
            await client.send_message(message.chat.id, text)
            await asyncio.sleep(0.3)
    except Exception:
        await message.edit("❌ `.spam <n> <txt>`")


@app.on_message(filters.command("type", prefixes=PREFIX) & filters.me)
async def type_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.edit("❌ `.type <txt>`")
        return
    text   = message.text.split(None, 1)[1]
    typing = ""
    for char in text:
        typing += char
        try:
            await message.edit(typing + "▌")
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.edit(typing)


@app.on_message(filters.command(["reverse", "upper", "lower", "mock"], prefixes=PREFIX) & filters.me)
async def text_transform(client: Client, message: Message):
    cmd  = message.command[0]
    text = (message.reply_to_message.text if message.reply_to_message
            else message.text.split(None, 1)[1] if len(message.command) > 1 else None)
    if not text:
        await message.edit(f"❌ `.{cmd} <txt>`")
        return
    transforms = {
        "reverse": lambda t: t[::-1],
        "upper":   str.upper,
        "lower":   str.lower,
        "mock":    lambda t: "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(t))
    }
    await message.edit(transforms[cmd](text))


@app.on_message(filters.command(["dice", "dart", "slot"], prefixes=PREFIX) & filters.me)
async def game_handler(client: Client, message: Message):
    await message.delete()
    await client.send_dice(
        message.chat.id,
        {"dice": "🎲", "dart": "🎯", "slot": "🎰"}[message.command[0]]
    )


# ==================== AFK ====================

@app.on_message(filters.command("afk", prefixes=PREFIX) & filters.me)
async def afk_handler(client: Client, message: Message):
    global afk_status
    reason = message.text.split(None, 1)[1] if len(message.command) > 1 else "Sabab yo'q"
    afk_status = {"is_afk": True, "reason": reason, "time": datetime.now()}
    await message.edit(f"💤 **AFK!**\n📝 {reason}")


@app.on_message(filters.mentioned & ~filters.me & ~filters.bot)
async def afk_mention(client: Client, message: Message):
    if afk_status["is_afk"]:
        await message.reply(f"💤 AFKdaman!\n📝 {afk_status['reason']}")


@app.on_message(filters.outgoing & filters.text)
async def unafk(client: Client, message: Message):
    global afk_status
    if afk_status["is_afk"] and not message.text.startswith(PREFIX):
        afk_status = {"is_afk": False, "reason": None, "time": None}
        await message.reply("✅ AFK o'chirildi!")


# ==================== RUN ====================

if __name__ == "__main__":
    print("\n🚀 USERBOT ULTIMATE ishga tushmoqda…")
    print("📸 Quotes: .q / .fq  →  bot.lyo.su API")
    print("🧠 AI: Grok xAI  →  .aion")
    print("📱 .help — barcha buyruqlar")

    # Session tekshirish
    session_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)) or ".", f"{SESSION_NAME}.session"
    )
    if os.path.exists(session_path):
        print(f"✅ Session topildi: {session_path} — qayta kiritish shart emas")
    else:
        print("⚠️  Session topilmadi — birinchi marta telefon raqam/kod so'raladi")

    print("=" * 60)
    try:
        app.run()
    except KeyboardInterrupt:
        print("\n👋 To'xtatildi!")
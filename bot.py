from telegram import Update, InlineQueryResultPhoto
from telegram.ext import Application, MessageHandler, filters, ContextTypes, InlineQueryHandler
import uuid

# Telegram bot tokeningiz
TOKEN = "8330664486:AAF1DDAcfv_TRgVDhKXR7OynGOLqdOGRdZw"

# Futbolchi bazasi
players = {
    "messi": {
        "info": "Lionel Messi — Argentina, 1987-yil, Inter Miami futbolchisi.",
        "photo": "https://t.me/Futbolchilar_rasmi/1"
    },
    "ronaldo": {
        "info": "Cristiano Ronaldo — Portugaliya, 1985-yil, Al-Nassr hujumchisi.",
        "photo": "https://t.me/Futbolchilar_rasmi/2"
    },
    # Shu tarzda boshqa futbolchilar qo‘shing
}

chat_responses = {
    "salom": "Assalomu alaykum! Futbol savollaringiz bormi? ⚽😊",
    "qalaysan": "Yaxshi, rahmat! Sizchi?",
    "rahmat": "Arzimaydi! Yana savol bo‘lsa yozing ⚽",
}

def ai_think(msg):
    msg = msg.lower()
    for name in players:
        if name in msg:
            if "qayerlik" in msg:
                return players[name]["info"].split("—")[1].split(",")[0]
            if "yosh" in msg or "tug‘ilgan" in msg:
                return players[name]["info"].split(",")[1]
            if "klub" in msg:
                return players[name]["info"].split(",")[-1]
            return players[name]["info"]
    for key in chat_responses:
        if key in msg:
            return chat_responses[key]
    return "Futbolchi ismini yozing, masalan: Messi"

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    await update.message.reply_text(ai_think(msg))

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower()
    if query in players:
        image_url = players[query]["photo"]
    else:
        image_url = "https://i.imgur.com/Qh7QmZp.jpeg"  # rasm topilmasa
    result = [
        InlineQueryResultPhoto(
            id=str(uuid.uuid4()),
            photo_url=image_url,
            thumbnail_url=image_url
        )
    ]
    await update.inline_query.answer(result)

app = Application.builder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT, reply))
app.add_handler(InlineQueryHandler(inline_query))

print("✅ Futbol AI Bot ishga tushdi...")
app.run_polling()
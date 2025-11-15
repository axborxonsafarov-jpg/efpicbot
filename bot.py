from telegram import Update, InlineQueryResultPhoto
from telegram.ext import Application, MessageHandler, filters, ContextTypes, InlineQueryHandler, CommandHandler
import uuid
import re
import os

TOKEN = os.getenv("8330664486:AAGLoPR6ISNvb1KIu8rne4_Rb7blhFYDFog", "")

# ======== Foydalanuvchilar ro‘yxati ========
users = set()   # start bosgan barcha user_id lar shu yerga qo‘shiladi

# ================= Futbolchi bazasi =================
players = {
    "messi": {
        "info": "Lionel Messi — Argentina, 1987-yil, Inter Miami futbolchisi.",
        "photo": "https://t.me/efpicchannel/32"
    },
    "ronaldo": {
        "info": "Cristiano Ronaldo — Portugaliya, 1985-yil, Al-Nassr hujumchisi.",
        "photo": "https://t.me/efpicchannel/34"
    },
    "neymar": {
        "info": "Neymar Jr — Braziliya, 1992-yil, Al-Hilal futbolchisi.",
        "photo": "https://t.me/efpicchannel/38"
    },
    "mbappe": {
        "info": "Kylian Mbappé — Fransiya, 1998-yil, Real Madrid o‘yinchisi.",
        "photo": "https://t.me/efpicchannel/35"
    },
    "scholes": {
        "info": "Paul Scholes 1974-yil 16-noyabr Angliyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Manchester United hamda Angliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/86"
    },
    "bekenbour": {
        "info": "Franz Beckenbauer 1945-yil 11-sentabr Germaniyada tug‘ilgan.U futbol afsonasi, himoyachi va Bayern München hamda Germaniya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/85"
    },
    "bekham": {
        "info": "David Beckham 1975-yil 2-may Angliyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Manchester United, Real Madrid hamda Angliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/84"
    },
    "maradona": {
        "info": "Diego Maradona 1960-yil 30-oktabr Argentinada tug‘ilgan.U futbol afsonasi, hujumchi va Napoli hamda Argentina terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/83"
    },
    "kvaratshele": {
        "info": "Khvicha Kvaratskhelia 2001-yil 12- februar Gruziyada tug‘ilgan.U futbol afsonasi, hujumchi va Napoli hamda Gruziya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/82"
    },
    "van der sar": {
        "info": "Edwin van der Sar 1970-yil 29-oktabr Niderlandiyada tug‘ilgan.U futbol afsonasi, darvozabon va Ajax, Manchester United hamda Niderlandiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/80"
    },
    "abduqodir": {
        "info": "Abduqodir Husanov 2002-yil 4-mart O‘zbekistonda tug‘ilgan.U futbol afsonasi, hujumchi va Manchester City hamda O‘zbekiston terma jamoasi yulduzi. ",
        "photo": "https://t.me/efpicchannel/79"
    },
    "hazard": {
        "info": "Eden Hazard 1991-yil 7-yanvar Belgiya, La Louvière shahrida tug‘ilgan.U futbol afsonasi, hujumchi va Real Madrid hamda Belgiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/78"
    },
    "yamal": {
        "info": "Lamine Yamal 2007-yil 2007-yil Ispaniyada tug‘ilgan.U futbol afsonasi, hujumchi va Barcelona hamda Ispaniya terma jamoasi yosh yulduzi.",
        "photo": "https://t.me/efpicchannel/77"
    },
    "dembele": {
        "info": "Ousmane Dembélé 1997-yil 15-may Fransiyada tug‘ilgan.U futbol afsonasi, hujumchi va PSG hamda Fransiya terma jamoasi o‘yinchisi.",
        "photo": "https://t.me/efpicchannel/76"
    },
    "puyol": {
        "info": "Carles Puyol 1978-yil 13-aprelda Ispaniyada tug‘ilgan.U futbol afsonasi, himoyachi va Barcelona hamda Ispaniya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/75"
    },
    "puskash": {
        "info": "Ferens Puskash 1927-yil 1-aprelda Vengriyada tug‘ilgan.U futbol afsonasi, hujumchi va Real Madrid hamda Vengriya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/74"
    },
    "zico": {
        "info": "Zico 1953-yil 3-sentabr Braziliyada tug‘ilgan.U futbol afsonasi, hujumchi va Braziliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/72"
    },
    "suarez": {
        "info": "Luis Suárez 1987-yil 24-yanvar Urugvayda tug‘ilgan.U futbol afsonasi, hujumchi va Barcelona hamda Urugvay terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/71"
    },
    "shevchenko": {
        "info": "Andriy Shevchenko 1976-yil 29-sentabr Ukrainada tug‘ilgan.U futbol afsonasi, hujumchi va Milan hamda Ukraina terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/70"
    },
    "patrik viera": {
        "info": "Patrick Vieira 1976-yil 23-iyun Senegal tug‘ilgan, lekin Fransiya fuqaroligiga ega.U futbol afsonasi, yarim himoyachi va Arsenal hamda Fransiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/68"
    },
    "rijkard": {
        "info": "Frank Rijkaard 1962-yil 30-september Niderlandiyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Barcelona hamda Niderlandiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/66"
    },
    "chiesa": {
        "info": "Federico Chiesa 1997-yil 25-oktabr Italiyada tug‘ilgan.U futbol afsonasi, hujumchi va Juventus hamda Italiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/62"
    },
    "del piero": {
        "info": "Alessandro Del Piero 1974-yil 9-noyabr Italiyada tug‘ilgan.U futbol afsonasi, hujumchi va Juventus hamda Italiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/61"
    },
    "owen": {
        "info": "Michael Owen 1979-yil 14-dekabr Angliyada tug‘ilgan.U futbol afsonasi, hujumchi va Liverpool hamda Angliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/60"
    },
    "salah": {
        "info": "Mohamed Salah 1992-yil 15-iyun Misrda tug‘ilgan.U futbol afsonasi, hujumchi va Liverpool hamda Misr terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/59"
    },
    "chex": {
        "info": "Petr Čech 1982-yil 20-may Chexiyada tug‘ilgan.U futbol afsonasi, darvozabon va Chelsea hamda Chexiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/58"
    },
    "maldini": {
        "info": "Paolo Maldini 1968-yil 26-iyun Italiyada tug‘ilgan.U futbol afsonasi, himoyachi va AC Milan hamda Italiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/57"
    },
    "gullit": {
        "info": "Ruud Gullit 1962-yil 1-sentabr Niderlandiyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va AC Milan hamda Niderlandiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/56"
    },
    "ronaldinho": {
        "info": "Ronaldinho 1980-yil 21-mart Braziliyada tug‘ilgan.U futbol afsonasi, hujumchi va Barcelona hamda Braziliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/33"
    },
    "bale": {
        "info": "Gareth Bale 1989-yil 16-iyul Uelsda tug‘ilgan.U futbol afsonasi, hujumchi va Real Madrid hamda Uels terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/36"
    },
    "pepe": {
        "info": "Pepe 1983-yil 26-fevral Braziliyada tug‘ilgan.U futbol afsonasi, himoyachi va Porto, Real Madrid hamda Portugaliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/37"
    },
    "rummenigge": {
        "info": "Karl-Heinz Rummenigge 1955-yil 25-sentabr Germaniyada tug‘ilgan.U futbol afsonasi, hujumchi va Bayern München hamda Germaniya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/44"
    },
    "platini": {
        "info": "Michel Platini 1955-yil 21-iyun Fransiyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Juventus hamda Fransiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/45"
    },
    "cruyff": {
        "info": "Johan Cruyff 1947-yil 25-aprel Niderlandiyada tug‘ilgan.U futbol afsonasi, hujumchi va Ajax, Barcelona hamda Niderlandiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/46"
    },
    "thuram": {
        "info": "Lilian Thuram 1972-yil 1-yanvar Fransiyada tug‘ilgan.U futbol afsonasi, himoyachi va Juventus, Barcelona hamda Fransiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/47"
    },
    "nedved": {
        "info": "Pavel Nedvěd 1972-yil 30-avgust Chexiyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Juventus hamda Chexiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/49"
    },
    "koller": {
        "info": "Jan Koller 1973-yil 30-may Chexiyada tug‘ilgan.U futbol afsonasi, hujumchi va Borussia Dortmund hamda Chexiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/50"
    },
    "eto": {
        "info": "Samuel Eto’o 1981-yil 10-mart Kamerunda tug‘ilgan.U futbol afsonasi, hujumchi va Barcelona hamda Kamerun terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/52"
    },
    "shmexl": {
        "info": "Peter Schmeichel 1963-yil 18-noyabr Daniyada tug‘ilgan.U futbol afsonasi, darvozabon va Manchester United hamda Daniya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/53"
    },
    "kaka": {
        "info": "Kaká 1982-yil 22-aprel Braziliyada tug‘ilgan.U futbol afsonasi, hujumchi va AC Milan hamda Braziliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/54"
    },
    "carlos": {
        "info": "Carlos 1976-yil 17-febral Braziliyada tug‘ilgan.U futbol afsonasi, himoyachi va Braziliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/55"
    },
    "buffon": {
        "info": "Gianluigi Buffon 1978-yil 28-yanvar Italiyada tug‘ilgan.U futbol afsonasi, darvozabon va Juventus hamda Italiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/91"
    },
    "nesta": {
        "info": "Alessandro Nesta 1976-yil 19-mart Italiyada tug‘ilgan.U futbol afsonasi, himoyachi va Lazio, AC Milan hamda Italiya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/90"
    },
    "belingham": {
        "info": "Jude Bellingham 2003-yil 29-iyun Angliyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Real Madrid hamda Angliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/89"
    },
    "figo": {
        "info": "Luis Figo 1972-yil 4-noyabr Portugaliyada tug‘ilgan.U futbol afsonasi, yarim himoyachi va Barcelona, Real Madrid hamda Portugaliya terma jamoasi yulduzi.",
        "photo": "https://t.me/efpicchannel/88"
    },
}

# ================= Oddiy chat javoblari =================
chat_responses = {
    "salom": "Assalomu alaykum! Futbol savollaringiz bormi? ⚽️😊",
    "qalaysan": "Yaxshi, rahmat! Sizchi?",
    "rahmat": "Arzimaydi! Yana savol bo‘lsa yozing ⚽️",
    "kim": "Siz futbolchi haqida so‘rayapsizmi? Ismini yozing (masalan: Messi)",
    "sen kimsan": "Men futbol AI botman. Sizga o‘yinchilar haqida aytaman 🤖⚽️",
    "yordam": " Reklama boyicha @axborxonsafarov ga murojaat qilamiz",
}

# ================= /start handler =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    users.add(user_id)  # foydalanuvchini ro‘yxatga qo‘shish

    await update.message.reply_text(
        "Salom! 👋\nFutbolchi haqida bilmoqchi bo‘lsangiz ismini yozing.\n"
        "Masalan: Messi, Ronaldo, Neymar..."
    )

# ================= /users (foydalanuvchilar soni) =================
async def users_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👥 Botdagi foydalanuvchilar soni: {len(users)} nafar")

async def yordam(update: Update,context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Reklama boyicha @axborxonsafarov ga murojaat qilamiz.  Bizning kanal @china_efootball")

# ================= AI javob beruvchi funksiyasi =================
def ai_think(msg: str) -> str:
    msg = msg.lower().strip()

    # Futbolchi so‘ralganda
    for name, data in players.items():
        if name in msg:
            if "qayerlik" in msg:
                return data["info"].split("—")[1].split(",")[0].strip()
            if "yosh" in msg or "tug‘ilgan" in msg:
                return data["info"].split(",")[1].strip()
            if "klub" in msg:
                return data["info"].split(",")[-1].strip()
            return data["info"]

    # Oddiy chat javobi (regex bilan alohida so‘z sifatida tekshiradi)
    for key, val in chat_responses.items():
        if re.search(rf"\b{key}\b", msg):
            return val

    return "Hmm, qiziq savol 🤔 Futbolchi ismini qo‘shib yozing: Masalan, Messi qayerlik?"

# ================= Chat handler =================
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        msg = update.message.text
        await update.message.reply_text(ai_think(msg))

# ================= Inline query handler =================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower().strip()
    if query in players:
        image_url = players[query]["photo"]
    else:
        image_url = "https://i.imgur.com/Qh7QmZp.jpeg"

    result = [
        InlineQueryResultPhoto(
            id=str(uuid.uuid4()),
            photo_url=image_url,
            thumbnail_url=image_url
        )
    ]
    await update.inline_query.answer(result, cache_time=1)

# ================= Bot ishga tushirish =================
if __name__ == "__main__":
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_count))
    app.add_handler(CommandHandler("yordam", yordam))  # <-- foydalanuvchilar soni
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
    app.add_handler(InlineQueryHandler(inline_query))
    
    print("✅ Futbol AI Bot ishga tushdi...")
    app.run_polling()




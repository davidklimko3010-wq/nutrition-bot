import os
import base64
import logging
import anthropic
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

DAVID_PROFILE = """
Ты — персональный нутрициолог и фитнес-ассистент Дэйвида. Общаешься только на русском языке, дружески и по делу.

ПРОФИЛЬ: Рост 174 см, Вес 77 кг, Возраст 28 лет, Жир 23.9% → цель 18%, Цель: рекомпозиция тела за 9 недель.

НОРМЫ: 2302 ккал | Белки 150г | Жиры 74г | Углеводы 258г

ТРЕНИРОВКИ: Зал вт/чт/сб (плечи/спина, грудь/трицепс, ноги/бицепс), Падел 2x/нед (~1000 ккал)

ПИТАНИЕ: 2-3 приёма, первый в 11:00, аэрогриль, ЛЕГО-заготовки, креатин 5г/день

ЛЮБИТ: куриные бёдра, фарш, яйца, творог, помидоры, огурцы, фрукты, гречку, рис
НЕ ЛЮБИТ: морковку. ИСКЛЮЧАЕТ: фастфуд, доставку, сладкие напитки

ЛЕГО: Гарниры — гречка/рис/булгур/киноа/чечевица. Мясо — фарш/курица/котлеты/индейка. Овощи — брокколи/перец/кабачок. Соусы — томатный/грибной/карри/песто
"""

food_diary = {}
conversation_history = []


def get_today():
    return date.today().isoformat()


def get_today_diary():
    today = get_today()
    if today not in food_diary:
        food_diary[today] = {"meals": [], "totals": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}}
    return food_diary[today]


def get_remaining():
    t = get_today_diary()["totals"]
    return {"kcal": 2302 - t["kcal"], "protein": 150 - t["protein"], "fat": 74 - t["fat"], "carbs": 258 - t["carbs"]}


def format_bar(current, target, emoji=""):
    pct = min(int((current / max(target, 1)) * 10), 10)
    return f"{emoji} [{'█' * pct}{'░' * (10 - pct)}] {current:.0f}/{target:.0f}"


def format_daily_summary():
    diary = get_today_diary()
    t = diary["totals"]
    lines = [f"📊 *Дневник — {get_today()}*\n"]
    if diary["meals"]:
        lines.append("*Приёмы пищи:*")
        for m in diary["meals"]:
            lines.append(f"  {m['time']} — {m['name']}: {m['kcal']} ккал")
        lines.append("")
    lines += [
        "*Прогресс:*",
        format_bar(t["kcal"], 2302, "🔥"),
        format_bar(t["protein"], 150, "💪"),
        format_bar(t["fat"], 74, "🥑"),
        format_bar(t["carbs"], 258, "🍚"),
    ]
    r = get_remaining()
    lines.append(f"\n*Осталось:* 🔥{r['kcal']:.0f} | 💪{r['protein']:.0f}г | 🥑{r['fat']:.0f}г | 🍚{r['carbs']:.0f}г")
    return "\n".join(lines)


async def ask_claude(user_message, image_base64=None):
    global conversation_history
    content = []
    if image_base64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}})
    content.append({"type": "text", "text": user_message})
    conversation_history.append({"role": "user", "content": content})
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]
    t = get_today_diary()["totals"]
    context = f"\n\nСЕГОДНЯ ({get_today()}): съедено {t['kcal']:.0f} ккал | Б{t['protein']:.0f}г | Ж{t['fat']:.0f}г | У{t['carbs']:.0f}г. Осталось: {get_remaining()['kcal']:.0f} ккал"
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=DAVID_PROFILE + context,
        messages=conversation_history
    )
    reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": reply})
    return reply


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Дневник", callback_data="diary"), InlineKeyboardButton("🎯 Моя цель", callback_data="goal")],
        [InlineKeyboardButton("🍽 Меню на неделю", callback_data="weekly_menu"), InlineKeyboardButton("🛒 Список покупок", callback_data="shopping")],
        [InlineKeyboardButton("🧊 Лего-заготовки", callback_data="lego")]
    ]
    await update.message.reply_text(
        "Привет, Дэйвид! 💪\n\nЯ твой персональный нутрициолог.\n\n"
        "• Напиши что съел → посчитаю КБЖУ\n"
        "• Пришли фото еды → распознаю автоматически\n"
        "• Задай любой вопрос по питанию\n",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "diary":
        await query.message.reply_text(format_daily_summary(), parse_mode="Markdown")
    elif query.data == "goal":
        await query.message.reply_text(
            "🎯 *Твоя цель*\n\nРекомпозиция за 9 недель:\n• Вес: 77 → 72 кг\n• Жир: 23.9% → 18%\n\n📊 *Нормы:*\n🔥 2302 ккал | 💪 150г | 🥑 74г | 🍚 258г",
            parse_mode="Markdown"
        )
    elif query.data == "lego":
        await query.message.reply_text("⏳ Подбираю комбинации...")
        await query.message.reply_text(await ask_claude("Дай идеи комбинаций лего-заготовок на сегодня с учётом того что я уже съел."))
    elif query.data == "weekly_menu":
        await query.message.reply_text("⏳ Составляю меню...")
        await query.message.reply_text(await ask_claude("Составь меню на 7 дней под систему лего-заготовок. Формат: день → приёмы пищи с КБЖУ."))
    elif query.data == "shopping":
        await query.message.reply_text("⏳ Составляю список покупок...")
        await query.message.reply_text(await ask_claude("Составь список покупок в Ашане на неделю для лего-заготовок с количеством каждого продукта."))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Анализирую фото...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    image_base64 = base64.b64encode(photo_bytes).decode("utf-8")
    prompt = f"На фото еда. Определи что это, оцени порцию и посчитай КБЖУ. {update.message.caption or ''}\n\nСпроси: добавить в дневник?"
    await update.message.reply_text(await ask_claude(prompt, image_base64=image_base64))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    if any(w in text for w in ["дневник", "статус", "итого", "сколько осталось"]):
        await update.message.reply_text(format_daily_summary(), parse_mode="Markdown")
        return
    prompt = update.message.text
    if any(w in text for w in ["съел", "поел", "выпил", "перекусил", "завтрак", "обед", "ужин"]):
        prompt += "\n\nПосчитай КБЖУ и спроси добавить в дневник."
    await update.message.reply_text(await ask_claude(prompt))


async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_daily_summary(), parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    food_diary[get_today()] = {"meals": [], "totals": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}}
    await update.message.reply_text("🔄 Дневник сброшен.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("diary", diary_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

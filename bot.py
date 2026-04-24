import os
import json
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

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
AUTHORIZED_USER = os.environ.get("AUTHORIZED_USER_ID", "")  # твой Telegram user_id

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── David's profile ─────────────────────────────────────────────────────────
DAVID_PROFILE = """
Ты — персональный нутрициолог и фитнес-ассистент Дэйвида. Общаешься только на русском языке, дружески и по делу. Без лишней воды.

ПРОФИЛЬ:
- Рост: 174 см, Вес: 77 кг, Возраст: 28 лет
- Жировая масса: 23.9% → цель 18%
- Цель: рекомпозиция тела (перевёрнутый треугольник — широкие плечи/спина, узкая талия)
- Желаемый вес: 72 кг за 9 недель

ДНЕВНЫЕ НОРМЫ:
- Калории: 2302 ккал
- Белки: 150г
- Жиры: 74г
- Углеводы: 258г

ТРЕНИРОВКИ:
- Зал 3x в неделю (вт/чт/сб, 10:00–11:30)
  • День A: плечи/спина
  • День B: грудь/трицепс  
  • День C: ноги/бицепс/пресс
- Падел 2x в неделю (~1000 ккал за тренировку)
- Итого 5 активных дней в неделю

ПИТАНИЕ:
- 2-3 приёма пищи в день
- Первый приём в 11:00 (после тренировки/кофе)
- Система ЛЕГО-заготовок (замороженные кубики: белок, гарнир, овощи, соус)
- Готовит в аэрогриле, максимум 30 мин активной готовки
- Принимает: креатин 5г/день, магний, хлорелла

ЛЮБИТ: куриные бёдра, фарш, яйца, творог, помидоры, огурцы, фрукты, гречку, рис, жидкий желток
НЕ ЛЮБИТ: морковку
ИСКЛЮЧАЕТ: сладкие напитки, фастфуд, доставку, белый хлеб, майонез в больших количествах, алкоголь

ЛЕГО-СИСТЕМА (замороженные кубики):
Гарниры (отлично): гречка, рис, булгур, киноа, чечевица, перловка
Мясо (отлично): фарш, курица кусочками, котлеты/тефтели, индейка, тушёная говядина
Овощи (отлично): брокколи, перец, кабачок, цветная капуста, горошек
Соусы (отлично): томатный, грибной, карри, аджика, песто

Когда Дэйвид пишет что съел — распознай еду, посчитай КБЖУ и добавь в дневник.
Когда присылает фото — опиши что видишь, оцени КБЖУ и спроси подтвердить.
"""

# ── In-memory storage (resets on restart) ───────────────────────────────────
# Format: { "YYYY-MM-DD": { "meals": [...], "totals": {k,b,f,c} } }
food_diary = {}
conversation_history = []


def get_today() -> str:
    return date.today().isoformat()


def get_today_diary():
    today = get_today()
    if today not in food_diary:
        food_diary[today] = {"meals": [], "totals": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}}
    return food_diary[today]


def add_meal(name: str, kcal: int, protein: float, fat: float, carbs: float):
    diary = get_today_diary()
    meal = {
        "time": datetime.now().strftime("%H:%M"),
        "name": name,
        "kcal": kcal,
        "protein": protein,
        "fat": fat,
        "carbs": carbs
    }
    diary["meals"].append(meal)
    diary["totals"]["kcal"] += kcal
    diary["totals"]["protein"] += protein
    diary["totals"]["fat"] += fat
    diary["totals"]["carbs"] += carbs


def get_remaining():
    totals = get_today_diary()["totals"]
    return {
        "kcal": 2302 - totals["kcal"],
        "protein": 150 - totals["protein"],
        "fat": 74 - totals["fat"],
        "carbs": 258 - totals["carbs"]
    }


def format_status_bar(current, target, emoji=""):
    pct = min(int((current / target) * 10), 10)
    bar = "█" * pct + "░" * (10 - pct)
    return f"{emoji} [{bar}] {current:.0f}/{target:.0f}"


def format_daily_summary() -> str:
    diary = get_today_diary()
    totals = diary["totals"]
    rem = get_remaining()
    meals = diary["meals"]

    lines = [f"📊 *Дневник питания — {get_today()}*\n"]

    if meals:
        lines.append("*Приёмы пищи:*")
        for m in meals:
            lines.append(f"  {m['time']} — {m['name']}: {m['kcal']} ккал | Б{m['protein']:.0f} Ж{m['fat']:.0f} У{m['carbs']:.0f}")
        lines.append("")

    lines.append("*Прогресс:*")
    lines.append(format_status_bar(totals["kcal"], 2302, "🔥"))
    lines.append(format_status_bar(totals["protein"], 150, "💪"))
    lines.append(format_status_bar(totals["fat"], 74, "🥑"))
    lines.append(format_status_bar(totals["carbs"], 258, "🍚"))

    lines.append(f"\n*Осталось на сегодня:*")
    lines.append(f"🔥 {rem['kcal']:.0f} ккал | 💪 Б{rem['protein']:.0f}г | 🥑 Ж{rem['fat']:.0f}г | 🍚 У{rem['carbs']:.0f}г")

    return "\n".join(lines)


async def ask_claude(user_message: str, image_base64: str = None) -> str:
    global conversation_history

    # Build message content
    content = []
    if image_base64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}
        })
    content.append({"type": "text", "text": user_message})

    conversation_history.append({"role": "user", "content": content})

    # Keep history manageable
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    # Add current diary context
    diary_context = f"\n\nТЕКУЩИЙ ДНЕВНИК (сегодня {get_today()}):\n"
    diary = get_today_diary()
    diary_context += f"Съедено: {diary['totals']['kcal']:.0f} ккал | Б{diary['totals']['protein']:.0f}г | Ж{diary['totals']['fat']:.0f}г | У{diary['totals']['carbs']:.0f}г\n"
    diary_context += f"Осталось: {get_remaining()['kcal']:.0f} ккал"

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=DAVID_PROFILE + diary_context,
        messages=conversation_history
    )

    assistant_message = response.content[0].text
    conversation_history.append({"role": "assistant", "content": assistant_message})

    return assistant_message


# ── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Дневник", callback_data="diary"),
         InlineKeyboardButton("➕ Добавить еду", callback_data="add_food")],
        [InlineKeyboardButton("🍽 Меню на неделю", callback_data="weekly_menu"),
         InlineKeyboardButton("🛒 Список покупок", callback_data="shopping")],
        [InlineKeyboardButton("💪 Моя цель", callback_data="goal"),
         InlineKeyboardButton("🧊 Лего-заготовки", callback_data="lego")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Привет, Дэйвид! 💪\n\nЯ твой персональный нутрициолог.\n\n"
        "Можешь:\n"
        "• Написать что съел — я посчитаю КБЖУ\n"
        "• Прислать фото еды — распознаю и добавлю\n"
        "• Задать любой вопрос по питанию\n\n"
        "Или выбери действие:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "diary":
        await query.message.reply_text(format_daily_summary(), parse_mode="Markdown")

    elif query.data == "goal":
        await query.message.reply_text(
            "🎯 *Твоя цель*\n\n"
            "Рекомпозиция тела за 9 недель:\n"
            "• Вес: 77 → 72 кг\n"
            "• Жир: 23.9% → 18%\n"
            "• Форма: перевёрнутый треугольник\n\n"
            "📊 *Дневные нормы:*\n"
            "🔥 2302 ккал\n💪 150г белка\n🥑 74г жиров\n🍚 258г углеводов",
            parse_mode="Markdown"
        )

    elif query.data == "lego":
        response = await ask_claude("Дай мне идеи комбинаций лего-заготовок на сегодня, исходя из моих норм и того что я уже съел.")
        await query.message.reply_text(response)

    elif query.data == "weekly_menu":
        await query.message.reply_text("⏳ Составляю меню на неделю...")
        response = await ask_claude("Составь мне меню питания на 7 дней с учётом системы лего-заготовок. Формат: завтрак/обед/ужин с КБЖУ каждого приёма. Укладывайся в мои нормы.")
        await query.message.reply_text(response)

    elif query.data == "shopping":
        await query.message.reply_text("⏳ Составляю список покупок...")
        response = await ask_claude("Составь список покупок в Ашане на неделю для лего-заготовок под мои цели. Укажи примерное количество каждого продукта.")
        await query.message.reply_text(response)

    elif query.data == "add_food":
        await query.message.reply_text(
            "Напиши что съел или пришли фото 📸\n\n"
            "Примеры:\n"
            "• «гречка 150г + куриное бедро 200г»\n"
            "• «творог 200г + банан»\n"
            "• [фото тарелки]"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Анализирую фото...")

    photo = update.message.photo[-1]  # highest resolution
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    image_base64 = base64.b64encode(photo_bytes).decode("utf-8")

    caption = update.message.caption or ""
    prompt = f"На фото еда. Определи что это, оцени порцию и посчитай КБЖУ. {caption}\n\nПосле анализа спроси: добавить это в дневник? (да/нет)"

    response = await ask_claude(prompt, image_base64=image_base64)
    await update.message.reply_text(response)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    # Quick commands
    if any(w in text for w in ["дневник", "статус", "итого", "сколько осталось"]):
        await update.message.reply_text(format_daily_summary(), parse_mode="Markdown")
        return

    # Check if user is confirming food addition
    if text in ["да", "добавь", "добавить", "ок", "окей", "yes"]:
        # Ask Claude what was the last food discussed
        response = await ask_claude(
            "Пользователь подтвердил добавление еды в дневник. "
            "Из нашего последнего обмена — добавь эту еду и скажи что добавлено с итоговым КБЖУ за день. "
            "Формат ответа: '✅ Добавлено: [название]\n[КБЖУ]\n\nСегодня итого: [ккал] ккал'"
        )
        await update.message.reply_text(response)
        return

    # Regular message → Claude
    prompt = update.message.text

    # If message looks like food description
    food_keywords = ["съел", "поел", "выпил", "перекусил", "завтрак", "обед", "ужин", "добавь"]
    if any(w in text for w in food_keywords):
        prompt += "\n\nЕсли это описание еды — посчитай КБЖУ и спроси добавить в дневник."

    response = await ask_claude(prompt)
    await update.message.reply_text(response)


async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_daily_summary(), parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = get_today()
    food_diary[today] = {"meals": [], "totals": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}}
    await update.message.reply_text("🔄 Дневник за сегодня сброшен.")


# ── Main ──────────────────────────────────────────────────────────────────────

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

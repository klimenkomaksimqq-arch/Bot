import re
import time
import pytchat
import requests
import urllib.parse as urlparse
import os
from multiprocessing import Process
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import telegram.error


# ================== ТВОИ ДАННЫЕ ==================
TELEGRAM_TOKEN = '7949569236:AAEtMeo9l43nZoJ6P10U1tCfZaIAYw4y38g'
# ================================================


# Ищем оба формата кодов
CODE_REGEX = re.compile(
    r'\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}\b|\b[A-Z0-9]{16}\b',
    re.IGNORECASE
)

user_processes = {}
user_video_ids = {}


# ---------------- КНОПКИ ----------------
def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Статус", callback_data="status"),
            InlineKeyboardButton("🛑 Стоп", callback_data="stop"),
            InlineKeyboardButton("ℹ️ Краткая инструкция по боту", callback_data="help")
        ],
        [
            InlineKeyboardButton("🎯 Ввести код", url="https://redeem.fconline.garena.in.th")
        ]
    ])


# ---------------- VIDEO_ID ----------------
def extract_video_id(url):
    parsed = urlparse.urlparse(url)

    # youtu.be/ID
    if 'youtu.be' in parsed.netloc:
        return parsed.path.lstrip('/')

    if 'youtube.com' in parsed.netloc:
        qs = urlparse.parse_qs(parsed.query)
        if 'v' in qs:
            return qs['v'][0]

        parts = parsed.path.strip('/').split('/')

        if 'live' in parts:
            return parts[-1]

        if 'shorts' in parts:
            return parts[-1]

        if 'embed' in parts:
            return parts[-1]

    return None


# ---------------- TELEGRAM ----------------
def send_to_telegram(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message})
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")


# ---------------- CODES ----------------
def normalize_code(code: str) -> str:
    return code.replace("-", "").upper()


def load_seen_codes(user_id):
    filename = f"seen_user_{user_id}.txt"
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return set(line.strip() for line in f)
    return set()


def save_seen_code(user_id, code):
    filename = f"seen_user_{user_id}.txt"
    with open(filename, 'a') as f:
        f.write(code + "\n")


# ---------------- CHAT TRACKING ----------------
def track_chat(bot_token, user_id, video_id):
    seen_codes = load_seen_codes(user_id)

    try:
        chat = pytchat.create(video_id=video_id)

        while chat.is_alive():
            for c in chat.get().sync_items():
                msg = c.message.upper()

                for raw_code in CODE_REGEX.findall(msg):
                    norm = normalize_code(raw_code)

                    if norm in seen_codes:
                        continue

                    seen_codes.add(norm)
                    save_seen_code(user_id, norm)

                    # Красивый формат
                    pretty = "-".join(norm[i:i+4] for i in range(0, 16, 4))

                    # В ТГ — ТОЛЬКО КОД
                    send_to_telegram(bot_token, user_id, pretty)

            time.sleep(3)

    except Exception as e:
        print(f"❌ Ошибка в процессе пользователя {user_id}: {e}")
    finally:
        send_to_telegram(bot_token, user_id, "🛑 Трансляция завершена. Отслеживание остановлено.")
        user_processes.pop(user_id, None)
        user_video_ids.pop(user_id, None)


# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь мне ссылку на YouTube-трансляцию, и я буду присылать тебе новые коды из чата.",
        reply_markup=get_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Помощь:\n"
        "1. Отправь ссылку на YouTube-трансляцию\n"
        "2. Бот ищет коды в чате\n"
        "3. Каждый код приходит только один раз",
        reply_markup=get_keyboard()
    )


async def stop_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    process = user_processes.get(user_id)

    if process and process.is_alive():
        process.terminate()
        user_processes.pop(user_id)
        user_video_ids.pop(user_id, None)
        await update.message.reply_text("🛑 Отслеживание остановлено.", reply_markup=get_keyboard())
    else:
        await update.message.reply_text("ℹ️ Отслеживание не было запущено.", reply_markup=get_keyboard())


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    process = user_processes.get(user_id)

    if process and process.is_alive():
        video_id = user_video_ids.get(user_id)
        await update.message.reply_text(
            f"📡 Текущая трансляция:\nhttps://youtu.be/{video_id}",
            reply_markup=get_keyboard()
        )
    else:
        await update.message.reply_text("ℹ️ Отслеживание не запущено.", reply_markup=get_keyboard())


# ---------------- MESSAGE ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text.strip()

    # ЛОГ В КОНСОЛЬ (как раньше)
    user = update.message.from_user
    username = user.username or "-"
    name = user.first_name or "-"
    print(f"📥 Пользователь: {name} (@{username}, ID: {user_id}) прислал сообщение: {text}")

    match = re.search(
        r'(https?://(?:www\.|m\.)?youtube\.com/[^\s]+|https?://youtu\.be/[^\s]+)',
        text
    )

    if not match:
        await update.message.reply_text("Пожалуйста, отправь ссылку на YouTube.", reply_markup=get_keyboard())
        return

    video_id = extract_video_id(match.group(1))

    if not video_id:
        await update.message.reply_text("Не удалось распознать ссылку.", reply_markup=get_keyboard())
        return

    if user_id in user_processes and user_processes[user_id].is_alive():
        await update.message.reply_text("Я уже отслеживаю трансляцию для тебя.", reply_markup=get_keyboard())
        return

    user_video_ids[user_id] = video_id
    process = Process(target=track_chat, args=(TELEGRAM_TOKEN, user_id, video_id), daemon=True)
    user_processes[user_id] = process
    process.start()

    await update.message.reply_text(
        f"🚀 Начинаю отслеживание:\nhttps://youtu.be/{video_id}",
        reply_markup=get_keyboard()
    )


# ---------------- BUTTONS ----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data == 'stop':
            await stop_tracking(query, context)
        elif query.data == 'status':
            await status(query, context)
        elif query.data == 'help':
            await help_command(query, context)
    except telegram.error.BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop_tracking))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Бот запущен. Ожидаю сообщений...")
    app.run_polling()


if __name__ == "__main__":
    main()

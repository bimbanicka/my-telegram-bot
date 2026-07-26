import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ====== Настройки ======
BOT_TOKEN = "8996114840:AAE-e6c1sXFPnIGUxK1WHxyDvkkzkotAfYU"
INTERVAL_DAYS = 500
DATA_FILE = Path("user_data.json")

router = Router()
scheduler = AsyncIOScheduler()


def load_user_data() -> dict[str, str]:
    """Загружает сохранённые главные даты пользователей."""
    if not DATA_FILE.exists():
        try:
            DATA_FILE.write_text("{}", encoding="utf-8")
        except OSError as error:
            logging.error("Не удалось создать файл данных: %s", error)
        return {}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as error:
        logging.error("Ошибка чтения %s: %s", DATA_FILE, error)
        return {}


def save_user_data(data: dict[str, str]) -> bool:
    """Сохраняет главные даты пользователей."""
    try:
        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return True
    except OSError as error:
        logging.error("Ошибка записи %s: %s", DATA_FILE, error)
        return False


def parse_date(date_text: str) -> date | None:
    """Проверяет строгий формат ДД.ММ.ГГГГ."""
    try:
        parsed = datetime.strptime(date_text, "%d.%m.%Y").date()
        return parsed if parsed.strftime("%d.%m.%Y") == date_text else None
    except ValueError:
        return None


def calculate_statistics(selected_date: date) -> tuple[int, int, int]:
    now = datetime.now()
    start_datetime = datetime.combine(selected_date, datetime.min.time())
    passed_days = (now.date() - selected_date).days
    passed_seconds = int((now - start_datetime).total_seconds())
    remainder = passed_days % INTERVAL_DAYS
    days_to_anniversary = INTERVAL_DAYS if remainder == 0 else INTERVAL_DAYS - remainder
    return passed_days, passed_seconds, days_to_anniversary


@router.message(CommandStart())
async def start_command(message: Message) -> None:
    await message.answer(
        "Привет! 👋\n\n"
        "Я считаю, сколько дней и секунд прошло с указанной даты, "
        "а также напоминаю о юбилеях каждые 500 дней.\n\n"
        "Отправьте дату в формате: <b>ДД.ММ.ГГГГ</b>\n"
        "Например: <code>05.07.2013</code>"
    )


@router.message(F.text)
async def process_date(message: Message) -> None:
    date_text = message.text.strip()
    selected_date = parse_date(date_text)
    if selected_date is None:
        await message.answer(
            "❌ Не удалось распознать дату.\n\n"
            "Введите её строго в формате <b>ДД.ММ.ГГГГ</b>.\n"
            "Например: <code>05.07.2013</code>"
        )
        return
    if selected_date > date.today():
        await message.answer("❌ Эта дата находится в будущем. Введите дату из прошлого.")
        return

    passed_days, passed_seconds, days_to_anniversary = calculate_statistics(selected_date)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📌 Сделать этой датой главный отсчёт",
            callback_data=f"set_main_date:{date_text}",
        )
    ]])
    await message.answer(
        f"📅 Дата: <b>{date_text}</b>\n\n"
        f"🗓 Прошло дней: <b>{passed_days}</b>\n"
        f"⏱ Прошло секунд: <b>{passed_seconds:,}</b>\n"
        f"🎯 До ближайшего юбилея ({INTERVAL_DAYS} дней): <b>{days_to_anniversary}</b> дней",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("set_main_date:"))
async def set_main_date(callback: CallbackQuery) -> None:
    """Сохраняет выбранную дату как главную для пользователя."""
    if callback.from_user is None or callback.data is None:
        return
    date_text = callback.data.split(":", maxsplit=1)[1]
    user_data = load_user_data()
    user_data[str(callback.from_user.id)] = date_text
    if save_user_data(user_data):
        await callback.answer("Главная дата сохранена!")
        if callback.message:
            await callback.message.answer(
                f"✅ Дата <b>{date_text}</b> сохранена как ваш главный отсчёт.\n"
                f"Я поздравлю вас в каждый юбилей, кратный {INTERVAL_DAYS} дням."
            )
    else:
        await callback.answer("Не удалось сохранить дату.", show_alert=True)


async def check_anniversaries(bot: Bot) -> None:
    """В 09:00 проверяет юбилеи для всех сохранённых пользователей."""
    today = date.today()
    for user_id, date_text in load_user_data().items():
        main_date = parse_date(date_text)
        if main_date is None or main_date > today:
            continue
        passed_days = (today - main_date).days
        if passed_days > 0 and passed_days % INTERVAL_DAYS == 0:
            passed_seconds = passed_days * 24 * 60 * 60
            try:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "🎉 <b>ЮБИЛЕЙ!</b>\n\n"
                        f"С вашей главной даты прошло ровно <b>{passed_days}</b> дней "
                        f"(<b>{passed_seconds:,}</b> секунд)!"
                    ),
                )
            except Exception as error:
                logging.warning("Не удалось отправить сообщение %s: %s", user_id, error)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if BOT_TOKEN == "ВАШ_ТОКЕН_ОТ_BOTFATHER":
        raise ValueError("Укажите настоящий BOT_TOKEN в начале файла.")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    # Проверка каждый день в 09:00 по времени компьютера/сервера.
    scheduler.add_job(
        check_anniversaries,
        trigger="cron",
        hour=9,
        minute=0,
        args=[bot],
        id="anniversary_check",
        replace_existing=True,
    )
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

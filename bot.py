import os
import asyncio
import json
import logging
import uuid
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler


BOT_TOKEN = os.getenv("BOT_TOKEN", "8996114840:AAHU2Q7jhz2K4LnhU8UzK6knD1TFS9GB7D8")
INTERVAL_DAYS = 500
DATA_FILE = Path("user_data.json")

router = Router()
scheduler = AsyncIOScheduler()


class Form(StatesGroup):
    waiting_birthday = State()
    waiting_date_name = State()
    waiting_date_value = State()


def load_data() -> dict:
    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}", encoding="utf-8")
        return {}

    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))

        if not isinstance(raw, dict):
            return {}

        # Підтримка старого формату:
        # {"123456": "05.07.2013"}
        for user_id, value in list(raw.items()):
            if isinstance(value, str):
                raw[user_id] = {"main_date": value}

        return raw
    except (OSError, json.JSONDecodeError):
        return {}


def save_data(data: dict) -> bool:
    try:
        DATA_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def get_profile(data: dict, user_id: int) -> dict:
    profile = data.setdefault(str(user_id), {})

    if not isinstance(profile, dict):
        profile = {}
        data[str(user_id)] = profile

    profile.setdefault("important_dates", [])
    profile.setdefault("notification_hour", 9)

    return profile


def parse_date(text: str) -> date | None:
    try:
        parsed = datetime.strptime(text, "%d.%m.%Y").date()

        if parsed.strftime("%d.%m.%Y") != text:
            return None

        return parsed
    except ValueError:
        return None


def safe_date(year: int, month: int, day: int) -> date:
    """29 лютого в невисокосний рік вважаємо 28 лютого."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def days_to_next_birthday(birthday: date, today: date) -> int:
    next_birthday = safe_date(today.year, birthday.month, birthday.day)

    if next_birthday < today:
        next_birthday = safe_date(today.year + 1, birthday.month, birthday.day)

    return (next_birthday - today).days


def age_statistics(birthday: date, today: date) -> tuple[int, int, int, int]:
    """Роки, місяці, дні та секунди від дня народження."""
    years = today.year - birthday.year

    if today < safe_date(today.year, birthday.month, birthday.day):
        years -= 1

    after_years = safe_date(
        birthday.year + years,
        birthday.month,
        birthday.day,
    )

    months = (today.year - after_years.year) * 12 + today.month - after_years.month

    if today.day < after_years.day:
        months -= 1

    total_month = after_years.month - 1 + months

    after_months = safe_date(
        after_years.year + total_month // 12,
        total_month % 12 + 1,
        after_years.day,
    )

    days = (today - after_months).days

    seconds = int(
        (
            datetime.now()
            - datetime.combine(birthday, datetime.min.time())
        ).total_seconds()
    )

    return years, months, days, seconds


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎂 Вказати день народження",
                    callback_data="birthday_ask",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎈 Скільки до дня народження?",
                    callback_data="birthday_countdown",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Додати важливу дату",
                    callback_data="important_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Час сповіщень",
                    callback_data="notification_settings",
                )
            ],
        ]
    )


def notification_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="09:00",
                    callback_data="notification_hour:9",
                ),
                InlineKeyboardButton(
                    text="18:00",
                    callback_data="notification_hour:18",
                ),
            ]
        ]
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Вітаю! 👋\n\n"
        "Надішліть мені дату у форматі <b>ДД.ММ.РРРР</b> — "
        "я порахую, скільки часу минуло від неї.\n\n"
        "<b>Команди:</b>\n"
        "/my_dates — мої збережені дати\n"
        "/birthday — вказати день народження\n"
        "/add_date — додати важливу дату\n"
        "/delete_birthday — видалити день народження\n"
        "/delete_main_date — видалити головну дату\n"
        "/delete_date — видалити важливу дату\n"
        "/settings — час сповіщень\n"
        "/cancel — скасувати введення",
        reply_markup=main_keyboard(),
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.")


# ====== День народження ======

@router.message(Command("birthday"))
async def birthday_command(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.waiting_birthday)

    await message.answer(
        "🎂 Напишіть дату народження у форматі <b>ДД.ММ.РРРР</b>.\n"
        "Наприклад: <code>05.07.2013</code>"
    )


@router.callback_query(F.data == "birthday_ask")
async def birthday_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.waiting_birthday)
    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "🎂 Напишіть дату народження у форматі <b>ДД.ММ.РРРР</b>."
        )


@router.message(Form.waiting_birthday, F.text)
async def save_birthday(message: Message, state: FSMContext) -> None:
    birthday_text = message.text.strip()
    birthday = parse_date(birthday_text)

    if birthday is None or birthday > date.today():
        await message.answer(
            "❌ Введіть коректну дату з минулого у форматі <b>ДД.ММ.РРРР</b>."
        )
        return

    data = load_data()
    profile = get_profile(data, message.from_user.id)
    profile["birthday"] = birthday_text

    save_data(data)
    await state.clear()

    await message.answer(
        f"✅ День народження <b>{birthday_text}</b> збережено!\n"
        "Я привітаю вас саме у цей день."
    )


@router.callback_query(F.data == "birthday_countdown")
async def birthday_countdown(callback: CallbackQuery) -> None:
    data = load_data()
    profile = get_profile(data, callback.from_user.id)
    birthday_text = profile.get("birthday")

    await callback.answer()

    if not birthday_text:
        if callback.message:
            await callback.message.answer(
                "Спочатку вкажіть день народження кнопкою «🎂 Вказати день народження»."
            )
        return

    birthday = parse_date(birthday_text)

    if birthday is None:
        return

    days_left = days_to_next_birthday(birthday, date.today())
    years, months, days, seconds = age_statistics(birthday, date.today())

    if callback.message:
        await callback.message.answer(
            f"🎈 До вашого дня народження: <b>{days_left}</b> днів.\n\n"
            f"Ваш вік: <b>{years}</b> р., <b>{months}</b> міс., "
            f"<b>{days}</b> дн.\n"
            f"Від дати народження минуло <b>{seconds:,}</b> секунд."
        )


@router.message(Command("delete_birthday"))
async def delete_birthday(message: Message) -> None:
    data = load_data()
    profile = get_profile(data, message.from_user.id)

    if profile.pop("birthday", None):
        save_data(data)
        await message.answer("✅ День народження видалено.")
    else:
        await message.answer("День народження ще не збережений.")


# ====== Головна дата ======

@router.message(Command("delete_main_date"))
async def delete_main_date(message: Message) -> None:
    data = load_data()
    profile = get_profile(data, message.from_user.id)

    if profile.pop("main_date", None):
        save_data(data)
        await message.answer("✅ Головну дату видалено.")
    else:
        await message.answer("Головна дата ще не збережена.")


@router.callback_query(F.data.startswith("set_main:"))
async def set_main_date(callback: CallbackQuery) -> None:
    if callback.data is None:
        return

    date_text = callback.data.split(":", maxsplit=1)[1]

    data = load_data()
    profile = get_profile(data, callback.from_user.id)
    profile["main_date"] = date_text

    save_data(data)
    await callback.answer("Головну дату збережено!")

    if callback.message:
        await callback.message.answer(
            f"✅ Дату <b>{date_text}</b> збережено як головний відлік."
        )


# ====== Важливі дати ======

@router.message(Command("add_date"))
async def add_date(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.waiting_date_name)

    await message.answer(
        "➕ Напишіть назву дати.\n"
        "Наприклад: <i>Наша річниця</i>, <i>Мій проєкт</i> або "
        "<i>День без куріння</i>."
    )


@router.callback_query(F.data == "important_add")
async def important_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.waiting_date_name)
    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "➕ Напишіть назву дати.\n"
            "Наприклад: <i>Наша річниця</i>."
        )


@router.message(Form.waiting_date_name, F.text)
async def save_date_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()

    if not name or len(name) > 50:
        await message.answer("Назва має містити від 1 до 50 символів.")
        return

    await state.update_data(date_name=name)
    await state.set_state(Form.waiting_date_value)

    await message.answer(
        "Тепер введіть дату у форматі <b>ДД.ММ.РРРР</b>."
    )


@router.message(Form.waiting_date_value, F.text)
async def save_important_date(message: Message, state: FSMContext) -> None:
    date_text = message.text.strip()
    selected_date = parse_date(date_text)

    if selected_date is None or selected_date > date.today():
        await message.answer(
            "❌ Введіть коректну дату з минулого у форматі <b>ДД.ММ.РРРР</b>."
        )
        return

    form_data = await state.get_data()

    data = load_data()
    profile = get_profile(data, message.from_user.id)

    profile["important_dates"].append(
        {
            "id": uuid.uuid4().hex[:8],
            "name": form_data["date_name"],
            "date": date_text,
        }
    )

    save_data(data)
    await state.clear()

    await message.answer(
        f"✅ Дату «<b>{form_data['date_name']}</b>» збережено: {date_text}."
    )


@router.message(Command("delete_date"))
async def delete_date_menu(message: Message) -> None:
    data = load_data()
    profile = get_profile(data, message.from_user.id)
    important_dates = profile.get("important_dates", [])

    if not important_dates:
        await message.answer("Важливих дат для видалення немає.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🗑 {item.get('name', 'Без назви')}",
                    callback_data=f"delete_important:{item['id']}",
                )
            ]
            for item in important_dates
        ]
    )

    await message.answer(
        "Оберіть дату, яку потрібно видалити:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("delete_important:"))
async def delete_important_date(callback: CallbackQuery) -> None:
    if callback.data is None:
        return

    item_id = callback.data.split(":", maxsplit=1)[1]

    data = load_data()
    profile = get_profile(data, callback.from_user.id)

    before = len(profile["important_dates"])

    profile["important_dates"] = [
        item
        for item in profile["important_dates"]
        if item.get("id") != item_id
    ]

    save_data(data)

    if len(profile["important_dates"]) < before:
        await callback.answer("Дату видалено!")
    else:
        await callback.answer("Дату не знайдено.")

    if callback.message:
        await callback.message.edit_text(
            "✅ Готово. Скористайтеся /my_dates, щоб переглянути список."
        )


# ====== Перегляд даних ======

@router.message(Command("my_dates"))
async def my_dates(message: Message) -> None:
    data = load_data()
    profile = get_profile(data, message.from_user.id)

    lines = [
        "📌 <b>Ваші збережені дати</b>",
        f"Головний відлік: <b>{profile.get('main_date', 'не вказано')}</b>",
        f"День народження: <b>{profile.get('birthday', 'не вказано')}</b>",
        f"Час сповіщень: <b>{profile.get('notification_hour', 9)}:00</b>",
    ]

    important_dates = profile.get("important_dates", [])

    if important_dates:
        lines.append("\n<b>Важливі дати:</b>")

        for item in important_dates:
            lines.append(
                f"• {item.get('name', 'Без назви')} — {item.get('date', '')}"
            )
    else:
        lines.append("\nВажливих дат ще немає.")

    await message.answer("\n".join(lines), reply_markup=main_keyboard())


# ====== Час сповіщень ======

@router.message(Command("settings"))
async def settings(message: Message) -> None:
    await message.answer(
        "Оберіть зручний час для сповіщень:",
        reply_markup=notification_keyboard(),
    )


@router.callback_query(F.data == "notification_settings")
async def notification_settings(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "Оберіть зручний час для сповіщень:",
            reply_markup=notification_keyboard(),
        )


@router.callback_query(F.data.startswith("notification_hour:"))
async def set_notification_hour(callback: CallbackQuery) -> None:
    if callback.data is None:
        return

    hour = int(callback.data.split(":", maxsplit=1)[1])

    data = load_data()
    profile = get_profile(data, callback.from_user.id)
    profile["notification_hour"] = hour

    save_data(data)
    await callback.answer("Час збережено!")

    if callback.message:
        await callback.message.edit_text(
            f"✅ Сповіщення приходитимуть о <b>{hour}:00</b>."
        )


# ====== Звичайна дата для підрахунку ======

@router.message(F.text)
async def process_main_date(message: Message) -> None:
    date_text = message.text.strip()
    selected_date = parse_date(date_text)

    if selected_date is None or selected_date > date.today():
        await message.answer(
            "❌ Введіть дату з минулого у форматі <b>ДД.ММ.РРРР</b>."
        )
        return

    now = datetime.now()

    passed_days = (now.date() - selected_date).days
    passed_seconds = int(
        (
            now
            - datetime.combine(selected_date, datetime.min.time())
        ).total_seconds()
    )

    remainder = passed_days % INTERVAL_DAYS
    days_left = INTERVAL_DAYS if remainder == 0 else INTERVAL_DAYS - remainder

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📌 Зробити цю дату головним відліком",
                    callback_data=f"set_main:{date_text}",
                )
            ]
        ]
    )

    await message.answer(
        f"📅 Дата: <b>{date_text}</b>\n\n"
        f"🗓 Минуло днів: <b>{passed_days}</b>\n"
        f"⏱ Минуло секунд: <b>{passed_seconds:,}</b>\n"
        f"🎯 До ювілею в {INTERVAL_DAYS} днів: "
        f"<b>{days_left}</b> днів.",
        reply_markup=keyboard,
    )


# ====== Щоденні сповіщення ======

async def send_daily_notifications(bot: Bot) -> None:
    """
    Запускається о 09:00 та 18:00.
    Користувач отримує повідомлення лише в обраний ним час.
    """
    today = date.today()
    current_hour = datetime.now().hour
    data = load_data()

    for user_id, profile in data.items():
        if profile.get("notification_hour", 9) != current_hour:
            continue

        try:
            # День народження та нагадування за 7, 3 і 1 день.
            birthday_text = profile.get("birthday")

            if birthday_text:
                birthday = parse_date(birthday_text)

                if birthday:
                    days_left = days_to_next_birthday(birthday, today)

                    if days_left == 0:
                        age = today.year - birthday.year

                        await bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                "🎂 <b>З Днем народження!</b>\n\n"
                                f"Сьогодні вам виповнюється <b>{age}</b>! "
                                "Бажаю щастя, здоров’я та здійснення мрій!"
                            ),
                        )

                    elif days_left in (7, 3, 1):
                        await bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                "🎈 Нагадування: до вашого дня народження "
                                f"залишилося <b>{days_left}</b> днів!"
                            ),
                        )

            # Головна дата та важливі дати.
            all_dates = []

            if profile.get("main_date"):
                all_dates.append(
                    ("Головний відлік", profile["main_date"])
                )

            for item in profile.get("important_dates", []):
                all_dates.append(
                    (
                        item.get("name", "Важлива дата"),
                        item.get("date", ""),
                    )
                )

            for name, date_text in all_dates:
                event_date = parse_date(date_text)

                if event_date is None:
                    continue

                passed_days = (today - event_date).days

                # Нагадування про 100, 365 і 1000 днів.
                if passed_days in (100, 365, 1000):
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"✨ <b>Важлива дата: {name}</b>\n"
                            f"Від {date_text} минуло рівно "
                            f"<b>{passed_days}</b> днів!"
                        ),
                    )

                # Ювілеї кожні 500 днів.
                elif passed_days > 0 and passed_days % INTERVAL_DAYS == 0:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            "🎉 <b>ЮВІЛЕЙ!</b>\n"
                            f"Від дати «{name}» минуло рівно "
                            f"<b>{passed_days}</b> днів!"
                        ),
                    )

        except Exception as error:
            logging.warning(
                "Не вдалося надіслати сповіщення користувачу %s: %s",
                user_id,
                error,
            )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if BOT_TOKEN == "ВСТАВТЕ_СЮДИ_НОВИЙ_ТОКЕН_ВІД_BOTFATHER":
        raise ValueError(
            "Вставте справжній токен від BotFather у змінну BOT_TOKEN."
        )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    scheduler.add_job(
        send_daily_notifications,
        trigger="cron",
        hour="9,18",
        minute=0,
        args=[bot],
    )

    scheduler.start()

    await dispatcher.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())

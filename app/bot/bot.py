import os
import logging


from aiogram.filters import CommandStart
from aiogram import Bot, types, Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder

from google_auth_oauthlib.flow import InstalledAppFlow
import secrets
import urllib.parse

from .handlers import get_upcoming_events, get_calendar_color, create_event_from_text, create_google_calendar_event, \
    check_token_health
from .init_bot import bot, dp
from app.settings import get_settings
from .keyboards import get_postpone_time_options_keyboard, get_main_keyboard

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')
USER_CREDENTIALS_DIR = "/service/user_credentials"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

user_router = Router()


LOCAL_TIMEZONE = pytz.timezone('Europe/Moscow')



class AuthState(StatesGroup):
    waiting_for_auth_code = State()

class EventCreation(StatesGroup):
    waiting_for_text = State()
    waiting_for_commit = State()



@user_router.message(CommandStart())
async def start_handler(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    keyboard = get_main_keyboard()
    await message.answer(
        "Привет! Я ваш помощник для Google Calendar.\n\n"
        "Команды:\n"
        "• /auth - Авторизовать бота\n"
        "• /events - Показать предстоящие события\n"
        "• /token_status - Проверить статус авторизации\n\n"
        "Используйте /auth для авторизации и /events для просмотра предстоящих событий.", 
        reply_markup=keyboard)
    logger.info(f"Отправлен ответ на команду /start пользователю {message.from_user.id}")



@user_router.message(F.text == '/auth')
async def auth_handler(message: Message, state: FSMContext):
    logger.info(f"Получена команда /auth от пользователя {message.from_user.id}")
    user_id = message.from_user.id
    
    # Удаляем старый токен, если он существует
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
            logger.info(f"Удален старый токен для пользователя {user_id}")
        except Exception as e:
            logger.warning(f"Не удалось удалить старый токен для пользователя {user_id}: {e}")
    
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, settings.scopes, redirect_uri=f"{settings.server_address}/callback"
    )
    auth_state = secrets.token_urlsafe(16)
    composite_state = f"{auth_state}|{user_id}"
    encoded_composite_state = urllib.parse.quote(composite_state)
    
    # Используем prompt='consent' для гарантированного получения refresh токена
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=encoded_composite_state,
        prompt='consent'  # Принудительно запрашиваем согласие для получения refresh токена
    )

    await state.set_state(AuthState.waiting_for_auth_code)
    await state.update_data(auth_state=auth_state, auth_flow=flow, user_id=user_id)

    # Create an inline keyboard with a link to the authorization URL
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Авторизовать Google Calendar",
        url=auth_url
    ))

    await message.answer(
        "Пожалуйста, авторизуйте доступ к вашему Google Calendar, перейдя по этой ссылке:\n\n"
        "⚠️ Важно: Убедитесь, что вы отметили 'Оставаться в системе' если будет предложено, "
        "и предоставьте все запрашиваемые разрешения для обеспечения непрерывного доступа.",
        reply_markup=builder.as_markup()
    )


@user_router.callback_query(F.data.startswith("show_postpone_times:"))
async def show_postpone_times(callback_query: types.CallbackQuery):
    """Handles the callback query to show postpone time options."""
    logger.info(f"Получен запрос на показ опций отложенного времени от пользователя {callback_query.from_user.id}")
    event_id = int(callback_query.data.split(":")[1])
    keyboard = get_postpone_time_options_keyboard(event_id)
    await callback_query.message.edit_reply_markup(reply_markup=keyboard)
    await callback_query.answer()

@user_router.callback_query(F.data.startswith("cancel_postpone:"))
async def cancel_postpone(callback_query: types.CallbackQuery):
    """Handles the callback query to cancel postponing."""
    logger.info(f"Получен запрос на отмену отложенного времени от пользователя {callback_query.from_user.id}")
    event_id = int(callback_query.data.split(":")[1])
    await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
    await callback_query.answer("Отложено отменено.")

@user_router.callback_query(F.data.startswith("postpone:"))
async def postpone_reminder(callback_query: types.CallbackQuery):
    pass
    # """Handles the callback query for postponing a reminder."""
    # user_id = callback_query.from_user.id
    # data = callback_query.data.split(":")
    # event_id = int(data[1])
    # postpone_minutes = int(data[2])
    #
    # # Get event details (replace with your database lookup)
    # # event_summary = "Your event summary"  # Fetch from db
    # # event_start_time = datetime.datetime.now() # Fetch from db
    #
    # # Calculate new reminder time
    # new_reminder_time = datetime.datetime.now(LOCAL_TIMEZONE) + datetime.timedelta(minutes=postpone_minutes)
    #
    # # Add a new job to the scheduler
    # scheduler.add_job(
    #     send_reminder,
    #     'date',
    #     run_date=new_reminder_time,
    #     args=[bot, user_id, "заменить event_summary"]  # pass replace event_summary here
    # )
    #
    # await bot.answer_callback_query(callback_query.id, text=f"Напоминание отложено на {postpone_minutes} минут!")
    # await callback_query.message.edit_text(
    #     text=f"Напоминание отложено на {postpone_minutes} минут!",
    # )

@user_router.callback_query(F.data == "reauth")
async def reauthorize_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Handles the re-authorization callback."""
    logger.info(f"Получен запрос на повторную авторизацию от пользователя {callback_query.from_user.id}")
    user_id = callback_query.from_user.id
    
    # Удаляем старый токен, если он существует
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
            logger.info(f"Удален старый токен для пользователя {user_id} при повторной авторизации")
        except Exception as e:
            logger.warning(f"Не удалось удалить старый токен для пользователя {user_id}: {e}")
    
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, settings.scopes, redirect_uri=f"{settings.server_address}/callback"
    )
    auth_state = secrets.token_urlsafe(16)
    composite_state = f"{auth_state}|{user_id}"
    encoded_composite_state = urllib.parse.quote(composite_state)
    
    # Используем prompt='consent' для гарантированного получения refresh токена
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=encoded_composite_state,
        prompt='consent'  # Принудительно запрашиваем согласие для получения refresh токена
    )

    await state.set_state(AuthState.waiting_for_auth_code)
    await state.update_data(auth_state=auth_state, auth_flow=flow, user_id=user_id)

    # Create an inline keyboard with a link to the authorization URL
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Авторизовать Google Calendar",
        url=auth_url
    ))

    await callback_query.message.answer(
        "Пожалуйста, повторно авторизуйте доступ к вашему Google Calendar, перейдя по этой ссылке:\n\n"
        "⚠️ Важно: Убедитесь, что вы отметили 'Оставаться в системе' если будет предложено, "
        "и предоставьте все запрашиваемые разрешения для обеспечения непрерывного доступа.",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()

@user_router.message(F.text == '/events')
async def events_handler(message: Message):
    logger.info(f"Получена команда /events от пользователя {message.from_user.id}")
    user_id = message.from_user.id
    events = await get_upcoming_events(user_id)

    if isinstance(events, tuple):  # Check if events is tuple(str,InlineKeyboardMarkup)
        logger.warning(f"Получена ошибка авторизации для пользователя {user_id}")
        await message.answer(events[0], reply_markup=events[1])  # Send the error message with the keyboard
        return

    if not events:
        logger.info(f"События не найдены для пользователя {user_id}")
        await message.answer("Предстоящих событий не найдено.")
        return

    logger.info(f"Найдено {len(events)} событий для пользователя {user_id}")
    formatted_events = []
    for calendar_name, event_summary, event_start_time_str in events:
        color = await get_calendar_color(calendar_name)
        formatted_events.append(
            f"{color} <b>{calendar_name}:</b> {event_summary} - {event_start_time_str}\n"
        )

    await message.answer(
        "\n".join(formatted_events),
        parse_mode="HTML"
    )
    logger.info(f"Отправлен список событий пользователю {user_id}")


@user_router.message(F.text == "Создать событие")
async def create_event_handler(message: types.Message, state: FSMContext): # Corrected argument type
    """Handles the message for the 'Create Event' button."""
    logger.info(f"Получен запрос на создание события от пользователя {message.from_user.id}")
    await message.answer( # Corrected to message.answer
        "Пожалуйста, введите детали события:",
    )
    await state.set_state(EventCreation.waiting_for_text)
    logger.info(f"Установлено состояние ожидания текста для пользователя {message.from_user.id}")
    # No callback_query.answer needed

events_memory = {}
@user_router.message(EventCreation.waiting_for_text)
async def process_event_details(message: types.Message, state: FSMContext):
    """Processes the event details entered by the user."""
    logger.info(f"Получены детали события от пользователя {message.from_user.id}: {message.text[:50]}...")
    user_id = str(message.from_user.id)  # Получаем ID пользователя
    user_text = message.text

    try:
        # Вызываем функцию для создания события из текста
        result = await create_event_from_text(user_id, user_text)
        logger.info(f"Событие обработано для пользователя {user_id}")

        # Create inline keyboard for confirmation
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="confirm_event")],
            [InlineKeyboardButton(text="Отклонить", callback_data="reject_event")]
        ])

        # Отправляем пользователю предварительный просмотр события с кнопками
        await message.answer(
            f"Предварительный просмотр события:\n{result.calendar_name} "
            f"\n{result.event_summary}\n{result.date}\n{result.start_time}\n{result.end_time}",
            reply_markup=keyboard
        )
        logger.info(f"Отправлен предварительный просмотр события пользователю {user_id}")

        events_memory[user_id] = result

        await state.set_state(EventCreation.waiting_for_commit)
        logger.info(f"Установлено состояние ожидания подтверждения для пользователя {user_id}")
        # # Отправляем ответ пользователю
        # await message.reply(result)
        event_data = await state.get_data()
        print(event_data)

    except Exception as e:
        logger.exception("An error occurred while processing event details")
        await message.reply("Извините, произошла ошибка. Пожалуйста, попробуйте снова.")

    finally:
        # Сбрасываем состояние
        await state.clear()
        logger.info(f"Состояние сброшено для пользователя {user_id}")

@user_router.callback_query(F.data == "confirm_event")
async def confirm_event_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Confirms the event and saves it."""
    logger.info(f"Получено подтверждение события от пользователя {callback_query.from_user.id}")

    user_id = str(callback_query.from_user.id)
    event = events_memory.get(user_id)
    print(event)
    if event is None:
        logger.error(f"Данные события не найдены для пользователя {user_id}")
        await callback_query.answer("Ошибка: Данные события не найдены.")
        await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
        await state.clear()
        return

    user_id = str(callback_query.from_user.id)

    success = await create_google_calendar_event(
        user_id,
        event.event_summary,
        event.event_description,
        event.start_time,
        event.end_time,
        event.calendar_id
    )
    await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
    if success:
        logger.info(f"Событие успешно создано для пользователя {user_id}")
        await callback_query.message.answer(f"Событие создано в календаре {event.calendar_name}.")
    else:
        logger.error(f"Ошибка при создании события для пользователя {user_id}")
        await callback_query.answer("Извините, произошла ошибка при создании события.")
    await state.clear()

@user_router.callback_query(F.data == "reject_event")
async def reject_event_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Rejects the event and resets the state."""
    logger.info(f"Получен отказ от события от пользователя {callback_query.from_user.id}")
    await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
    await callback_query.message.answer("Событие отклонено.")

    await state.clear()









async def start_bot():
    try:
        logger.info("Бот запускается...")
        await bot.send_message(settings.admin_id, f'Бот запущен')
        logger.info("Бот успешно запущен")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")


async def stop_bot():
    try:
        logger.info("Бот останавливается...")
        await bot.send_message(settings.admin_id, f'Бот остановлен')
        logger.info("Бот успешно остановлен")
    except Exception as e:
        logger.error(f"Ошибка при остановке бота: {e}")


@user_router.message(F.text == '/token_status')
async def token_status_handler(message: Message):
    """Handles the token status check command."""
    logger.info(f"Получена команда /token_status от пользователя {message.from_user.id}")
    user_id = message.from_user.id
    status, message_text = await check_token_health(user_id)
    
    status_emoji = {
        "healthy": "✅",
        "expiring_soon": "⚠️",
        "no_token": "❌",
        "no_refresh": "❌",
        "refresh_failed": "❌",
        "refreshed": "🔄"
    }
    
    emoji = status_emoji.get(status, "❓")
    
    await message.answer(
        f"{emoji} Статус токена: {status}\n\n"
        f"Детали: {message_text}\n\n"
        f"Если у вас возникают проблемы, используйте /auth для повторной авторизации."
    )
    logger.info(f"Отправлен статус токена пользователю {user_id}: {status}")


@user_router.message()
async def handle_all_messages(message: Message):
    """Обработчик для всех сообщений (для отладки)"""
    logger.info(f"Получено сообщение от пользователя {message.from_user.id}: {message.text}")
    # Не отвечаем на сообщение, просто логируем

@user_router.callback_query()
async def handle_all_callbacks(callback_query: types.CallbackQuery):
    """Обработчик для всех callback запросов (для отладки)"""
    logger.info(f"Получен callback от пользователя {callback_query.from_user.id}: {callback_query.data}")
    # Не отвечаем на callback, просто логируем


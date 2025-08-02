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

from .handlers import get_upcoming_events, get_calendar_color, create_event_from_text, create_google_calendar_event, check_token_health
from .init_bot import bot, dp
from app.settings import get_settings
from .keyboards import get_postpone_time_options_keyboard, get_main_keyboard

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')
USER_CREDENTIALS_DIR = "/service/user_credentials"


logging.basicConfig(level=logging.ERROR)
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
    keyboard = get_main_keyboard()
    await message.answer(
        "Hello! I'm your Google Calendar assistant.\n\n"
        "Commands:\n"
        "• /auth - Authorize the bot\n"
        "• /events - Show upcoming events\n"
        "• /token_status - Check authentication status\n\n"
        "Use /auth to authorize me and /events to see your upcoming events.", 
        reply_markup=keyboard)



@user_router.message(F.text == '/auth')
async def auth_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Удаляем старый токен, если он существует
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
            logger.info(f"Removed old token for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to remove old token for user {user_id}: {e}")
    
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
        text="Authorize Google Calendar",
        url=auth_url
    ))

    await message.answer(
        "Please authorize access to your Google Calendar by visiting this URL:\n\n"
        "⚠️ Important: Make sure to check 'Keep me signed in' if prompted, "
        "and grant all requested permissions to ensure continuous access.",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith("show_postpone_times:"))
async def show_postpone_times(callback_query: types.CallbackQuery):
    """Handles the callback query to show postpone time options."""
    event_id = int(callback_query.data.split(":")[1])
    keyboard = get_postpone_time_options_keyboard(event_id)
    await callback_query.message.edit_reply_markup(reply_markup=keyboard)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("cancel_postpone:"))
async def cancel_postpone(callback_query: types.CallbackQuery):
    """Handles the callback query to cancel postponing."""
    event_id = int(callback_query.data.split(":")[1])
    await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
    await callback_query.answer("Postponing cancelled.")

@dp.callback_query(F.data.startswith("postpone:"))
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
    # await bot.answer_callback_query(callback_query.id, text=f"Reminder postponed by {postpone_minutes} minutes!")
    # await callback_query.message.edit_text(
    #     text=f"Reminder postponed by {postpone_minutes} minutes!",
    # )

@dp.callback_query(F.data == "reauth")
async def reauthorize_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Handles the re-authorization callback."""
    user_id = callback_query.from_user.id
    
    # Удаляем старый токен, если он существует
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
            logger.info(f"Removed old token for user {user_id} during re-auth")
        except Exception as e:
            logger.warning(f"Failed to remove old token for user {user_id}: {e}")
    
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
        text="Authorize Google Calendar",
        url=auth_url
    ))

    await callback_query.message.answer(
        "Please re-authorize access to your Google Calendar by visiting this URL:\n\n"
        "⚠️ Important: Make sure to check 'Keep me signed in' if prompted, "
        "and grant all requested permissions to ensure continuous access.",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()

@user_router.message(F.text == '/events')
async def events_handler(message: Message):
    user_id = message.from_user.id
    events = await get_upcoming_events(user_id)

    if isinstance(events, tuple):  # Check if events is tuple(str,InlineKeyboardMarkup)
        await message.answer(events[0], reply_markup=events[1])  # Send the error message with the keyboard
        return


    if not events:
        await message.answer("No upcoming events found.")
        return

    formatted_events = []
    for calendar_name, event_summary, event_start_time_str in events:
        color = get_calendar_color(calendar_name)
        formatted_events.append(
            f"{color} <b>{calendar_name}:</b> {event_summary} - {event_start_time_str}\n"
        )

    await message.answer(
        "\n".join(formatted_events),
        parse_mode="HTML"
    )


@dp.message(F.text == "Создать событие")
async def create_event_handler(message: types.Message, state: FSMContext): # Corrected argument type
    """Handles the message for the 'Create Event' button."""
    await message.answer( # Corrected to message.answer
        "Please enter the event details:",
    )
    await state.set_state(EventCreation.waiting_for_text)
    # No callback_query.answer needed

events_memory = {}
@dp.message(EventCreation.waiting_for_text)
async def process_event_details(message: types.Message, state: FSMContext):
    """Processes the event details entered by the user."""
    user_id = str(message.from_user.id)  # Получаем ID пользователя
    user_text = message.text

    try:
        # Вызываем функцию для создания события из текста
        result = await create_event_from_text(user_id, user_text)

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

        events_memory[user_id] = result

        await state.set_state(EventCreation.waiting_for_commit)
        # # Отправляем ответ пользователю
        # await message.reply(result)
        event_data = await state.get_data()
        print(event_data)

    except Exception as e:
        logger.exception("An error occurred while processing event details")
        await message.reply("Sorry, an error occurred. Please try again.")

    finally:
        # Сбрасываем состояние
        await state.clear()

@dp.callback_query(F.data == "confirm_event")
async def confirm_event_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Confirms the event and saves it."""

    user_id = str(callback_query.from_user.id)
    event = events_memory.get(user_id)
    print(event)
    if event is None:
        await callback_query.answer("Error: Event data not found.")
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
        await callback_query.message.answer(f"Event created in {event.calendar_name} calendar.")
    else:
        await callback_query.answer("Sorry, there was an error creating the event.")
    await state.clear()

@dp.callback_query(F.data == "reject_event")
async def reject_event_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Rejects the event and resets the state."""
    await callback_query.message.edit_reply_markup(reply_markup=None)  # Remove the keyboard
    await callback_query.message.answer("Event rejected.")

    await state.clear()









async def start_bot():
    try:
        await bot.send_message(settings.admin_id, f'Start bot')
    except:
        pass


async def stop_bot():
    try:
        await bot.send_message(settings.admin_id, f'Stop bot')
    except:
        pass


@user_router.message(F.text == '/token_status')
async def token_status_handler(message: Message):
    """Handles the token status check command."""
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
        f"{emoji} Token Status: {status}\n\n"
        f"Details: {message_text}\n\n"
        f"If you're experiencing issues, use /auth to re-authorize."
    )


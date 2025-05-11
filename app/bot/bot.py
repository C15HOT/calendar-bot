import os
import logging


from aiogram.filters import CommandStart
from aiogram import Bot, types, Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder

from google_auth_oauthlib.flow import InstalledAppFlow
import secrets
import urllib.parse

from .handlers import get_upcoming_events, get_calendar_color
from .init_bot import bot, dp
from app.settings import get_settings
from .keyboards import get_postpone_time_options_keyboard

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')
USER_CREDENTIALS_DIR = "/service/user_credentials"


logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

settings = get_settings()

user_router = Router()


LOCAL_TIMEZONE = pytz.timezone('Europe/Moscow')



class AuthState(StatesGroup):
    waiting_for_auth_code = State()

@user_router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Hello! I'm your Google Calendar assistant. Use /auth to authorize me and /events to see your upcoming events.")

@user_router.message(F.text == '/auth')
async def auth_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, settings.scopes, redirect_uri=f"{settings.server_address}/callback"
    )
    auth_state = secrets.token_urlsafe(16)
    composite_state = f"{auth_state}|{user_id}"
    encoded_composite_state = urllib.parse.quote(composite_state)
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=encoded_composite_state,
        prompt='consent'
    )
    # auth_url, auth_state = flow.authorization_url(
    #     access_type='offline',
    #     include_granted_scopes='true'
    # )

    await state.set_state(AuthState.waiting_for_auth_code)  # Set the state
    await state.update_data(auth_state=auth_state, auth_flow=flow, user_id=user_id)

    # Create an inline keyboard with a link to the authorization URL
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Authorize Google Calendar",
        url=auth_url
    ))

    await message.answer(
        "Please authorize access to your Google Calendar by visiting this URL:",
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
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, settings.scopes, redirect_uri=f"{settings.server_address}/callback"
    )
    auth_state = secrets.token_urlsafe(16)
    composite_state = f"{auth_state}|{user_id}"
    encoded_composite_state = urllib.parse.quote(composite_state)
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=encoded_composite_state,
        prompt='consent'
    )

    await state.set_state(AuthState.waiting_for_auth_code)  # Set the state
    await state.update_data(auth_state=auth_state, auth_flow=flow, user_id=user_id)

    # Create an inline keyboard with a link to the authorization URL
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Authorize Google Calendar",
        url=auth_url
    ))

    await callback_query.message.answer(
        "Please authorize access to your Google Calendar by visiting this URL:",
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


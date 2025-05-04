import os
import logging
import datetime
from aiogram.filters import CommandStart
from aiogram import Bot, types, Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
import secrets
import urllib.parse
from .init_bot import bot
# Google Calendar API Scope
from app.settings import get_settings

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')  # Path to the downloaded credentials.json file
USER_CREDENTIALS_DIR = "/service/user_credentials"

# Настройки Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

user_router = Router()

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
        CREDENTIALS_FILE, SCOPES, redirect_uri=f"{settings.server_address}/callback"
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

@user_router.message(F.text == '/events')
async def events_handler(message: Message):
    user_id = message.from_user.id
    events_text = await get_upcoming_events(user_id)
    await message.answer(events_text)






def get_calendar_service(user_id):
    creds = None
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Failed to refresh token for user {user_id}: {e}")
                return None
        else:
            return None  # Token needs to be created

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except HttpError as error:
        logger.error(f"An error occurred: {error}")
        return None


# Функция для сохранения учетных данных пользователя (OAuth2 flow)
async def save_credentials(user_id, credentials):
    os.makedirs(USER_CREDENTIALS_DIR, exist_ok=True)
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    with open(token_path, 'w') as token:
        token.write(credentials.to_json())
    logger.info(f"Credentials saved for user {user_id} to {token_path}")



async def get_upcoming_events(user_id, num_events=5):
    service = get_calendar_service(user_id)
    if not service:
        return "Please authorize the bot to access your Google Calendar first. Use the /auth command."

    now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    try:
        events_result = service.events().list(calendarId='primary', timeMin=now,
                                              maxResults=num_events, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
    except HttpError as error:
        logger.error(f"An error occurred: {error}")
        return f"Error retrieving events: {error}"

    if not events:
        return 'No upcoming events found.'

    event_details = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        start_datetime = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
        local_timezone = pytz.timezone('Europe/Moscow')  # Замените на ваш часовой пояс
        local_start_time = start_datetime.replace(tzinfo=pytz.utc).astimezone(local_timezone)

        event_details.append(f"{event['summary']} at {local_start_time.strftime('%Y-%m-%d %H:%M')}")

    return "\n".join(event_details)






# Функция для отправки уведомлений (запускается периодически)
async def send_event_reminders(bot: Bot):  # Pass Bot instance
    # Logic for sending event reminders
    # This would involve iterating through authorized users,
    # checking their calendars, and sending notifications.
    pass


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


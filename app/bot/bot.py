import os
import logging
import datetime
from typing import List, Tuple

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
from aiogram.utils.markdown import hbold, hitalic

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')  # Path to the downloaded credentials.json file
USER_CREDENTIALS_DIR = "/service/user_credentials"

# Настройки Logging
logging.basicConfig(level=logging.INFO)
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
    events = await get_upcoming_events(user_id)
    if not events:
        await message.answer("No upcoming events found.")
        return

    formatted_events = []
    for calendar_name, event_summary, event_start_time_str in events:
        color = get_calendar_color(calendar_name)
        formatted_events.append(
            f"{color}<b>{calendar_name}:</b> {event_summary} - {event_start_time_str}\n"
        )

    await message.answer(
        "\n".join(formatted_events),
        parse_mode="HTML"
    )

def get_calendar_color(calendar_name: str) -> str:
    """
    Assigns a color to a calendar based on its name.
    """
    calendar_colors = {
        "Важные срочные": "🟥",
        "Важные несрочные": "🟩",
        "Неважные срочные": "🟦",
        "Неважные несрочные": "🟧",
        "Праздники России": "🎉",
        "dknotion@gmail.com": "🟪",  # Цвет для календаря dknotion@gmail.com
    }
    return calendar_colors.get(calendar_name, "⬛️")  # Default color




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

async def get_calendar_list(service):
    """
    Gets the list of calendars for the user.
    """
    try:
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])
        return calendars
    except HttpError as error:
        logger.error(f"An error occurred: {error}")
        return []

async def get_events_from_calendar(service, calendar_id, num_events=5):
    """
    Gets upcoming events from a specific calendar.
    """
    now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    try:
        events_result = service.events().list(calendarId=calendar_id, timeMin=now,
                                              maxResults=num_events, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
        return events
    except HttpError as error:
        logger.error(f"An error occurred: {error}")
        return []

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
        return "Please authorize the bot to access your OpenAI Calendar first. Use the /auth command."

    calendars = await get_calendar_list(service)
    if not calendars:
        return "No calendars found or could not retrieve the calendar list."

    all_events = []
    for calendar in calendars:
        calendar_id = calendar['id']
        calendar_name = calendar['summary']  # Имя календаря
        events = await get_events_from_calendar(service, calendar_id, num_events)

        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            start_datetime = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            if start_datetime.tzinfo is None:
                local_start_time = start_datetime.replace(tzinfo=pytz.utc).astimezone(LOCAL_TIMEZONE)
            else:
                local_start_time = start_datetime.astimezone(LOCAL_TIMEZONE)
            all_events.append((calendar_name, event['summary'], local_start_time.strftime('%Y-%m-%d %H:%M')))

    return all_events





async def send_event_reminders(bot: Bot):
    """
    Sends event reminders to all authorized users.
    """
    # Get the list of user IDs from the credentials directory
    user_ids = get_all_user_ids()

    now = datetime.datetime.now(LOCAL_TIMEZONE)# Замените на ваш часовой пояс

    for user_id in user_ids:
        upcoming_events = await get_upcoming_events(user_id, num_events=5)
        formatted_events = []
        for calendar_name, event_summary, event_start_time_str in upcoming_events:
            color = get_calendar_color(calendar_name)
            formatted_events.append(
                f"{color}<b>{calendar_name}:</b> Reminder: {event_summary} is starting at {event_start_time_str}"
            )
            event_start_time = datetime.datetime.strptime(event_start_time_str, '%Y-%m-%d %H:%M')
            event_start_time = LOCAL_TIMEZONE.localize(event_start_time)
            time_difference = event_start_time - now
            if datetime.timedelta(minutes=15) <= time_difference <= datetime.timedelta(
                    hours=2):  # Check if event is in 15-30 minutes
                total_minutes = int(time_difference.total_seconds() / 60)

                if total_minutes < 60:
                    time_string = f"{total_minutes} minutes"
                else:
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    time_string = f"{hours} hours {minutes} minutes"
                await bot.send_message(chat_id=user_id,
                                       text=f"Reminder: {event_summary} will start in {time_string}")
            logger.info(f"Reminder sent to user {user_id} for event {event_summary}")



def get_all_user_ids():
    """
    Gets all user IDs by listing files in the credentials directory.
    """
    user_ids = []
    for filename in os.listdir(USER_CREDENTIALS_DIR):
        if filename.startswith('token_') and filename.endswith('.json'):
            try:
                user_id = int(filename.split('_')[1].split('.')[0])
                user_ids.append(user_id)
            except ValueError:
                logger.warning(f"Invalid filename in credentials directory: {filename}")
    return user_ids









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


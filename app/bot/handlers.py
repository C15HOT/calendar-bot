import os
import logging
import datetime
from aiogram import Bot
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.bot.keyboards import get_auth_keyboard, get_postpone_keyboard
from app.settings import get_settings



CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')
USER_CREDENTIALS_DIR = "/service/user_credentials"
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

settings = get_settings()


LOCAL_TIMEZONE = pytz.timezone('Europe/Moscow')

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
        try:
            creds = Credentials.from_authorized_user_file(token_path, settings.scopes)
            logger.info(f"Token expiry from file: {creds.expiry}")
            logger.info(f"Current UTC time: {datetime.datetime.now(pytz.utc)}")
        except Exception as e:
            logger.error(f"Error loading credentials from file for user {user_id}: {e}")
            creds = None  # Set creds to None if loading fails

    # Force refresh the access token if a refresh token exists
    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save the refreshed credentials
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            logger.info(f"Token forcibly refreshed for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to refresh token for user {user_id}: {e}")
            return ("Failed to refresh token. Please re-authorize the bot.", get_auth_keyboard())
    elif creds:
        # If there's no refresh token, the token needs re-authorization
        logger.warning(f"No refresh token found for user {user_id}. Re-authorization required.")
        return ("No refresh token found. Please re-authorize the bot.", get_auth_keyboard())
    else:
        # If there's no creds
        return ("Please authorize the bot to access your OpenAI Calendar first.", get_auth_keyboard())

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

    if isinstance(service, tuple): #If response is tuple
        return service
    try:
        calendars = await get_calendar_list(service)
    except Exception as e:
        logger.error(f"An error occurred while fetching calendar list: {e}")
        return "An error occurred while fetching the calendar list. Please try again later.", get_auth_keyboard()

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
        for calendar_name, event_summary, event_start_time_str in upcoming_events:
            color = get_calendar_color(calendar_name)

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
                                       text=f"<b>Reminder: </b> {color} {event_summary} will start in {time_string}", parse_mode="HTML", reply_markup=get_postpone_keyboard(event_id=1)) #TODO
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

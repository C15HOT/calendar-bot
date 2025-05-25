import json
import os
import logging
import datetime
from pprint import pprint

from aiogram import Bot
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dataclasses import dataclass, asdict

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain_core.prompts import ChatPromptTemplate

from app.bot.keyboards import get_auth_keyboard, get_postpone_keyboard
from app.settings import get_settings

from langchain_gigachat.chat_models import GigaChat

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '../../credentials.json')
USER_CREDENTIALS_DIR = "/service/user_credentials"
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

settings = get_settings()

llm = GigaChat(
    # Для авторизации запросов используйте ключ, полученный в проекте GigaChat API
    credentials=settings.gigachat_key,
    verify_ssl_certs=False,
)
DEFAULT_CALENDAR_ID = 'primary'
LOCAL_TIMEZONE = pytz.timezone('Europe/Moscow')

async def get_calendar_color(calendar_name: str) -> str:
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

async def get_creds(user_id):
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
    return creds

async def get_calendar_service(user_id):
    creds = await get_creds(user_id)
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
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
    service = await get_calendar_service(user_id)

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
    user_ids = await get_all_user_ids()

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

async def get_all_user_ids():
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

@dataclass
class EventDetails:
    event_summary: str
    event_description: str
    date: str
    start_time: str
    end_time: str

# Функция для создания события в Google Calendar
async def create_google_calendar_event(user_id, event_summary, event_description, start_time, end_time, calendar_id=DEFAULT_CALENDAR_ID):
    """
    Creates an event in Google Calendar.
    """
    creds = await get_creds(user_id)
    if creds is None:
      logger.error("Failed to retrieve credentials for user.")
      return False  # Или выбросить исключение

    try:
        service = build('calendar', 'v3', credentials=creds)

        event = {
            'summary': event_summary,
            'description': event_description,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'Europe/Moscow',  # Замените на ваш часовой пояс
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'Europe/Moscow',  # Замените на ваш часовой пояс
            },
        }

        event = service.events().insert(calendarId=calendar_id, body=event).execute()
        logger.info(f'Event created in calendar {calendar_id}: {event.get("htmlLink")}')
        return True
    except HttpError as error:
        logger.error(f"An error occurred while creating the event: {error}")
        return False


# Функция для обработки запроса пользователя, классификации и создания события (ОБЪЕДИНЕННАЯ)
async def create_event_from_text(user_id, user_text):
    system_template = """
    You are a helpful assistant that extracts event details from user input.
    Given the following text, extract the event summary, event_description, date, start time, and end time.

    If the date isn't given, assume the current date.
    Try to understand what date is indicated in the user's message relative to the current date, the date can be described as the day of the week, or as an indication of tomorrow, the day after tomorrow and similar words. You need to convert this to the correct date format
    If the time isn't given, return 'NONE'. You *MUST* have a start time. If the user provides a duration, calculate the end time.
    If there is no explicit event_description, provide a short description of what the event is.
    Ни в коем случае нельзя создавать события раньше, чем сегодняшняя дата. Если ты не уверен как интерпретировать дату, посмотри в календарь и попытайся определить соответствие дней недели относительно текущей даты
    Return the data in the following JSON format:
    {{
      "event_summary": "...",
      "event_description": "...",
      "date": "YYYY-MM-DD",
      "start_time": "HH:MM",
      "end_time": "HH:MM"
    }}
    current date: {current_datetime}
    User Text: {user_text}
    """
    current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt_template = ChatPromptTemplate.from_messages(
        [("system", system_template), ("user", "{user_text}")]
    )

    prompt = prompt_template.invoke({"user_text": user_text, "current_datetime": current_datetime})

    response = llm.invoke(prompt)
    response_content = response.content

    try:
        event_data = json.loads(response_content)
        event_details = EventDetails(**event_data)
        print(asdict(event_details))  # Вывод в виде словаря

        event_summary = event_details.event_summary
        event_description = event_details.event_description
        date_str = event_details.date
        start_time_str = event_details.start_time
        end_time_str = event_details.end_time

        # Проверка, что время указано
        if start_time_str == "NONE" or not start_time_str:
            return "Sorry, I need a specific time to schedule the event."

        try:
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception as exc:
            date = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")

        start_time = datetime.datetime.combine(date, datetime.datetime.strptime(start_time_str, "%H:%M").time())
        # Если не указано время окончания - прибавляем час
        if end_time_str == "NONE" or not end_time_str:
            end_time = start_time + datetime.timedelta(hours=1)
        else:
            end_time = datetime.datetime.combine(date, datetime.datetime.strptime(end_time_str, "%H:%M").time())

        # 6. Получение списка календарей
        creds = await get_creds(user_id)
        if creds is None:
            return "Sorry, could not retrieve credentials."

        service = build('calendar', 'v3', credentials=creds)
        available_calendars = await get_calendar_list(service)

        # 7. Выбор подходящего календаря
        calendar_id = await choose_calendar(event_summary, event_description, available_calendars)

        # 8. Создание события в выбранном календаре
        success = await create_google_calendar_event(user_id, event_summary, event_description, start_time, end_time, calendar_id)

        if success:
            # Находим имя календаря по его ID
            calendar_name = next((cal['summary'] for cal in available_calendars if cal['id'] == calendar_id), "Стандартный")
            return f"Event created in {calendar_name} calendar."
        else:
            return "Sorry, there was an error creating the event."

    except json.JSONDecodeError as e:
        logger.error(f"JSONDecodeError: {e}, Response: {response}")
        return "Sorry, I couldn't understand the details. Please rephrase your request."
    except ValueError as e:
        logger.error(f"ValueError: {e}")
        return "Sorry, I couldn't parse the date or time. Please check the format."
    except Exception as e:
        logger.exception("An unexpected error occurred")
        return "Sorry, there was an unexpected error. Please try again."


# Функция для выбора календаря на основе названия и описания события
async def choose_calendar(event_summary, event_description, available_calendars):
    """
    Chooses the most appropriate calendar based on event summary, description, and available calendars.
    """

    # 1. Инициализация LLM
    # llm = OpenAI(openai_api_key=OPENAI_API_KEY, temperature=0.5) #Уменьшил temperature для большей предсказуемости

    # 2. Создание prompt template
    template = """
    You are a helpful assistant that selects the best Google Calendar to put an event into.

    Given the following event summary and description, and a list of Google Calendars with their names, choose the *single best* calendar for the event.

    Your answer *MUST* be the *EXACT* name of one of the available calendars. If none of the calendars are appropriate, return "Стандартный".

    Event Summary: {event_summary}
    Event Description: {event_description}

    Available Calendars:
    {calendar_list}

    Best Calendar:
    """

    prompt = PromptTemplate(template=template, input_variables=["event_summary", "event_description", "calendar_list"])

    # Формируем список календарей для prompt
    calendar_names = [calendar['summary'] for calendar in available_calendars]
    calendar_list_str = "\n".join(calendar_names)

    # 3. Создание LLM Chain
    llm_chain = LLMChain(prompt=prompt, llm=llm)

    # 4. Запуск LLM Chain
    chosen_calendar_name = llm_chain.run({"event_summary": event_summary, "event_description": event_description, "calendar_list": calendar_list_str})
    chosen_calendar_name = chosen_calendar_name.strip()  #Удалите лишние пробелы

    logger.info(f"Chosen calendar name: {chosen_calendar_name}")

    # 5. Поиск calendar_id по имени
    for calendar in available_calendars:
        if calendar['summary'] == chosen_calendar_name:
            return calendar['id']

    logger.warning(f"Calendar '{chosen_calendar_name}' not found. Using default calendar.")
    return DEFAULT_CALENDAR_ID  # Если не нашли, возвращаем стандартный
import json
import os
import logging
import datetime
from pprint import pprint
from typing import Optional

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
            logger.info(f"Срок действия токена из файла: {creds.expiry}")
            logger.info(f"Текущее время UTC: {datetime.datetime.now(pytz.utc)}")
            
            if creds.expired:
                logger.info(f"Токен истек для пользователя {user_id}, пытаемся обновить")
                if creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        with open(token_path, 'w') as f:
                            f.write(creds.to_json())
                        logger.info(f"Токен успешно обновлен для пользователя {user_id}")
                    except Exception as e:
                        logger.error(f"Не удалось обновить токен для пользователя {user_id}: {e}")
                        os.remove(token_path)
                        creds = None
                else:
                    logger.warning(f"Refresh токен недоступен для пользователя {user_id}")
                    os.remove(token_path)
                    creds = None
            else:
                logger.info(f"Токен все еще действителен для пользователя {user_id}")
                
        except Exception as e:
            logger.error(f"Ошибка загрузки учетных данных из файла для пользователя {user_id}: {e}")
            if os.path.exists(token_path):
                os.remove(token_path)
            creds = None
    return creds

async def get_calendar_service(user_id):
    creds = await get_creds(user_id)
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    
    if not creds:
        logger.warning(f"Действительные учетные данные не найдены для пользователя {user_id}")
        return ("Пожалуйста, сначала авторизуйте бота для доступа к вашему Google Calendar.", get_auth_keyboard())

    # Проверяем, есть ли refresh токен
    if not creds.refresh_token:
        logger.warning(f"Refresh токен не найден для пользователя {user_id}. Требуется повторная авторизация.")
        # Удаляем файл токена, так как refresh токен отсутствует
        if os.path.exists(token_path):
            os.remove(token_path)
        return ("Refresh токен не найден. Пожалуйста, повторно авторизуйте бота.", get_auth_keyboard())

    # Проверяем, истек ли токен и пытаемся обновить его
    if creds.expired:
        try:
            creds.refresh(Request())
            # Сохраняем обновленные учетные данные
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            logger.info(f"Токен успешно обновлен для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Не удалось обновить токен для пользователя {user_id}: {e}")
            # Если не удалось обновить, удаляем файл токена
            if os.path.exists(token_path):
                os.remove(token_path)
            return ("Не удалось обновить токен. Пожалуйста, повторно авторизуйте бота.", get_auth_keyboard())

    try:
        service = build('calendar', 'v3', credentials=creds)
        # Проверяем, что сервис работает, делая тестовый запрос
        service.calendarList().list(maxResults=1).execute()
        return service
    except HttpError as error:
        logger.error(f"Произошла ошибка при создании сервиса календаря для пользователя {user_id}: {error}")
        # Если ошибка связана с аутентификацией, удаляем токен
        if error.resp.status in [401, 403]:
            if os.path.exists(token_path):
                os.remove(token_path)
            return ("Ошибка аутентификации. Пожалуйста, повторно авторизуйте бота.", get_auth_keyboard())
        return None
    except Exception as error:
        logger.error(f"Неожиданная ошибка при создании сервиса календаря для пользователя {user_id}: {error}")
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
        logger.error(f"Произошла ошибка: {error}")
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
        logger.error(f"Произошла ошибка: {error}")
        return []

# Функция для сохранения учетных данных пользователя (OAuth2 flow)
async def save_credentials(user_id, credentials):
    os.makedirs(USER_CREDENTIALS_DIR, exist_ok=True)
    token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
    
    # Проверяем, что у нас есть refresh токен
    if not credentials.refresh_token:
        logger.warning(f"Refresh токен не получен для пользователя {user_id}. Это может вызвать проблемы с авторизацией позже.")
    
    try:
        with open(token_path, 'w') as token:
            token.write(credentials.to_json())
        logger.info(f"Учетные данные сохранены для пользователя {user_id} в {token_path}")
        logger.info(f"Срок действия токена: {credentials.expiry}")
        logger.info(f"Есть refresh токен: {bool(credentials.refresh_token)}")
    except Exception as e:
        logger.error(f"Не удалось сохранить учетные данные для пользователя {user_id}: {e}")
        raise

async def get_upcoming_events(user_id, num_events=5):
    service = await get_calendar_service(user_id)

    if isinstance(service, tuple): #If response is tuple
        return service
    
    all_events = []
    
    try:
        calendars = await get_calendar_list(service)
        
        if not calendars:
            return "Календари не найдены или не удалось получить список календарей."

        for calendar in calendars:
            calendar_id = calendar['id']
            calendar_name = calendar['summary']  # Имя календаря
            events = await get_events_from_calendar(service, calendar_id, num_events)

            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                if start:
                    start_datetime = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                    if start_datetime.tzinfo is None:
                        local_start_time = start_datetime.replace(tzinfo=pytz.utc).astimezone(LOCAL_TIMEZONE)
                    else:
                        local_start_time = start_datetime.astimezone(LOCAL_TIMEZONE)
                    all_events.append((calendar_name, event['summary'], local_start_time.strftime('%Y-%m-%d %H:%M')))
                    
    except Exception as e:
        logger.error(f"Произошла ошибка при получении списка календарей: {e}")
        return []

    return all_events

async def send_event_reminders(bot: Bot):
    """
    Sends event reminders to all authorized users.
    """
    # Get the list of user IDs from the credentials directory
    user_ids = await get_all_user_ids()
    
    if not user_ids:
        logger.info("Нет авторизованных пользователей для отправки напоминаний")
        return

    now = datetime.datetime.now(LOCAL_TIMEZONE)# Замените на ваш часовой пояс

    for user_id in user_ids:
        upcoming_events = await get_upcoming_events(user_id, num_events=5)
        
        # Проверяем, что upcoming_events является списком, а не строкой ошибки или кортежем
        if isinstance(upcoming_events, (str, tuple)):
            logger.warning(f"Ошибка получения событий для пользователя {user_id}: {upcoming_events}")
            continue
            
        for calendar_name, event_summary, event_start_time_str in upcoming_events:
            color = get_calendar_color(calendar_name)

            event_start_time = datetime.datetime.strptime(event_start_time_str, '%Y-%m-%d %H:%M')
            event_start_time = LOCAL_TIMEZONE.localize(event_start_time)
            time_difference = event_start_time - now
            if datetime.timedelta(minutes=15) <= time_difference <= datetime.timedelta(
                    hours=2):  # Проверяем, если событие через 15-30 минут
                total_minutes = int(time_difference.total_seconds() / 60)

                if total_minutes < 60:
                    time_string = f"{total_minutes} минут"
                else:
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    time_string = f"{hours} часов {minutes} минут"
                await bot.send_message(chat_id=user_id,
                                       text=f"<b>Напоминание: </b> {color} {event_summary} начнется через {time_string}", parse_mode="HTML", reply_markup=get_postpone_keyboard(event_id=1)) #TODO
                logger.info(f"Напоминание отправлено пользователю {user_id} для события {event_summary}")

async def get_all_user_ids():
    """
    Gets all user IDs by listing files in the credentials directory.
    """
    user_ids = []
    
    # Проверяем, существует ли директория
    if not os.path.exists(USER_CREDENTIALS_DIR):
        logger.warning(f"Директория учетных данных не существует: {USER_CREDENTIALS_DIR}")
        return user_ids
        
    for filename in os.listdir(USER_CREDENTIALS_DIR):
        if filename.startswith('token_') and filename.endswith('.json'):
            try:
                user_id = int(filename.split('_')[1].split('.')[0])
                user_ids.append(user_id)
            except ValueError:
                logger.warning(f"Некорректное имя файла в директории учетных данных: {filename}")
    return user_ids

@dataclass
class EventDetails:
    event_summary: str
    event_description: str
    date: str
    start_time: str
    end_time: str
    calendar_id: Optional[str] = None
    calendar_name: Optional[str] = None

# Функция для создания события в Google Calendar
async def create_google_calendar_event(user_id, event_summary, event_description, start_time, end_time, calendar_id=DEFAULT_CALENDAR_ID):
    """
    Creates an event in Google Calendar.
    """
    creds = await get_creds(user_id)
    if creds is None:
      logger.error("Не удалось получить учетные данные для пользователя.")
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
        logger.info(f'Событие создано в календаре {calendar_id}: {event.get("htmlLink")}')
        return True
    except HttpError as error:
        logger.error(f"Произошла ошибка при создании события: {error}")
        return False


# Функция для обработки запроса пользователя, классификации и создания события (ОБЪЕДИНЕННАЯ)
async def create_event_from_text(user_id, user_text):
    system_template = """
        You are a helpful assistant that extracts event details from user input.
        Given the following text, extract the event summary, event_description, date, start time, and end time.

        If the date isn't given in the text, **use the current date.**
        Try to understand what date is indicated in the user's message relative to the current date, the date can be described as the day of the week, or as an indication of tomorrow, the day after tomorrow and similar words. You need to convert this to the correct date format.
        If the time isn't given, return 'NONE'. You *MUST* have a start time. If the user provides a duration, calculate the end time.
        If there is no explicit event_description, provide a short description of what the event is.

        **Do not use dates from the past.  Any date generated must be equal to or later than the current date.**

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

    try:
        response = llm.invoke(prompt)
        response_content = response.content
    except Exception as e:
        logger.error(f"Ошибка при вызове LLM: {e}")
        return "Извините, произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."

    try:
        event_data = json.loads(response_content)
        
        # Проверяем, что все необходимые поля присутствуют
        required_fields = ['event_summary', 'event_description', 'date', 'start_time', 'end_time']
        for field in required_fields:
            if field not in event_data:
                logger.error(f"Отсутствует обязательное поле в ответе LLM: {field}")
                return "Извините, произошла ошибка при обработке деталей события. Пожалуйста, попробуйте снова."
        
        # Проверяем, что все поля являются строками
        for field in required_fields:
            if not isinstance(event_data[field], str):
                logger.error(f"Поле {field} не является строкой: {type(event_data[field])}")
                return "Извините, произошла ошибка при обработке деталей события. Пожалуйста, попробуйте снова."
        
        try:
            event_details = EventDetails(**event_data)
        except TypeError as e:
            logger.error(f"Ошибка при создании EventDetails: {e}")
            return "Извините, произошла ошибка при обработке деталей события. Пожалуйста, попробуйте снова."
        except Exception as e:
            logger.error(f"Неожиданная ошибка при создании EventDetails: {e}")
            return "Извините, произошла ошибка при обработке деталей события. Пожалуйста, попробуйте снова."
        
        print(asdict(event_details))  # Вывод в виде словаря

        event_summary = event_details.event_summary
        event_description = event_details.event_description
        date_str = event_details.date
        start_time_str = event_details.start_time
        end_time_str = event_details.end_time

        # Проверяем, что название события не пустое
        if not event_summary or event_summary.strip() == "":
            return "Извините, не удалось определить название события. Пожалуйста, укажите более подробную информацию."

        # Проверяем, что описание события не пустое
        if not event_description or event_description.strip() == "":
            event_description = f"Событие: {event_summary}"

        # Проверка, что время указано
        if start_time_str == "NONE" or not start_time_str:
            return "Извините, мне нужно конкретное время для планирования события."

        try:
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Некорректный формат даты: {date_str}, используем завтрашний день")
            # Если не удалось распарсить дату, используем завтрашний день
            tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
            date = tomorrow.date()
        except Exception as exc:
            logger.warning(f"Ошибка при парсинге даты: {exc}, используем завтрашний день")
            # Если не удалось распарсить дату, используем завтрашний день
            tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
            date = tomorrow.date()

        start_time = datetime.datetime.combine(date, datetime.datetime.strptime(start_time_str, "%H:%M").time())
        # Если не указано время окончания - прибавляем час
        if end_time_str == "NONE" or not end_time_str:
            end_time = start_time + datetime.timedelta(hours=1)
        else:
            try:
                end_time = datetime.datetime.combine(date, datetime.datetime.strptime(end_time_str, "%H:%M").time())
            except ValueError:
                logger.warning(f"Некорректное время окончания: {end_time_str}, используем время начала + 1 час")
                end_time = start_time + datetime.timedelta(hours=1)

        # 6. Получение списка календарей
        creds = await get_creds(user_id)
        if creds is None:
            return "Извините, не удалось получить учетные данные."

        service = build('calendar', 'v3', credentials=creds)
        available_calendars = await get_calendar_list(service)
        
        if not available_calendars:
            logger.warning(f"Не найдено доступных календарей для пользователя {user_id}")
            # Используем календарь по умолчанию
            calendar_id = DEFAULT_CALENDAR_ID
            calendar_name = 'Стандартный'
        else:
            # 7. Выбор подходящего календаря
            calendar_id = await choose_calendar(event_summary, event_description, available_calendars)
            print('\n')
            print(calendar_id)
            print('\n')
            calendar_name = next((cal['summary'] for cal in available_calendars if cal['id'] == calendar_id), "Стандартный")
            if not calendar_id:
                calendar_id = DEFAULT_CALENDAR_ID
                calendar_name = 'Стандартный'
        # 8. Создание события в выбранном календаре
        print('\n')
        print(calendar_id)
        print(calendar_name)
        print('12312312312312312312')
        print('\n')
        event_details.calendar_id = calendar_id
        event_details.calendar_name = calendar_name
        event_details.start_time = start_time
        event_details.end_time = end_time
        return event_details
        # success = await create_google_calendar_event(user_id, event_summary, event_description, start_time, end_time, calendar_id)
        #
        # if success:
        #     # Находим имя календаря по его ID
        #     calendar_name = next((cal['summary'] for cal in available_calendars if cal['id'] == calendar_id), "Стандартный")
        #     return f"Event created in {calendar_name} calendar."
        # else:
        #     return "Sorry, there was an error creating the event."

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON: {e}, Ответ: {response}")
        return "Извините, я не смог понять детали. Пожалуйста, переформулируйте ваш запрос."
    except ValueError as e:
        logger.error(f"Ошибка значения: {e}")
        return "Извините, я не смог разобрать дату или время. Пожалуйста, проверьте формат."
    except Exception as e:
        logger.exception("Произошла неожиданная ошибка")
        return "Извините, произошла неожиданная ошибка. Пожалуйста, попробуйте снова."


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
    try:
        chosen_calendar_name = llm_chain.run({"event_summary": event_summary, "event_description": event_description, "calendar_list": calendar_list_str})
        chosen_calendar_name = chosen_calendar_name.strip()  #Удалите лишние пробелы
    except Exception as e:
        logger.error(f"Ошибка при выборе календаря: {e}")
        return DEFAULT_CALENDAR_ID

    logger.info(f"Выбранное имя календаря: {chosen_calendar_name}")

    # 5. Поиск calendar_id по имени
    pprint(chosen_calendar_name)

    if not chosen_calendar_name:
        return DEFAULT_CALENDAR_ID
    for calendar in available_calendars:
        if calendar['summary'] == chosen_calendar_name:
            return calendar['id']

    logger.warning(f"Календарь '{chosen_calendar_name}' не найден. Используется календарь по умолчанию.")
    return DEFAULT_CALENDAR_ID  # Если не нашли, возвращаем стандартный

async def check_token_health(user_id):
    """
    Проверяет состояние токена пользователя и возвращает информацию о его здоровье.
    """
    creds = await get_creds(user_id)
    if not creds:
        return "no_token", "Токен не найден"
    
    if not creds.refresh_token:
        return "no_refresh", "Refresh токен недоступен"
    
    if creds.expired:
        try:
            creds.refresh(Request())
            # Сохраняем обновленные учетные данные
            token_path = os.path.join(USER_CREDENTIALS_DIR, f'token_{user_id}.json')
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            return "refreshed", "Токен успешно обновлен"
        except Exception as e:
            logger.error(f"Не удалось обновить токен для пользователя {user_id}: {e}")
            return "refresh_failed", f"Не удалось обновить токен: {e}"
    
    # Проверяем, сколько времени осталось до истечения токена
    time_until_expiry = creds.expiry - datetime.datetime.now(pytz.utc)
    if time_until_expiry.total_seconds() < 3600:  # Меньше часа
        return "expiring_soon", f"Токен истекает через {int(time_until_expiry.total_seconds() / 60)} минут"
    
    return "healthy", "Токен в порядке"

async def monitor_tokens(bot: Bot):
    """
    Мониторит состояние токенов всех пользователей и уведомляет о проблемах.
    """
    user_ids = await get_all_user_ids()
    
    if not user_ids:
        logger.info("Нет авторизованных пользователей для мониторинга токенов")
        return
    
    for user_id in user_ids:
        status, message = await check_token_health(user_id)
        
        if status in ["no_token", "no_refresh", "refresh_failed"]:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ Обнаружена проблема с авторизацией: {message}\n\n"
                         f"Пожалуйста, используйте /auth для повторной авторизации бота.",
                    reply_markup=get_auth_keyboard()
                )
                logger.warning(f"Проблема со здоровьем токена для пользователя {user_id}: {message}")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление о проблеме с токеном пользователю {user_id}: {e}")
        
        elif status == "expiring_soon":
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"ℹ️ Ваш токен авторизации скоро истечет: {message}\n\n"
                         f"Бот автоматически обновит его, но если у вас возникнут проблемы, "
                         f"пожалуйста, используйте /auth для повторной авторизации."
                )
                logger.info(f"Токен скоро истечет для пользователя {user_id}: {message}")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление об истечении токена пользователю {user_id}: {e}")
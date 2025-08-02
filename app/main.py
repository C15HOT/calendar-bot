from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import HTMLResponse
from uvicorn import run
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.bot.handlers import send_event_reminders, save_credentials, monitor_tokens
from app.bot.init_bot import dp, bot
from app.bot.bot import start_bot, stop_bot, user_router
import urllib.parse
from app.settings import get_settings

scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск приложения...")
    dp.include_router(user_router)
    logger.info("Роутер подключен")
    webhook_url = settings.webhook_url
    logger.info(f"Настройка webhook: {webhook_url}")
    logger.info(f"Bot token: {settings.bot_token[:10]}...")
    logger.info(f"Server address: {settings.server_address}")
    try:
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True
        )
        logger.info("Webhook настроен успешно")
    except Exception as e:
        logger.error(f"Ошибка при настройке webhook: {e}")
    await start_bot()
    scheduler.add_job(send_event_reminders, "interval", minutes=int(settings.default_remind_time), args=(bot,))
    scheduler.add_job(monitor_tokens, "interval", hours=6, args=(bot,))  # Проверяем токены каждые 6 часов
    scheduler.start()
    logger.info("Планировщик запущен")
    yield
    logger.info("Остановка приложения...")
    await stop_bot()
    scheduler.shutdown()
    await bot.delete_webhook()
    logger.info("Приложение остановлено")

app = FastAPI(
    title="calendar",
    description="",
    lifespan=lifespan
)

app.add_middleware(CORSMiddleware,
                   allow_origins=['*'],
                   allow_credentials=True,
                   allow_methods=['*'],
                   allow_headers=['*'])
@app.get('/test')
async def test():
    logger.info("Получен запрос на /test")
    return {'test': 'test'}

@app.get("/webhook-info")
async def webhook_info():
    """Проверяет информацию о webhook"""
    try:
        webhook_info = await bot.get_webhook_info()
        logger.info(f"Webhook info: {webhook_info}")
        return webhook_info
    except Exception as e:
        logger.error(f"Ошибка при получении информации о webhook: {e}")
        return {"error": str(e)}

@app.get("/callback")
async def callback_handler(request: Request):
    code = request.query_params.get('code')
    encoded_composite_state = request.query_params.get('state')
    scope_from_callback = request.query_params.get('scope')

    if not code or not encoded_composite_state:
        raise HTTPException(status_code=400, detail="Missing required parameters")
    
    try:
        composite_state = urllib.parse.unquote(encoded_composite_state)
        auth_state, user_id = composite_state.split("|")
        if not user_id:
            raise HTTPException(status_code=400, detail="Missing user_id in state")

        user_id = int(user_id)  # Convert user_id to int
    except Exception as e:
        logger.error(f"Ошибка при парсинге параметра state: {e}")
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Find the state by user_id (which is the same as chat_id in private chats)
    fsm_context = dp.fsm.get_context(bot, chat_id=user_id, user_id=user_id)
    data = await fsm_context.get_data()

    stored_state = data.get('auth_state')
    flow = data.get('auth_flow')

    if stored_state != auth_state:
        logger.error(f"Несоответствие состояния для пользователя {user_id}: сохраненное={stored_state}, полученное={auth_state}")
        raise HTTPException(status_code=400, detail="State mismatch!")

    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Проверяем, что получили refresh токен
        if not credentials.refresh_token:
            logger.warning(f"Refresh токен не получен для пользователя {user_id}")
            await bot.send_message(chat_id=user_id,
                                   text="⚠️ Предупреждение: Refresh токен не получен. Это может вызвать проблемы с авторизацией позже.")
        
        await save_credentials(user_id, credentials)

        await bot.send_message(chat_id=user_id,
                               text="✅ Авторизация успешна! Теперь вы можете использовать /events для просмотра предстоящих событий.")

        await fsm_context.clear()  # Clear the state
        return HTMLResponse(content="Авторизация успешна! Пожалуйста, вернитесь к Telegram боту.", status_code=200)

    except Exception as e:
        logger.error(f"Ошибка аутентификации для пользователя {user_id}: {e}")
        await bot.send_message(chat_id=user_id,
                               text=f"❌ Ошибка авторизации: {e}")
        raise HTTPException(status_code=500, detail=f"Authorization failed: {e}")


# Маршрут для обработки вебхуков
@app.post("/webhook")
async def webhook(request: Request) -> None:
    try:
        update_data = await request.json()
        logger.info(f"Получен webhook: {update_data}")
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        logger.info("Webhook обработан успешно")
    except Exception as e:
        logger.error(f"Ошибка при обработке webhook: {e}")
        raise

def main() -> None:
    run(
        app,
        host='0.0.0.0',
        port=8080
    )


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import HTMLResponse
from uvicorn import run
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.bot.init_bot import dp, bot
from app.bot.bot import start_handler, auth_handler, events_handler, save_credentials, start_bot, stop_bot
from aiogram.filters import CommandStart, Command

from app.settings import get_settings

scheduler = AsyncIOScheduler()

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = settings.webhook_url
    print(webhook_url)
    # await bot.set_webhook(
    #     url=webhook_url,
    #     allowed_updates=dp.resolve_used_update_types(),
    #     drop_pending_updates=True
    # )
    await start_bot()

    yield
    await stop_bot()

    await bot.delete_webhook()

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
    return {'test: test'}
# app.include_router(miniapp_router)
@app.get("/callback")
async def callback_handler(request: Request):
    code = request.query_params.get('code')
    state = request.query_params.get('state')
    user_id = request.query_params.get('user_id')

    if not code or not state or not user_id:
        raise HTTPException(status_code=400, detail="Missing required parameters")

    # Find the state by user_id
    fsm_context = dp.fsm.get_context(bot, user_id=int(user_id))
    data = await fsm_context.get_data()

    stored_state = data.get('auth_state')
    flow = data.get('auth_flow')

    if stored_state != state:
        raise HTTPException(status_code=400, detail="State mismatch!")

    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        await save_credentials(user_id, credentials)

        await bot.send_message(chat_id=user_id,
                               text="Authorization successful! You can now use /events to see your upcoming events.")

        await fsm_context.clear()  # Clear the state
        return HTMLResponse(content="Authorization successful! Please return to the Telegram bot.", status_code=200)

    except Exception as e:
        await bot.send_message(chat_id=user_id,
                               text=f"Authentication failed: {e}")
        raise HTTPException(status_code=500, detail=f"Authorization failed: {e}")


# Маршрут для обработки вебхуков
@app.post("/webhook")
async def webhook(request: Request) -> None:
    update = await request.json()  # Получаем данные из запроса
    # Обрабатываем обновление через диспетчер (dp) и передаем в бот
    await dp.feed_update(bot, update)

def main() -> None:
    run(
        app,
        host='0.0.0.0',
        port=8080
    )

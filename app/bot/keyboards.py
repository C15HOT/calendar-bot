from aiogram.types import ReplyKeyboardMarkup, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from app.settings import get_settings

settings = get_settings()


def get_auth_keyboard():
    """Creates an inline keyboard for re-authorization."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Re-authorize", callback_data="reauth") #Replace callback_data with actual re-auth command
        ]
    ])
    return keyboard


def get_postpone_keyboard(event_id: int):
    """Creates an inline keyboard for postponing a reminder."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Remind later", callback_data=f"show_postpone_times:{event_id}"),
        ]
    ])
    return keyboard


def get_postpone_time_options_keyboard(event_id: int):
    """Creates an inline keyboard with time options for postponing."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15 minutes", callback_data=f"postpone:{event_id}:15"),
            InlineKeyboardButton(text="30 minutes", callback_data=f"postpone:{event_id}:30"),
        ],
        [
            InlineKeyboardButton(text="1 hour", callback_data=f"postpone:{event_id}:60"),
            InlineKeyboardButton(text="2 hours", callback_data=f"postpone:{event_id}:120"),
        ],
        [
            InlineKeyboardButton(text="Cancel", callback_data=f"cancel_postpone:{event_id}"),  # Add a cancel button
        ]
    ])
    return keyboard


def main_keyboard(user_id: int, first_name: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    url_applications = f"{settings.BASE_SITE}/applications?user_id={user_id}"
    url_add_application = f'{settings.BASE_SITE}/form?user_id={user_id}&first_name={first_name}'
    kb.button(text="🌐 Мои заявки", web_app=WebAppInfo(url=url_applications))
    kb.button(text="📝 Оставить заявку", web_app=WebAppInfo(url=url_add_application))
    kb.button(text="ℹ️ О нас")
    if user_id == settings.ADMIN_ID:
        kb.button(text="🔑 Админ панель")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)
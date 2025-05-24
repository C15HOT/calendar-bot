from aiogram.types import ReplyKeyboardMarkup, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
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

def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Creates the main ReplyKeyboardMarkup with the 'Create Event' button."""
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [
            KeyboardButton(text="Создать событие")
        ],
        # Add other main menu buttons here if needed
    ], resize_keyboard=True)
    return keyboard

def get_postpone_keyboard(event_id: int):
    """Creates an inline keyboard for postponing a reminder."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Напомнить позже", callback_data=f"show_postpone_times:{event_id}"),
            InlineKeyboardButton(text="Отмена", callback_data=f"cancel_postpone:{event_id}"),
        ]
    ])
    return keyboard


def get_postpone_time_options_keyboard(event_id: int):
    """Creates an inline keyboard with time options for postponing."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15 минут", callback_data=f"postpone:{event_id}:15"),
            InlineKeyboardButton(text="30 минут", callback_data=f"postpone:{event_id}:30"),
        ],
        [
            InlineKeyboardButton(text="1 час", callback_data=f"postpone:{event_id}:60"),
            InlineKeyboardButton(text="2 часа", callback_data=f"postpone:{event_id}:120"),
        ],
        [
            InlineKeyboardButton(text="Отмена", callback_data=f"cancel_postpone:{event_id}"),  # Add a cancel button
        ]
    ])
    return keyboard



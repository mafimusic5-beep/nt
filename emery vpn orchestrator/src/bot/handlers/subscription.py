from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.ui.keyboards import main_menu_keyboard

router = Router()


@router.message(Command("subscription"))
async def subscription_handler(message: Message) -> None:
    await message.answer(
        "Откройте главное меню и выберите нужный раздел.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await message.answer("Главное меню", reply_markup=main_menu_keyboard())

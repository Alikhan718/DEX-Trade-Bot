from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def create_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """
    Создает объект InlineKeyboardButton.

    :param text: Текст кнопки, отображаемый пользователю.
    :param callback_data: Данные для callback'а, отправляемые при нажатии кнопки.
    :return: Объект InlineKeyboardButton.
    """
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def create_inline_keyboard(buttons: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """
    Создает объект InlineKeyboardMarkup из списка кнопок.

    :param buttons: Список списков объектов InlineKeyboardButton, где каждый подсписок — это строка кнопок.
    :return: Объект InlineKeyboardMarkup.
    """
    return InlineKeyboardMarkup(inline_keyboard=buttons)

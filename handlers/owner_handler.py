"""Уведомления владельцу бизнеса о вопросах, требующих живого человека."""

import logging

from telegram import User
from telegram.ext import ContextTypes

from config.settings import Settings

logger = logging.getLogger(__name__)


async def notify_owner(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    user: User | None,
    message_text: str,
    reason: str,
) -> bool:
    """Пересылает владельцу исходное сообщение клиента и его данные.

    Возвращает True, если уведомление доставлено.
    """
    chat_id = settings.owner_chat_id
    if chat_id is None:
        logger.warning(
            "Fallback невозможен для уведомления: owner_telegram_chat_id не задан в client_config.yaml"
        )
        return False

    if user is None:
        who = "неизвестный пользователь"
    else:
        who = f"{user.full_name}" + (f" (@{user.username})" if user.username else "") + f", id={user.id}"

    text = (
        f"🔔 Вопрос вне базы знаний ({reason})\n"
        f"От: {who}\n"
        f"Сообщение: {message_text}"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception:
        # Падение уведомления не должно ломать диалог с клиентом — логируем.
        logger.exception("Не удалось отправить уведомление владельцу (chat_id=%s)", chat_id)
        return False

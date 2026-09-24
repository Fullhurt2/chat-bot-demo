"""Точка входа: сборка и запуск Telegram-бота.

Запуск: python main.py
Все настройки берутся из .env и config/client_config.yaml.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from config.settings import get_settings
from handlers.message_handler import MessageProcessor
from services.llm_client import LLMClient

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "bot.log"

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    """Логи пишутся одновременно в файл logs/bot.log и в консоль."""
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Слишком болтливые библиотеки — только предупреждения и выше.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


async def on_error(update: object, context) -> None:
    """Глобальный обработчик ошибок PTB: сбой обработки не должен крашить бота.

    Реальная ошибка лежит в context.error (PTB перехватывает исключение),
    поэтому exc_info берём оттуда, а не из sys.exc_info().
    """
    logger.error("Ошибка при обработке апдейта: %s", update, exc_info=context.error)


def main() -> None:
    # Логирование настраиваем до загрузки настроек, чтобы видеть её
    # предупреждения (некорректный chat_id, override и т.п.).
    setup_logging(os.getenv("LOG_LEVEL", "INFO").strip().upper())
    settings = get_settings()
    logger.info(
        "Запуск бота для %s (модель: %s)", settings.business_name, settings.llm.model
    )

    # Один LLM-клиент на всё приложение (переиспользует HTTP-соединения).
    llm_client = LLMClient(settings.llm_api_url, settings.llm_api_key, settings.llm)
    processor = MessageProcessor(settings, llm_client)

    # concurrent_updates=True — апдейты обрабатываются параллельно: бот не
    # блокируется на одном пользователе, пока ждёт ответ LLM (ТЗ п.8).
    app: Application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_shutdown(llm_client.close)  # корректно закрываем HTTP-клиент LLM
        .build()
    )

    app.add_handler(CommandHandler("start", processor.handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processor.handle_message))
    app.add_error_handler(on_error)

    logger.info("Бот запущен (long polling). Для остановки нажмите Ctrl+C")
    # drop_pending_updates=True — не заваливаем клиентов старыми сообщениями после простоя.
    app.run_polling(drop_pending_updates=True)
    logger.info("Бот остановлен")


if __name__ == "__main__":
    main()

"""Логика определения «передать человеку» (fallback).

Два источника сигнала:
1. Ключевые слова из client_config.yaml (fallback_triggers) в тексте сообщения.
2. Токен [HANDOFF] в ответе модели — инструкция об этом зашита в system prompt;
   модель начинает с него ответ, когда не может ответить из базы знаний.
"""

# Токен, который модель ставит в начале ответа при неуверенности.
HANDOFF_TOKEN = "[HANDOFF]"


def find_trigger(message_text: str, triggers: list[str]) -> str | None:
    """Возвращает первый сработавший триггер из списка или None."""
    if not message_text or not triggers:
        return None
    lowered = message_text.casefold()
    for trigger in triggers:
        if trigger.casefold() in lowered:
            return trigger
    return None


def response_is_handoff(llm_response: str) -> bool:
    """True, если модель сигнализирует о неуверенности токеном [HANDOFF].

    По ТЗ токен ставится в начале ответа; для надёжности проверяем его
    в любом месте ответа — так «техническая» часть гарантированно
    не уйдёт клиенту.
    """
    return HANDOFF_TOKEN in llm_response

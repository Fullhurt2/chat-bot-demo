# Стресс на смешение языков: бот обязан отвечать на одном языке (ru/kk),
# не вставляя русские слова в казахский ответ и наоборот.
# Живая модель, полный пайплайн. Запуск из корня:
#   python tests/stress_language.py
#   CLIENT_CONFIG=client_config.yaml python tests/stress_language.py
import asyncio
import dataclasses
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.CRITICAL)
os.environ.setdefault("OWNER_CHAT_ID_OVERRIDE", "424242")

from config.settings import get_settings
from handlers.message_handler import MessageProcessor
from services.llm_client import LLMClient
from tests.test_history import FakeUpdate, FakeContext

KZ_LETTERS = re.compile(r"[әғқңөұүһі]", re.IGNORECASE)
# Служебные фразы бота (на языке клиента) — считаем «языком ответа».
KZ_SERVICE_RE = re.compile(r"(Сұрағыңызды|командасына жеткіземін|Бір сәт|нақтылаймын)", re.I)
RU_SERVICE_RE = re.compile(r"(Передаю ваш вопрос|уточняю у|Секунду)", re.I)
# Служебные/глагольные слова казахского (без учёта заимствований вроде «маникюр»).
KZ_MARKERS = re.compile(
    r"\b(біз|сіз|қалай|және|керек|расталады|жазылу|ертең|үшін|туралы|жоқ|"
    r"бар|рақмет|жылы|сұрастыр|мұндай|сол|сонымен|болса|болады|қалайсыз|жайлы)\b", re.I)
# Служебные слова русского (без заимствований и названий услуг).
RU_MARKERS = re.compile(
    r"\b(и|в|на|вы|мы|не|что|как|когда|можно|вам|вас|для|по|это|если|но|или|"
    r"спасибо|пожалуйста|хочу|сколько|стоит|работаете|приходите|записаться|"
    r"утро|вечер|завтра|сегодня)\b", re.I)


def analyze(text: str) -> list[str]:
    """Список нарушений чистоты языка в одном ответе."""
    problems = []
    for sent in re.split(r"(?<=[.!?…])\s+|\n", text):
        sent = sent.strip()
        if not sent:
            continue
        kz_letters = bool(KZ_LETTERS.search(sent))
        kz_words = KZ_MARKERS.findall(sent)
        ru_words = RU_MARKERS.findall(sent)
        if kz_letters and len(set(w.lower() for w in ru_words)) >= 2:
            problems.append(f"RU-слова в KZ-предложении: {sent[:70]!r}")
        if kz_words and len(set(w.lower() for w in ru_words)) >= 2:
            problems.append(f"RU-слова при KZ-лексике: {sent[:70]!r}")
    return problems


def looks_kz(text: str) -> bool:
    return bool(KZ_LETTERS.search(text)) or bool(KZ_MARKERS.search(text)) or bool(KZ_SERVICE_RE.search(text))


def looks_ru(text: str) -> bool:
    return bool(RU_MARKERS.search(text)) or bool(RU_SERVICE_RE.search(text))


# want: "kk" — ответ должен быть казахским; "ru" — русским; "auto" — язык на выбор,
# главное один язык (смешение не допускается). Шаги: каждый — отдельный ход диалога.
SCENARIOS = [
    {"id": "parking", "title": "мешанина с парковкой (правило 5)",
     "steps": ["Парковка барма платный?"], "want": "kk"},
    {"id": "booking-kk", "title": "можно записаться?",
     "steps": ["Записаться барма?"], "want": "kk"},
    {"id": "price-kk", "title": "цена по-казахски",
     "steps": ["Қайырлы кеш! Гель-покрытие қанша тұрады?"], "want": "kk"},
    {"id": "prepay-kk", "title": "предоплата по-казахски",
     "steps": ["Алдын ала төлем қалай жасалады?"], "want": "kk"},
    {"id": "greet-mix", "title": "приветствие KZ + вопрос RU",
     "steps": ["Сәлем, сколько стоит наращивание?"], "want": "auto"},
    {"id": "alt-ru-kk", "title": "диалог: RU-ход, потом KZ-вопрос",
     "steps": ["Хочу записаться на гель-покрытие", "Алдын ала төлем қалай жасалады?"],
     "want_last": "kk"},
    {"id": "alt-kk-ru", "title": "диалог: KZ-ход, потом RU-вопрос",
     "steps": ["Гель-покрытие қанша тұрады?", "А записаться на завтра к 11:00 можно?"],
     "want_last": "ru"},
    {"id": "cite", "title": "RU-вопрос с казахским словом в кавычках",
     "steps": ["Скажите «рақмет» по-казахски — для проверки"], "want": "ru"},
]


async def main():
    s = get_settings()
    s = dataclasses.replace(s, llm=dataclasses.replace(s.llm, timeout_seconds=90))
    llm = LLMClient(s.llm_api_url, s.llm_api_key, s.llm)
    proc = MessageProcessor(s, llm)
    print(f"бизнес: {s.business_name} | конфиг: {s.config_file} | модель: {s.llm.model}\n")

    total = 0
    for n, sc in enumerate(SCENARIOS):
        cid = 4000 + n
        problems = []
        answers = []
        for text in sc["steps"]:
            upd = FakeUpdate(text, uid=cid, cid=cid)
            try:
                await proc.handle_message(upd, FakeContext())
                a = upd.effective_message.replies[-1] if upd.effective_message.replies else "<пусто>"
            except Exception as e:  # noqa: BLE001
                a = f"ИСКЛЮЧЕНИЕ: {e}"
            answers.append(a)
            print(f"  клиент: {text}")
            print(f"  бот   : {a[:240].replace(chr(10), ' / ')}")
        # проверка чистоты — на каждом шаге
        for i, a in enumerate(answers):
            for p in analyze(a):
                problems.append(f"шаг {i + 1}: {p}")
        want_last = sc.get("want_last") or sc.get("want")
        if want_last == "kk" and not looks_kz(answers[-1]):
            problems.append("ожидался казахский ответ — ответ не казахский")
        if want_last == "ru":
            if not looks_ru(answers[-1]):
                problems.append("ожидался русский ответ — нет русских слов")
            if KZ_LETTERS.search(answers[-1]) and not re.search(r"[«“\"][^»”\"]*[әғқңөұүһі][^»”\"]*[»”\"]", answers[-1], re.I):
                problems.append("в русском ответе казахские буквы вне кавычек")

        total += len(problems)
        print(f"[{'OK' if not problems else 'ПРОБЛЕМЫ'}] {sc['id']} — {sc['title']}")
        for p in problems:
            print(f"    - {p}")
        print()

    print(f"ИТОГО сценариев: {len(SCENARIOS)}, замечаний: {total}")
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())

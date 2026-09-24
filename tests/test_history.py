# Временный тест-скрипт памяти диалога (запускается вручную, в git не включён).

import asyncio
import logging
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.CRITICAL)

from handlers.message_handler import MessageProcessor
from config.settings import get_settings
from services.llm_client import LLMTimeout


# ---------- Фейки ----------
class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, t):
        self.replies.append(t)


class FakeUser:
    def __init__(self, uid=777, username="tester"):
        self.id = uid
        self.username = username
        self.full_name = "Тест Юзер"


class FakeChat:
    def __init__(self, cid=777):
        self.id = cid


class FakeUpdate:
    def __init__(self, text, uid=777, cid=777):
        self.effective_message = FakeMessage(text)
        self.effective_user = FakeUser(uid)
        self.effective_chat = FakeChat(cid)


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()


class StubLLM:
    """Подменяет LLMClient: фиксирует вызовы и раздаёт заготовки ответов."""

    def __init__(self, replies, default="ответ", delay=0.0):
        self.replies = list(replies)
        self.default = default
        self.calls = []
        self.in_call = False
        self.overlap = False
        self._delay = delay

    async def chat(self, system_prompt, user_message, history=None):
        if self.in_call:
            self.overlap = True
        self.in_call = True
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            r = self.replies.pop(0) if self.replies else self.default
            if isinstance(r, Exception):
                raise r
            self.calls.append({"q": user_message, "history": list(history or [])})
            return r
        finally:
            self.in_call = False


SlowStubLLM = lambda replies: StubLLM(replies, delay=0.15)


def make_processor(replies, cls=StubLLM):
    return MessageProcessor(get_settings(), cls(replies))


passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


async def main():
    ctx = FakeContext()

    print("[1] история: базовый сценарий")
    proc1 = make_processor(["Капучино 1200 ₸", "Латте 1300 ₸"])
    await proc1.handle_message(FakeUpdate("Сколько стоит капучино?"), ctx)
    await proc1.handle_message(FakeUpdate("А латте сколько?"), FakeContext())
    calls = proc1.llm.calls
    check("2 вызова LLM", len(calls) == 2)
    check("1-й вызов: история пуста", calls[0]["history"] == [])
    h = calls[1]["history"]
    check("2-й вызов: история = 2 записи", len(h) == 2)
    check("роли user/assistant", [m["role"] for m in h] == ["user", "assistant"])
    check("текст 1-го вопроса в истории", h[0]["content"] == "Сколько стоит капучино?")

    print("[2] HANDOFF не утекает в историю")
    proc2 = make_processor(["[HANDOFF] нет данных в базе"])
    await proc2.handle_message(FakeUpdate("Кто победит в финале ЛЧ?"), ctx)
    h2 = proc2._history_for(777)
    check("в истории 2 записи", len(h2) == 2)
    check("fallback_reply в истории", "Передаю ваш вопрос" in h2[-1]["content"])
    check("[HANDOFF] не в истории", all("[HANDOFF]" not in m["content"] for m in h2))

    print("[3] keyword-fallback: без вызова LLM")
    proc3 = make_processor([])
    await proc3.handle_message(FakeUpdate("Хочу человека"), ctx)
    check("LLM не вызван", len(proc3.llm.calls) == 0)
    check("пара в истории", len(proc3._history_for(777)) == 2)

    print("[4] обрезка длинных сообщений")
    proc4 = make_processor(["ок", "ок"])
    await proc4.handle_message(FakeUpdate("А" * 5000), ctx)
    await proc4.handle_message(FakeUpdate("ещё вопрос"), FakeContext())
    first = proc4.llm.calls[1]["history"][0]["content"]
    check(f"длина обрезана ({len(first)} <= 702)", len(first) <= 702)
    check("многоточие", first.endswith("…"))

    print("[5] LRU-лимит диалогов")
    proc5 = make_processor([])
    for cid in range(600):
        await proc5.handle_message(FakeUpdate("привет", uid=cid, cid=cid), FakeContext())
    check("словарь <= 501", len(proc5._histories) <= 501)
    check("старый чат 0 вытеснен", 0 not in proc5._histories)
    check("новый чат 599 на месте", 599 in proc5._histories)

    print("[6] лок чата: гонка без перекрытий")
    proc6 = make_processor(["от1", "от2"], cls=SlowStubLLM)
    llm6 = proc6.llm
    await asyncio.gather(
        proc6.handle_message(FakeUpdate("первое"), FakeContext()),
        proc6.handle_message(FakeUpdate("второе"), FakeContext()),
    )
    check("нет перекрытия вызовов LLM", not llm6.overlap)
    roles6 = [m["role"] for m in proc6._history_for(777)]
    check("порядок user/assistant/user/assistant", roles6 == ["user", "assistant", "user", "assistant"])

    print("[7] /start сбрасывает историю")
    proc7 = make_processor(["раз"])
    await proc7.handle_message(FakeUpdate("привет"), ctx)
    await proc7.handle_start(FakeUpdate("/start"), FakeContext())
    check("история очищена", len(proc7._histories.get(777, deque())) == 0)

    print("[8] timeout-путь")
    proc8 = make_processor([LLMTimeout("t/o")])
    await proc8.handle_message(FakeUpdate("привет"), ctx)
    h8 = proc8._history_for(777)
    check("timeout_reply в истории", "уточняю" in h8[-1]["content"])

    print("[9] пагинация 4096")
    proc9 = make_processor(["x" * 9000])
    upd = FakeUpdate("дай длинный ответ")
    await proc9.handle_message(upd, FakeContext())
    parts = upd.effective_message.replies
    check(f"разбито на {len(parts)} части", len(parts) == 3)
    check("каждая часть <= 4096", all(len(p) <= 4096 for p in parts))
    check("конкатенация без потерь", "".join(parts) == "x" * 9000)

    print(f"\nИТОГО: passed={passed}, failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())

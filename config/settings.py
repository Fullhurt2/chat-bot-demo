"""Загрузка настроек: .env (секреты, инфраструктура) + конфиг клиента (данные бизнеса).

Порядок применения:
  1. .env          — токены и URL (никогда не хардкодятся в код).
  2. config/client_config*.yaml — всё, что относится к конкретному бизнесу.
Под нового клиента правится только конфиг в config/; какой из них грузить —
переменная CLIENT_CONFIG в .env (по умолчанию client_config.yaml).
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DEFAULT_CONFIG_FILE = "client_config.yaml"

# Подхватываем .env из корня проекта (не хардкодим секреты)
load_dotenv(BASE_DIR / ".env")


def resolve_config_path() -> Path:
    """Путь к конфигу клиента, выбранному переменной CLIENT_CONFIG в .env.

    По умолчанию — config/client_config.yaml. Значение может быть с именем
    файла или без расширения (.yaml подставляется автоматически), чтобы в .env
    можно было писать просто `CLIENT_CONFIG=client_config_nails_atyrau`.
    Берётся только имя файла: конфиг всегда ищется в каталоге config/.
    """
    raw = os.getenv("CLIENT_CONFIG", "").strip() or DEFAULT_CONFIG_FILE
    name = raw if raw.lower().endswith((".yaml", ".yml")) else f"{raw}.yaml"
    if Path(name).name != name:
        logger.warning(
            "CLIENT_CONFIG=%r содержит путь — используется только имя файла %r "
            "(конфиг берётся из каталога config/)",
            raw, Path(name).name,
        )
    return CONFIG_DIR / Path(name).name


@dataclass(frozen=True)
class LLMParams:
    """Параметры вызова LLM — всё настраивается в client_config.yaml."""

    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Settings:
    """Сводные настройки бота: секреты из .env + бизнес-конфиг из YAML."""

    # из .env
    telegram_bot_token: str
    llm_api_url: str
    llm_api_key: str
    # из client_config.yaml
    business_name: str
    tone: str
    language: str
    knowledge_base: str
    owner_chat_id: int | None
    fallback_triggers: list[str] = field(default_factory=list)
    llm: LLMParams = field(default_factory=lambda: LLMParams("", 0.6, 3500, 15))
    # Примеры тёплого/дружеского ответа (few-shot) — необязательное поле.
    style_examples: str = ""
    # Имя загруженного конфига клиента (для логов старта).
    config_file: str = ""

    @property
    def fallback_reply(self) -> str:
        """Сообщение клиенту при передаче человеку."""
        return f"Передаю ваш вопрос команде {self.business_name} — скоро ответят лично. 🙌"

    @property
    def timeout_reply(self) -> str:
        """Сообщение клиенту при таймауте/ошибке LLM."""
        return f"Секунду, уточняю у {self.business_name}… ⏳"


def _load_config() -> tuple[dict, str]:
    """Читает YAML-конфиг клиента; отсутствие файла — фатальная ошибка запуска.

    Возвращает (данные конфига, имя файла).
    """
    path = resolve_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден конфиг клиента: {path}. "
            f"Создайте его по образцу из README или укажите другой файл "
            f"в переменной CLIENT_CONFIG (сейчас: {os.getenv('CLIENT_CONFIG', '') or DEFAULT_CONFIG_FILE})."
        )
    logger.info("Конфиг клиента: %s (выбор через CLIENT_CONFIG)", path.name)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data, path.name


def get_settings() -> Settings:
    """Собирает итоговые настройки из .env и конфига клиента (CLIENT_CONFIG).

    Бросает RuntimeError с понятным описанием, если не хватает обязательных полей.
    """
    cfg, config_file = _load_config()

    model = str((cfg.get("llm") or {}).get("model") or "").strip() or os.getenv("LLM_MODEL", "").strip()
    llm = LLMParams(
        model=model,
        temperature=float((cfg.get("llm") or {}).get("temperature", 0.6)),
        max_tokens=int((cfg.get("llm") or {}).get("max_tokens", 3500)),
        timeout_seconds=int((cfg.get("llm") or {}).get("timeout_seconds", 15)),
        reasoning_effort=(cfg.get("llm") or {}).get("reasoning_effort") or None,
    )

    # owner_telegram_chat_id может отсутствовать — бот всё равно отвечает,
    # но уведомления владельцу будут пропущены с предупреждением в логе.
    # OWNER_CHAT_ID_OVERRIDE из .env перекрывает yaml-значение — так можно
    # тестировать уведомления с отдельного аккаунта, не трогая конфиг клиента.
    owner_override = os.getenv("OWNER_CHAT_ID_OVERRIDE", "").strip()
    owner_raw = owner_override or str(cfg.get("owner_telegram_chat_id") or "").strip()
    owner_chat_id: int | None = None
    if owner_raw:
        try:
            owner_chat_id = int(owner_raw)
            if owner_override:
                logger.info("OWNER_CHAT_ID_OVERRIDE задан — owner chat_id=%s (перекрывает yaml)", owner_chat_id)
        except ValueError:
            if owner_override:
                # Явно заданное значение с опечаткой — ошибка конфигурации, падаем на старте.
                raise RuntimeError(
                    f"OWNER_CHAT_ID_OVERRIDE={owner_override!r} не является целым числом (Telegram chat_id)"
                )
            logger.warning(
                "owner_telegram_chat_id=%r не похож на Telegram chat_id — "
                "уведомления владельцу работать не будут",
                owner_raw,
            )

    settings = Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        llm_api_url=os.getenv("LLM_API_URL", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        business_name=str(cfg.get("business_name") or "").strip(),
        tone=str(cfg.get("tone") or "").strip(),
        language=str(cfg.get("language") or "ru").strip().lower(),
        knowledge_base=str(cfg.get("knowledge_base") or "").strip(),
        owner_chat_id=owner_chat_id,
        fallback_triggers=[str(t).strip() for t in (cfg.get("fallback_triggers") or []) if str(t).strip()],
        llm=llm,
        style_examples=str(cfg.get("style_examples") or "").strip(),
        config_file=config_file,
    )

    # Проверяем обязательные поля до старта, чтобы бот падал сразу с внятной ошибкой.
    missing = [
        name
        for name, value in {
            "TELEGRAM_BOT_TOKEN (.env)": settings.telegram_bot_token,
            "LLM_API_URL (.env)": settings.llm_api_url,
            "LLM_API_KEY (.env)": settings.llm_api_key,
            "business_name (клиентский yaml)": settings.business_name,
            "knowledge_base (клиентский yaml)": settings.knowledge_base,
            "llm.model (.env или клиентский yaml)": settings.llm.model,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Не заданы обязательные настройки: {', '.join(missing)}")

    return settings

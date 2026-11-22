"""Settings come from environment variables, with an optional .env file at the repo root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader so we do not need an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ydc_api_key: str
    llm_provider: str  # "anthropic" or "openai" (any OpenAI-compatible endpoint)
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    mock: bool
    search_count: int
    extraction_mode: str  # "highlights", "full_page" or "none"
    max_queries: int
    max_candidates: int
    research_effort: str  # "lite", "standard", "deep"

    @property
    def ready(self) -> bool:
        return self.mock or bool(self.ydc_api_key and self.llm_api_key and self.llm_model)

    def missing(self) -> list[str]:
        if self.mock:
            return []
        needed = {
            "YDC_API_KEY": self.ydc_api_key,
            "LLM_API_KEY": self.llm_api_key,
            "LLM_MODEL": self.llm_model,
        }
        return [name for name, value in needed.items() if not value]


def get_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    return Settings(
        ydc_api_key=env("YDC_API_KEY", ""),
        llm_provider=env("LLM_PROVIDER", "anthropic").lower(),
        llm_api_key=env("LLM_API_KEY", ""),
        llm_model=env("LLM_MODEL", ""),
        llm_base_url=env("LLM_BASE_URL", "").rstrip("/"),
        mock=_flag("MOCK"),
        search_count=int(env("SEARCH_COUNT", "10")),
        extraction_mode=env("EXTRACTION_MODE", "highlights"),
        max_queries=int(env("MAX_QUERIES", "12")),
        max_candidates=int(env("MAX_CANDIDATES", "48")),
        research_effort=env("RESEARCH_EFFORT", "standard"),
    )

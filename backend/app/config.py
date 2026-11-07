from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    magi_channel_compact_threshold_tokens: int = Field(default=8000, gt=0)
    magi_explicit_prompt_cache: bool = True
    magi_shared_evidence_cache: bool = False
    magi_parallel_verification: bool = False
    magi_parallel_planning: bool = False
    magi_parallel_conflict_resolution: bool = False
    magi_campaign_agent_enabled: bool = False
    magi_database_url: str = "sqlite:///./data/magi.db"
    magi_brand_source_dir: Path = PROJECT_ROOT / "wickhold-candidate-package" / "candidate"
    magi_document_directory: str = "brand"
    magi_image_directory: str = "images"
    magi_brief_directory: str = "briefs"
    magi_retrieval_evaluation_dir: Path = PROJECT_ROOT / "evaluation"
    magi_openai_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    magi_validation_timeout_seconds: float = Field(default=180, gt=0, allow_inf_nan=False)
    magi_retrieval_planner_model: str = "gpt-6-luna"
    magi_conflict_review_model: str = "gpt-6-luna"
    magi_agent_model: str = "gpt-6-luna"
    magi_writer_model: str = "gpt-6-luna"
    openai_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_id: str | None = None
    document_id: str | None = None
    campaign_id: str | None = None
    asset_id: str | None = None
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    source_references: list[dict[str, Any]] = Field(default_factory=list)

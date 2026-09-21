from typing import Any

from pydantic import BaseModel


class BlueprintCreate(BaseModel):
    name: str
    description: str | None = None
    input_type: str = "pdf"          # pdf | image | mixed
    generation_mode: str = "manual"  # manual | prompt
    extraction_prompt_override: str | None = None


class BlueprintUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    input_type: str | None = None
    generation_mode: str | None = None
    extraction_prompt_override: str | None = None
    is_active: bool | None = None


class BlueprintFieldCreate(BaseModel):
    field_key: str
    display_label: str
    field_type: str  # string|number|date|currency|boolean|enum|table
    description: str | None = None
    example_value: str | None = None
    is_required: bool = False
    enum_values: str | None = None
    table_columns: list[dict[str, Any]] | None = None
    sort_order: int = 0


class BlueprintFieldUpdate(BaseModel):
    field_key: str | None = None
    display_label: str | None = None
    field_type: str | None = None
    description: str | None = None
    example_value: str | None = None
    is_required: bool | None = None
    enum_values: str | None = None
    table_columns: list[dict[str, Any]] | None = None
    sort_order: int | None = None


class FieldReorderRequest(BaseModel):
    field_ids: list[str]


class GenerateFieldsRequest(BaseModel):
    prompt: str

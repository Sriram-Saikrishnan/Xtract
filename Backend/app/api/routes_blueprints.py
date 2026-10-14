import uuid
import logging
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from sqlalchemy import func, select, update

from app.database import (
    AsyncSessionLocal, BlueprintFieldORM, BlueprintORM, ExtractionResultORM, ProjectORM, UserORM, db_retry,
)
from app.core.auth import get_current_user
from app.core.gemini_client import (
    build_extraction_model, build_extraction_prompt, extract_with_blueprint,
    generate_fields as gemini_generate_fields, get_mime_type,
)
from app.config import settings
from app.core.observability import set_llm_context
from app.core.thinking import resolve_thinking_budget
from app.models.blueprint import (
    BlueprintFieldCreate, BlueprintFieldUpdate, BlueprintUpdate,
    FieldReorderRequest, GenerateFieldsRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Blueprints"])


def _field_dict(f: BlueprintFieldORM) -> dict:
    return {
        "id": str(f.id),
        "blueprint_id": str(f.blueprint_id),
        "field_key": f.field_key,
        "display_label": f.display_label,
        "field_type": f.field_type,
        "description": f.description,
        "example_value": f.example_value,
        "is_required": f.is_required,
        "enum_values": f.enum_values,
        "table_columns": f.table_columns,
        "sort_order": f.sort_order,
    }


def _blueprint_dict(b: BlueprintORM, fields: Optional[list] = None) -> dict:
    d = {
        "id": str(b.id),
        "project_id": str(b.project_id),
        "name": b.name,
        "description": b.description,
        "input_type": b.input_type,
        "generation_mode": b.generation_mode,
        "extraction_prompt_override": b.extraction_prompt_override,
        "is_active": b.is_active,
        "thinking": bool(b.thinking),
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }
    if fields is not None:
        d["fields"] = [_field_dict(f) for f in fields]
    return d


async def _get_owned_blueprint(session, blueprint_id: str, user_id) -> BlueprintORM:
    blueprint = await session.get(BlueprintORM, uuid.UUID(blueprint_id))
    if not blueprint:
        raise HTTPException(404, "Blueprint not found")
    project = await session.get(ProjectORM, blueprint.project_id)
    if not project or project.user_id != user_id:
        raise HTTPException(404, "Blueprint not found")
    return blueprint


async def _ordered_fields(session, blueprint_id: uuid.UUID) -> list[BlueprintFieldORM]:
    result = await session.execute(
        select(BlueprintFieldORM)
        .where(BlueprintFieldORM.blueprint_id == blueprint_id)
        .order_by(BlueprintFieldORM.sort_order)
    )
    return list(result.scalars().all())


@router.get("/blueprints/{blueprint_id}")
@db_retry()
async def get_blueprint(blueprint_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        fields = await _ordered_fields(session, blueprint.id)
        return _blueprint_dict(blueprint, fields)


@router.patch("/blueprints/{blueprint_id}")
@db_retry()
async def update_blueprint(blueprint_id: str, body: BlueprintUpdate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        updates = body.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(blueprint, key, value)
        await session.commit()
        await session.refresh(blueprint)
        fields = await _ordered_fields(session, blueprint.id)
        return _blueprint_dict(blueprint, fields)


@router.delete("/blueprints/{blueprint_id}")
@db_retry()
async def delete_blueprint(blueprint_id: str, current_user: UserORM = Depends(get_current_user)):
    """
    Hard-deletes a blueprint with no extraction history. A blueprint that
    has ever produced extraction_results is archived (is_active=false)
    instead — deleting it would silently orphan/blank those historical
    rows' field definitions, so it's kept around read-only rather than
    destroyed.
    """
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        count_result = await session.execute(
            select(func.count(ExtractionResultORM.id))
            .where(ExtractionResultORM.blueprint_id == blueprint.id)
        )
        has_results = count_result.scalar_one() > 0

        if has_results:
            blueprint.is_active = False
            await session.commit()
            await session.refresh(blueprint)
            fields = await _ordered_fields(session, blueprint.id)
            return {"archived": blueprint_id, "blueprint": _blueprint_dict(blueprint, fields)}

        await session.delete(blueprint)
        await session.commit()
    return {"deleted": blueprint_id}


@router.post("/blueprints/{blueprint_id}/fields")
@db_retry()
async def create_field(blueprint_id: str, body: BlueprintFieldCreate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        field = BlueprintFieldORM(id=uuid.uuid4(), blueprint_id=blueprint.id, **body.model_dump())
        session.add(field)
        await session.commit()
        await session.refresh(field)
    return _field_dict(field)


@router.patch("/blueprints/{blueprint_id}/fields/{field_id}")
@db_retry()
async def update_field(
    blueprint_id: str, field_id: str, body: BlueprintFieldUpdate,
    current_user: UserORM = Depends(get_current_user),
):
    async with AsyncSessionLocal() as session:
        await _get_owned_blueprint(session, blueprint_id, current_user.id)
        field = await session.get(BlueprintFieldORM, uuid.UUID(field_id))
        if not field or str(field.blueprint_id) != blueprint_id:
            raise HTTPException(404, "Field not found")
        updates = body.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(field, key, value)
        await session.commit()
        await session.refresh(field)
    return _field_dict(field)


@router.delete("/blueprints/{blueprint_id}/fields/{field_id}")
@db_retry()
async def delete_field(blueprint_id: str, field_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        await _get_owned_blueprint(session, blueprint_id, current_user.id)
        field = await session.get(BlueprintFieldORM, uuid.UUID(field_id))
        if not field or str(field.blueprint_id) != blueprint_id:
            raise HTTPException(404, "Field not found")
        await session.delete(field)
        await session.commit()
    return {"deleted": field_id}


@router.patch("/blueprints/{blueprint_id}/fields/reorder")
@db_retry()
async def reorder_fields(blueprint_id: str, body: FieldReorderRequest, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        fields = await _ordered_fields(session, blueprint.id)
        valid_ids = {str(f.id) for f in fields}
        if set(body.field_ids) != valid_ids:
            raise HTTPException(400, "field_ids must contain exactly this blueprint's field ids")
        for order, field_id in enumerate(body.field_ids):
            await session.execute(
                update(BlueprintFieldORM)
                .where(BlueprintFieldORM.id == uuid.UUID(field_id))
                .values(sort_order=order)
            )
        await session.commit()
        fields = await _ordered_fields(session, blueprint.id)
        return [_field_dict(f) for f in fields]


@router.post("/blueprints/{blueprint_id}/generate-fields")
@db_retry()
async def generate_fields_route(blueprint_id: str, body: GenerateFieldsRequest, current_user: UserORM = Depends(get_current_user)):
    """Text-only Gemini call proposing a field list from a prompt. Returned
    for client-side review — nothing is persisted here."""
    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        project = await session.get(ProjectORM, blueprint.project_id)

    set_llm_context(
        operation="generate_fields", user_id=current_user.id,
        project_id=blueprint.project_id, blueprint_id=blueprint.id,
    )
    try:
        fields = await gemini_generate_fields(body.prompt, project.context if project else None)
    except Exception as e:
        logger.error(f"generate-fields failed for blueprint {blueprint_id}: {e}")
        raise HTTPException(502, f"Field generation failed: {e}")

    return {"fields": fields}


def _fields_from_payload(raw_fields: list[dict]) -> list[SimpleNamespace]:
    """Turn client-supplied (possibly unsaved) field dicts into the same
    shape build_extraction_model/build_extraction_prompt expect from ORM rows."""
    out = []
    for f in raw_fields:
        out.append(SimpleNamespace(
            field_key=f["field_key"],
            field_type=f.get("field_type", "string"),
            description=f.get("description"),
            example_value=f.get("example_value"),
            is_required=f.get("is_required", False),
            enum_values=f.get("enum_values"),
            table_columns=f.get("table_columns"),
        ))
    return out


@router.post("/blueprints/{blueprint_id}/test")
async def test_blueprint(
    blueprint_id: str,
    file: UploadFile = File(...),
    fields: Optional[str] = Form(None),  # optional JSON array to test unsaved field edits
    thinking: Optional[bool] = Form(None),  # optional unsaved toggle; defaults to the saved setting
    current_user: UserORM = Depends(get_current_user),
):
    """Runs one extraction against the current (possibly unsaved) field
    list. No DB write — the result is returned inline."""
    import json as _json

    async with AsyncSessionLocal() as session:
        blueprint = await _get_owned_blueprint(session, blueprint_id, current_user.id)
        project = await session.get(ProjectORM, blueprint.project_id)
        saved_fields = await _ordered_fields(session, blueprint.id)

    if fields:
        try:
            field_objs = _fields_from_payload(_json.loads(fields))
        except Exception as e:
            raise HTTPException(400, f"Invalid fields payload: {e}")
    else:
        field_objs = saved_fields

    model_cls = build_extraction_model(str(blueprint.id), field_objs)
    prompt_text = build_extraction_prompt(project, blueprint, field_objs)

    file_bytes = await file.read()
    mime_type = get_mime_type(file.filename)

    set_llm_context(
        operation="test", user_id=current_user.id, project_id=blueprint.project_id,
        blueprint_id=blueprint.id, page_label=file.filename,
    )
    use_thinking = blueprint.thinking if thinking is None else thinking
    data = await extract_with_blueprint(
        file_bytes, file.filename, mime_type, model_cls, prompt_text,
        thinking_budget=resolve_thinking_budget(settings.GEMINI_MODEL, bool(use_thinking)),
    )
    if data is None:
        raise HTTPException(502, "Extraction failed — see server logs for details")

    return {"source_filename": file.filename, "data": data}

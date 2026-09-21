import uuid
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.database import AsyncSessionLocal, BlueprintORM, ProjectORM, UserORM, db_retry
from app.core.auth import get_current_user
from app.models.blueprint import BlueprintCreate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    context: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    context: str | None = None


def _project_dict(p: ProjectORM) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "context": p.context,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _blueprint_dict(b: BlueprintORM) -> dict:
    return {
        "id": str(b.id),
        "project_id": str(b.project_id),
        "name": b.name,
        "description": b.description,
        "input_type": b.input_type,
        "generation_mode": b.generation_mode,
        "extraction_prompt_override": b.extraction_prompt_override,
        "is_active": b.is_active,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


async def _get_owned_project(session, project_id: str, user_id) -> ProjectORM:
    project = await session.get(ProjectORM, uuid.UUID(project_id))
    if not project or project.user_id != user_id:
        raise HTTPException(404, "Project not found")
    return project


@router.post("/projects")
@db_retry()
async def create_project(body: ProjectCreate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        project = ProjectORM(
            id=uuid.uuid4(),
            user_id=current_user.id,
            name=body.name,
            description=body.description,
            context=body.context,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return _project_dict(project)


@router.get("/projects")
@db_retry()
async def list_projects(current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProjectORM)
            .where(ProjectORM.user_id == current_user.id)
            .order_by(ProjectORM.created_at.desc())
        )
        return [_project_dict(p) for p in result.scalars().all()]


@router.get("/projects/{project_id}")
@db_retry()
async def get_project(project_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        project = await _get_owned_project(session, project_id, current_user.id)
        return _project_dict(project)


@router.patch("/projects/{project_id}")
@db_retry()
async def update_project(project_id: str, body: ProjectUpdate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        project = await _get_owned_project(session, project_id, current_user.id)
        updates = body.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(project, key, value)
        await session.commit()
        await session.refresh(project)
        return _project_dict(project)


@router.delete("/projects/{project_id}")
@db_retry()
async def delete_project(project_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        project = await _get_owned_project(session, project_id, current_user.id)
        await session.delete(project)
        await session.commit()
    return {"deleted": project_id}


@router.post("/projects/{project_id}/blueprints")
@db_retry()
async def create_blueprint(project_id: str, body: BlueprintCreate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        await _get_owned_project(session, project_id, current_user.id)
        blueprint = BlueprintORM(
            id=uuid.uuid4(),
            project_id=uuid.UUID(project_id),
            name=body.name,
            description=body.description,
            input_type=body.input_type,
            generation_mode=body.generation_mode,
            extraction_prompt_override=body.extraction_prompt_override,
        )
        session.add(blueprint)
        await session.commit()
        await session.refresh(blueprint)
    return _blueprint_dict(blueprint)


@router.get("/projects/{project_id}/blueprints")
@db_retry()
async def list_blueprints(project_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        await _get_owned_project(session, project_id, current_user.id)
        result = await session.execute(
            select(BlueprintORM)
            .where(BlueprintORM.project_id == uuid.UUID(project_id))
            .order_by(BlueprintORM.created_at.desc())
        )
        return [_blueprint_dict(b) for b in result.scalars().all()]

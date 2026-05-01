import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.database import AsyncSessionLocal, UserORM, db_retry
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    name: str


def _user_dict(user: UserORM) -> dict:
    return {"id": str(user.id), "email": user.email, "name": user.full_name or ""}


@router.post("/signup")
@db_retry()
async def signup(body: SignupRequest):
    if "@" not in body.email or "." not in body.email.split("@")[-1]:
        raise HTTPException(400, "Invalid email address")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(UserORM).where(UserORM.email == body.email.lower().strip())
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "An account with this email already exists")

        user = UserORM(
            id=uuid.uuid4(),
            email=body.email.lower().strip(),
            hashed_password=hash_password(body.password),
            full_name=body.name.strip() or None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), user.email)
    return {"access_token": token, "token_type": "bearer", "user": _user_dict(user)}


@router.post("/login")
@db_retry()
async def login(body: LoginRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserORM).where(UserORM.email == body.email.lower().strip())
        )
        user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")

    token = create_access_token(str(user.id), user.email)
    return {"access_token": token, "token_type": "bearer", "user": _user_dict(user)}


@router.get("/me")
async def me(current_user: UserORM = Depends(get_current_user)):
    return _user_dict(current_user)


@router.patch("/profile")
@db_retry()
async def update_profile(body: ProfileUpdate, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, current_user.id)
        user.full_name = body.name.strip() or None
        await session.commit()
        await session.refresh(user)
    return _user_dict(user)

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from app.config import settings

from app.database import get_db
from app.models.user import User
from app.services.auth import hash_password, verify_password, create_jwt, get_current_user_from_cookie

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterForm(BaseModel):
    email: str
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6)

    @field_validator('password')
    @classmethod
    def password_length(cls, value):
        if len(value.encode('utf-8')) > 72:
            raise ValueError('Пароль должен занимать не более 72 байт UTF-8')
        return value


class LoginForm(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(body: RegisterForm, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # This installation has one owner. Existing installations keep their data.
    if await db.scalar(select(User.id).limit(1)) is not None:
        raise HTTPException(403, "Владелец уже зарегистрирован. Войдите в существующий аккаунт")
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        id=1,
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(403, "Владелец уже зарегистрирован")
    await db.refresh(user)

    token = create_jwt(user.id)
    response.set_cookie("access_token", token, httponly=True,
        secure=request.url.scheme == "https", samesite="strict",
        max_age=settings.jwt_expire_minutes * 60, path="/")
    return {"ok": True, "user_id": user.id}


@router.post("/login")
async def login(body: LoginForm, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_jwt(user.id)
    response.set_cookie("access_token", token, httponly=True,
        secure=request.url.scheme == "https", samesite="strict",
        max_age=settings.jwt_expire_minutes * 60, path="/")
    return {"ok": True, "user_id": user.id}


@router.post("/logout")
async def logout():
    response = Response(headers={"HX-Redirect": "/"})
    response.delete_cookie("access_token", path="/")
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user_from_cookie)):
    return {"id": user.id, "email": user.email, "username": user.username}

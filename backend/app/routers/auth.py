from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr

from app.database import get_db
from app.models.user import User
from app.services.auth import hash_password, verify_password, create_jwt, get_current_user_from_cookie

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterForm(BaseModel):
    email: str
    username: str
    password: str


class LoginForm(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(body: RegisterForm, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_jwt(user.id)
    return {"ok": True, "token": token, "user_id": user.id}


@router.post("/login")
async def login(body: LoginForm, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_jwt(user.id)
    return {"ok": True, "token": token, "user_id": user.id}


@router.post("/logout")
async def logout():
    response = Response()
    response.delete_cookie("access_token", path="/")
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user_from_cookie)):
    return {"id": user.id, "email": user.email, "username": user.username}
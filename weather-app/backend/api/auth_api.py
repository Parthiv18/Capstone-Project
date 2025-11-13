from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import db

router = APIRouter()


class SignupModel(BaseModel):
    username: str
    password: str
    postalcode: str


class LoginModel(BaseModel):
    username: str
    password: str


@router.post("/signup")
def signup(data: SignupModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="username and password required")
    if not data.postalcode or not data.postalcode.strip():
        raise HTTPException(status_code=400, detail="postalcode required for signup")
    ok = db.create_user(data.username, data.password, data.postalcode.strip())
    if not ok:
        raise HTTPException(status_code=400, detail="username already exists")
    return {"ok": True}


@router.post("/login")
def login(data: LoginModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="username and password required")
    ok = db.verify_user(data.username, data.password)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid username or password")
    postal = db.get_user_postal(data.username)
    return {"ok": True, "username": data.username, "postalcode": postal}

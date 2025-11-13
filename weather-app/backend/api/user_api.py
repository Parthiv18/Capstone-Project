from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from database import db

router = APIRouter()


class SaveFileRequest(BaseModel):
    username: str
    file: Optional[str] = None
    text: Optional[str] = None


@router.post("/user/weather")
def save_user_weather(req: SaveFileRequest):
    if not req.username:
        raise HTTPException(status_code=400, detail="username required")

    content = None
    if req.text:
        content = req.text
    elif req.file:
        # read file from server path but restrict to data-files folder
        p = Path(req.file)
        # normalize relative to project: allow only files under api/data-files or parent data-files
        base = Path(__file__).resolve().parent / "data-files"
        try:
            # if an absolute path was provided, ensure it's inside base
            p_resolved = p if p.is_absolute() else (Path(__file__).resolve().parent / p)
            p_resolved = p_resolved.resolve()
            if base.resolve() not in p_resolved.parents and p_resolved.parent.resolve() != base.resolve():
                raise HTTPException(status_code=400, detail="file path not allowed")
            with open(p_resolved, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="file not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="either file or text required")

    ok = db.set_user_weather(req.username, content)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.get("/user/weather")
def get_user_weather(username: str):
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    txt = db.get_user_weather(username)
    if txt is None:
        raise HTTPException(status_code=404, detail="no saved weather for user")
    return {"text": txt}


class SaveHouseRequest(BaseModel):
    username: str
    text: str


@router.post("/user/house")
def save_user_house(req: SaveHouseRequest):
    if not req.username or req.text is None:
        raise HTTPException(status_code=400, detail="username and text required")
    ok = db.set_user_house(req.username, req.text)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.get("/user/house")
def get_user_house(username: str):
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    txt = db.get_user_house(username)
    if txt is None:
        raise HTTPException(status_code=404, detail="no saved house variables for user")
    return {"text": txt}

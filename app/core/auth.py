from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_secret_key
from app.core.db import get_db, hash_password, verify_password

SESSION_COOKIE_NAME = "lab_toolbox_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 天

_serializer = URLSafeTimedSerializer(get_secret_key(), salt="lab-toolbox-auth")

router = APIRouter(tags=["auth"])


def _create_session_token(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def _read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def get_current_user(request: Request) -> dict[str, Any] | None:
    """從 Cookie 讀取目前登入的使用者資料；未登入或無效則回傳 None。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    user_id = _read_session_token(token)
    if user_id is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, role, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row["is_active"]:
        return None
    return dict(row)


def require_user(request: Request) -> dict[str, Any]:
    """FastAPI 相依性：要求已登入，未登入者重導至登入頁面。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """FastAPI 相依性：要求管理員權限。"""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="此操作僅限系統管理員執行")
    return user


@router.get("/login")
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "user": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    templates = request.app.state.templates
    clean_username = username.strip()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, password_hash, role, is_active FROM users WHERE username = ?",
            (clean_username,),
        ).fetchone()

    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "帳號或密碼錯誤，請重新輸入。", "user": None},
            status_code=401,
        )

    if not row["is_active"]:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "此帳號已停用，請聯繫管理員。", "user": None},
            status_code=403,
        )

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_create_session_token(row["id"]),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/admin/users")
def admin_users_page(request: Request, user: dict[str, Any] = Depends(require_admin)):
    templates = request.app.state.templates
    with get_db() as conn:
        users = [
            dict(r)
            for r in conn.execute(
                "SELECT id, username, display_name, role, is_active, created_at FROM users ORDER BY id ASC"
            ).fetchall()
        ]
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={"user": user, "users": users, "msg": msg, "err": err},
    )


@router.post("/admin/users/create")
def admin_create_user(
    user: dict[str, Any] = Depends(require_admin),
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
):
    clean_username = username.strip()
    clean_name = display_name.strip() or clean_username
    if not clean_username or len(password) < 4:
        return RedirectResponse(
            url="/admin/users?err=帳號不可為空，且密碼至少需 4 碼",
            status_code=303,
        )
    if role not in ("user", "admin"):
        role = "user"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (username, display_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (clean_username, clean_name, hash_password(password), role, now),
            )
    except Exception:
        return RedirectResponse(
            url=f"/admin/users?err=帳號「{clean_username}」已存在",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/users?msg=已建立帳號：{clean_name}（{clean_username}）",
        status_code=303,
    )


@router.post("/admin/users/{target_id}/reset-password")
def admin_reset_password(
    target_id: int,
    user: dict[str, Any] = Depends(require_admin),
    new_password: str = Form(...),
):
    if len(new_password) < 4:
        return RedirectResponse(url="/admin/users?err=新密碼至少需 4 碼", status_code=303)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), target_id),
        )
    return RedirectResponse(url="/admin/users?msg=密碼已重設完成", status_code=303)


@router.post("/admin/users/{target_id}/toggle")
def admin_toggle_user(
    target_id: int,
    user: dict[str, Any] = Depends(require_admin),
):
    if target_id == user["id"]:
        return RedirectResponse(url="/admin/users?err=無法停用目前登入中的管理員帳號", status_code=303)
    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM users WHERE id = ?", (target_id,)).fetchone()
        if row:
            new_state = 0 if row["is_active"] else 1
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, target_id))
    return RedirectResponse(url="/admin/users?msg=已更新帳號狀態", status_code=303)

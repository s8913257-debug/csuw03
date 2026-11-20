import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config import CATEGORIES
from app.core.auth import require_user
from app.core.db import get_db
from app.core.file_utils import export_to_csv_bytes, export_to_xlsx_bytes

router = APIRouter(prefix="/history", tags=["history"])


def save_history(
    user: dict[str, Any],
    tool_id: str,
    tool_name: str,
    category: str,
    batch_or_filename: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    summary: str = "",
    note: str = "",
) -> int:
    """
    共用歷史紀錄寫入函式：自動帶入處理人員、處理時間、批號／檔名與工具名稱。
    回傳新建紀錄的 id。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO history_records (
                tool_id, tool_name, category, batch_or_filename,
                note, summary, columns_json, rows_json, row_count,
                user_id, username, user_display_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_id,
                tool_name,
                category,
                batch_or_filename.strip() or "未指定批號",
                note.strip(),
                summary.strip(),
                json.dumps(columns, ensure_ascii=False),
                json.dumps(rows, ensure_ascii=False),
                len(rows),
                user["id"],
                user["username"],
                user["display_name"],
                now,
            ),
        )
        return int(cur.lastrowid)


def get_history_record(record_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM history_records WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["columns"] = json.loads(item["columns_json"])
    item["rows"] = json.loads(item["rows_json"])
    return item


@router.get("")
def history_list_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    q = request.query_params.get("q", "").strip()
    category = request.query_params.get("category", "").strip()
    operator = request.query_params.get("operator", "").strip()
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")

    sql = """
        SELECT id, tool_id, tool_name, category, batch_or_filename,
               note, summary, row_count, user_id, username, user_display_name, created_at
        FROM history_records
        WHERE 1=1
    """
    params: list[Any] = []

    if q:
        sql += " AND (batch_or_filename LIKE ? OR tool_name LIKE ? OR note LIKE ? OR summary LIKE ?)"
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q, like_q])
    if category:
        sql += " AND category = ?"
        params.append(category)
    if operator:
        sql += " AND (user_display_name LIKE ? OR username LIKE ?)"
        like_op = f"%{operator}%"
        params.extend([like_op, like_op])

    sql += " ORDER BY id DESC LIMIT 200"

    with get_db() as conn:
        records = [dict(r) for r in conn.execute(sql, params).fetchall()]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "user": user,
            "records": records,
            "categories": CATEGORIES,
            "q": q,
            "selected_category": category,
            "operator": operator,
            "msg": msg,
            "err": err,
        },
    )


@router.get("/{record_id}")
def history_detail_page(
    record_id: int,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    record = get_history_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="找不到該筆歷史紀錄")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="history_detail.html",
        context={"user": user, "record": record},
    )


@router.get("/{record_id}/download")
def history_download(
    record_id: int,
    fmt: str = "xlsx",
    user: dict[str, Any] = Depends(require_user),
):
    record = get_history_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="找不到該筆歷史紀錄")

    base_name = f"{record['tool_name']}_{record['batch_or_filename']}_{record['id']}"
    safe_base = base_name.replace("/", "_").replace("\\", "_").replace(" ", "_")

    if fmt == "csv":
        content = export_to_csv_bytes(record["columns"], record["rows"])
        media_type = "text/csv; charset=utf-8"
        filename = f"{safe_base}.csv"
    else:
        content = export_to_xlsx_bytes(record["columns"], record["rows"], sheet_name=record["tool_name"])
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{safe_base}.xlsx"

    encoded_name = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@router.post("/{record_id}/delete")
def history_delete(
    record_id: int,
    user: dict[str, Any] = Depends(require_user),
):
    record = get_history_record(record_id)
    if not record:
        return RedirectResponse(url="/history?err=找不到該筆紀錄", status_code=303)

    if user["role"] != "admin" and record["user_id"] != user["id"]:
        return RedirectResponse(url="/history?err=只能刪除自己上傳的紀錄，或請管理員協助刪除", status_code=303)

    with get_db() as conn:
        conn.execute("DELETE FROM history_records WHERE id = ?", (record_id,))

    return RedirectResponse(url="/history?msg=已刪除該筆歷史紀錄", status_code=303)

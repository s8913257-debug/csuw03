"""
【新工具範本檔】
新增每週小工具時，可複製此檔案並改名（例如 app/tools/screening_summary.py），
完成後再到 app/tools/registry.py 匯入並加入 TOOL_REGISTRY。
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.auth import require_user
from app.core.file_utils import parse_tabular_file
from app.core.history import save_history
from app.tools.registry import ToolEntry

TOOL_ID = "example_tool"
TOOL_NAME = "範例工具名稱"
TOOL_CATEGORY = "初篩檢驗"  # 必須是：收樣、初篩檢驗、確認檢驗、品管工具、帳款、藥品管理 之一
TOOL_DESC = "一句話說明這個工具的用途。"

router = APIRouter(prefix=f"/tools/{TOOL_ID}", tags=[TOOL_ID])


@router.get("")
def tool_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tool_example.html",  # 對應 app/templates/ 內的頁面模板
        context={
            "user": user,
            "tool_name": TOOL_NAME,
            "tool_category": TOOL_CATEGORY,
            "tool_desc": TOOL_DESC,
            "result": None,
            "error": None,
        },
    )


@router.post("")
async def tool_process(
    request: Request,
    file: UploadFile = File(...),
    note: str = Form(""),
    save_to_history: bool = Form(True),
    user: dict[str, Any] = Depends(require_user),
):
    templates = request.app.state.templates
    try:
        columns, rows, filename = await parse_tabular_file(
            file,
            required_columns=["檢體編號"],  # 依需求修改必填欄位
        )
        # TODO: 在此撰寫資料整理／計算邏輯
        out_columns = columns
        out_rows = rows
        summary = f"共處理 {len(out_rows)} 筆資料"

        history_id = None
        if save_to_history:
            history_id = save_history(
                user=user,
                tool_id=TOOL_ID,
                tool_name=TOOL_NAME,
                category=TOOL_CATEGORY,
                batch_or_filename=filename,
                columns=out_columns,
                rows=out_rows,
                summary=summary,
                note=note,
            )

        return templates.TemplateResponse(
            request=request,
            name="tool_example.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "result": {
                    "columns": out_columns,
                    "rows": out_rows,
                    "summary": summary,
                    "history_id": history_id,
                    "filename": filename,
                },
                "error": None,
            },
        )
    except ValueError as err:
        return templates.TemplateResponse(
            request=request,
            name="tool_example.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "result": None,
                "error": str(err),
            },
            status_code=400,
        )


TOOL_ENTRY = ToolEntry(
    id=TOOL_ID,
    name=TOOL_NAME,
    category=TOOL_CATEGORY,
    description=TOOL_DESC,
    router=router,
)

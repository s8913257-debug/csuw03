import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from app.core.auth import require_user
from app.core.file_utils import export_to_csv_bytes, export_to_xlsx_bytes, parse_tabular_file
from app.core.history import save_history
from app.tools.registry import ToolEntry

TOOL_ID = "units"
TOOL_NAME = "稀釋與單位換算"
TOOL_CATEGORY = "上課工具"
TOOL_DESC = "上傳上機濃度表，依水樣（µg/L）與土壤／食品（mg/kg）自動統一單位並換算報告濃度。"

REQUIRED_COLUMNS = [
    "樣品編號",
    "樣品類型",
    "元素",
    "上機濃度",
    "單位",
    "稀釋倍數",
    "定容體積 (mL)",
    "樣品重 (g)",
]

OUTPUT_COLUMNS = REQUIRED_COLUMNS + ["報告濃度", "報告單位", "狀態／備註"]

# 支援的單位對照表（忽略大小寫與前後空白，同時相容 µ U+00B5 與 μ U+03BC）
UNIT_FACTORS: dict[str, tuple[Decimal, Decimal]] = {
    # (轉成 µg/L 的倍率, 轉成 mg/L 的倍率)
    "ug/l": (Decimal("1"), Decimal("0.001")),
    "µg/l": (Decimal("1"), Decimal("0.001")),
    "μg/l": (Decimal("1"), Decimal("0.001")),
    "ppb": (Decimal("1"), Decimal("0.001")),
    "ng/ml": (Decimal("1"), Decimal("0.001")),
    "mg/l": (Decimal("1000"), Decimal("1")),
    "ppm": (Decimal("1000"), Decimal("1")),
}

router = APIRouter(prefix=f"/tools/{TOOL_ID}", tags=[TOOL_ID])


def _parse_decimal(text: str) -> Decimal | None:
    if not text:
        return None
    try:
        val = Decimal(text)
    except InvalidOperation:
        return None
    if not val.is_finite():
        return None
    return val


def _convert_row(raw_row: dict[str, str]) -> tuple[dict[str, str], bool]:
    """
    依 tools/units/SPEC.md 換算單一資料列。
    回傳 (輸出列字典, 是否為異常列)。
    """
    cleaned = {col: raw_row.get(col, "").strip() for col in REQUIRED_COLUMNS}
    sample_type = cleaned["樣品類型"]
    conc_str = cleaned["上機濃度"]
    unit_str = cleaned["單位"]
    df_str = cleaned["稀釋倍數"]
    vol_str = cleaned["定容體積 (mL)"]
    weight_str = cleaned["樣品重 (g)"]

    report_unit = "µg/L" if sample_type == "水樣" else ("mg/kg" if sample_type in ("土壤", "食品") else "")

    def error_row(reason: str) -> tuple[dict[str, str], bool]:
        out = dict(cleaned)
        out["報告濃度"] = ""
        out["報告單位"] = report_unit
        out["狀態／備註"] = reason
        return out, True

    if sample_type not in ("水樣", "土壤", "食品"):
        return error_row(f"不支援的樣品類型：{sample_type or '空白'}")

    if not conc_str:
        return error_row("缺少上機濃度")
    conc_val = _parse_decimal(conc_str)
    if conc_val is None:
        return error_row(f"上機濃度格式錯誤：{conc_str}")

    if not unit_str:
        return error_row("缺少單位")
    unit_key = unit_str.lower()
    if unit_key not in UNIT_FACTORS:
        return error_row(f"不支援的單位：{unit_str}")
    to_ug_l, to_mg_l = UNIT_FACTORS[unit_key]

    if not df_str:
        df_val = Decimal("1")
    else:
        df_val = _parse_decimal(df_str)
        if df_val is None or df_val <= 0:
            return error_row(f"稀釋倍數格式錯誤：{df_str}")

    if sample_type == "水樣":
        result = conc_val * to_ug_l * df_val
    else:
        vol_val = _parse_decimal(vol_str)
        weight_val = _parse_decimal(weight_str)
        if vol_val is None or vol_val <= 0 or weight_val is None or weight_val <= 0:
            return error_row("缺少定容體積或樣品重（或數值異常）")
        result = (conc_val * to_mg_l * df_val * vol_val) / weight_val

    rounded = result.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    out = dict(cleaned)
    out["報告濃度"] = f"{rounded:.3f}"
    out["報告單位"] = report_unit
    out["狀態／備註"] = "正常"
    return out, False


@router.get("")
def units_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/units.html",
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
async def units_process(
    request: Request,
    file: UploadFile = File(...),
    note: str = Form(""),
    save_to_history: bool = Form(False),
    user: dict[str, Any] = Depends(require_user),
):
    templates = request.app.state.templates
    try:
        _, rows, filename = await parse_tabular_file(
            file,
            required_columns=REQUIRED_COLUMNS,
        )
        if not rows:
            raise ValueError(f"檔案「{filename}」沒有任何資料列。")

        out_rows: list[dict[str, str]] = []
        error_count = 0
        for row in rows:
            converted, is_err = _convert_row(row)
            out_rows.append(converted)
            if is_err:
                error_count += 1

        total_count = len(out_rows)
        success_count = total_count - error_count
        summary = f"共 {total_count} 筆：成功換算 {success_count} 筆，異常 {error_count} 筆。"

        history_id = None
        if save_to_history:
            history_id = save_history(
                user=user,
                tool_id=TOOL_ID,
                tool_name=TOOL_NAME,
                category=TOOL_CATEGORY,
                batch_or_filename=filename,
                columns=OUTPUT_COLUMNS,
                rows=out_rows,
                summary=summary,
                note=note,
            )

        payload_json = json.dumps(
            {"filename": filename, "rows": out_rows},
            ensure_ascii=False,
        )

        return templates.TemplateResponse(
            request=request,
            name="tools/units.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "result": {
                    "columns": OUTPUT_COLUMNS,
                    "rows": out_rows,
                    "total_count": total_count,
                    "success_count": success_count,
                    "error_count": error_count,
                    "summary": summary,
                    "history_id": history_id,
                    "filename": filename,
                    "payload_json": payload_json,
                },
                "error": None,
            },
        )
    except ValueError as err:
        return templates.TemplateResponse(
            request=request,
            name="tools/units.html",
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


@router.post("/download")
def units_download(
    payload_json: str = Form(...),
    fmt: str = Form("xlsx"),
    user: dict[str, Any] = Depends(require_user),
):
    data = json.loads(payload_json)
    orig_name = data.get("filename", "result").rsplit(".", 1)[0]
    rows = data.get("rows", [])
    safe_base = f"{TOOL_NAME}_{orig_name}".replace("/", "_").replace("\\", "_").replace(" ", "_")

    if fmt == "csv":
        content = export_to_csv_bytes(OUTPUT_COLUMNS, rows)
        media_type = "text/csv; charset=utf-8"
        dl_name = f"{safe_base}.csv"
    else:
        content = export_to_xlsx_bytes(OUTPUT_COLUMNS, rows, sheet_name=TOOL_NAME)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        dl_name = f"{safe_base}.xlsx"

    encoded = quote(dl_name)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


TOOL_ENTRY = ToolEntry(
    id=TOOL_ID,
    name=TOOL_NAME,
    category=TOOL_CATEGORY,
    description=TOOL_DESC,
    router=router,
)

import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import openpyxl
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from app.core.auth import require_user
from app.core.file_utils import export_to_csv_bytes, parse_tabular_file
from app.core.history import save_history
from app.tools import ToolEntry

TOOL_ID = "merge"
TOOL_NAME = "多批次結果彙整"
TOOL_CATEGORY = "上課工具"
TOOL_DESC = "一次上傳多個批次農藥殘留檔，自動對應不同欄位與農藥名稱寫法，產出彙整總表與超標清單。"

DEFAULT_LIMITS: dict[str, dict[str, Any]] = {
    "carbofuran": {"name": "Carbofuran", "limit": 0.02, "loq": 0.01},
    "imidacloprid": {"name": "Imidacloprid", "limit": 0.5, "loq": 0.01},
    "acetamiprid": {"name": "Acetamiprid", "limit": 0.2, "loq": 0.01},
    "chlorpyrifos": {"name": "Chlorpyrifos", "limit": 0.05, "loq": 0.01},
    "fipronil": {"name": "Fipronil", "limit": 0.005, "loq": 0.01},
}

COLUMN_ALIASES = {
    "樣品": ["Sample ID", "SampleID", "樣品", "樣品編號"],
    "基質": ["Matrix", "基質"],
    "農藥": ["Analyte", "化合物", "農藥", "農藥名稱"],
    "濃度 (mg/kg)": ["Calc Conc (mg/kg)", "Conc. mg/kg", "濃度 (mg/kg)"],
}

EXCEEDANCE_COLUMNS = ["批次", "樣品", "基質", "農藥", "濃度 (mg/kg)", "示範容許量"]
SUMMARY_COLUMNS = ["批次", "樣品", "基質", "農藥", "濃度 (mg/kg)", "示範容許量", "LOQ (mg/kg)", "判定結果"]

router = APIRouter(prefix=f"/tools/{TOOL_ID}", tags=[TOOL_ID])


def _extract_batch_code(filename: str) -> str:
    stem = Path(filename).stem
    m = re.search(r"(?:19|20)\d{2}(\d{4})", stem)
    if m:
        return m.group(1)
    return stem


def _resolve_columns(columns: list[str], filename: str) -> dict[str, str]:
    col_map: dict[str, str] = {}
    clean_cols = {c.strip(): c for c in columns}
    for target, aliases in COLUMN_ALIASES.items():
        found = None
        for alias in aliases:
            if alias in clean_cols:
                found = clean_cols[alias]
                break
        if not found:
            raise ValueError(
                f"檔案「{filename}」找不到「{target}」對應欄位（支援：{', '.join(aliases)}）。"
            )
        col_map[target] = found
    return col_map


def _parse_limits_override(columns: list[str], rows: list[dict[str, str]], filename: str) -> dict[str, dict[str, Any]]:
    clean_cols = {c.strip(): c for c in columns}
    analyte_col = next((clean_cols[a] for a in ["Analyte", "農藥", "農藥名稱", "化合物"] if a in clean_cols), None)
    limit_col = next((clean_cols[a] for a in ["示範容許量 (mg/kg)", "示範容許量", "容許量"] if a in clean_cols), None)
    loq_col = next((clean_cols[a] for a in ["LOQ (mg/kg)", "LOQ"] if a in clean_cols), None)

    if not analyte_col or not limit_col:
        raise ValueError(f"自訂容許量表「{filename}」缺少「Analyte／農藥」或「示範容許量」欄位。")

    limits: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get(analyte_col, "").strip()
        if not name:
            continue
        try:
            limit_val = float(row.get(limit_col, "").strip())
        except ValueError as exc:
            raise ValueError(f"自訂容許量表「{name}」的容許量數值格式錯誤。") from exc

        loq_val: float | str = ""
        if loq_col and row.get(loq_col, "").strip():
            try:
                loq_val = float(row.get(loq_col, "").strip())
            except ValueError:
                loq_val = row.get(loq_col, "").strip()

        limits[name.lower()] = {"name": name, "limit": limit_val, "loq": loq_val}
    return limits


def _format_num(val: float | str) -> str:
    if isinstance(val, str):
        return val
    return f"{val:g}"


def _build_two_sheet_xlsx(exceedances: list[dict[str, str]], summary_rows: list[dict[str, str]]) -> bytes:
    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "超標清單"
    ws1.append(EXCEEDANCE_COLUMNS)
    for cell in ws1[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    for row in exceedances:
        ws1.append([row.get(c, "") for c in EXCEEDANCE_COLUMNS])

    ws2 = wb.create_sheet(title="彙整總表")
    ws2.append(SUMMARY_COLUMNS)
    for cell in ws2[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    for row in summary_rows:
        ws2.append([row.get(c, "") for c in SUMMARY_COLUMNS])

    for ws in (ws1, ws2):
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=8)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max(max_len + 4, 10), 40)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@router.get("")
def merge_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/merge.html",
        context={
            "user": user,
            "tool_name": TOOL_NAME,
            "tool_category": TOOL_CATEGORY,
            "tool_desc": TOOL_DESC,
            "default_limits": list(DEFAULT_LIMITS.values()),
            "result": None,
            "error": None,
        },
    )


@router.post("")
async def merge_process(
    request: Request,
    files: list[UploadFile] = File(...),
    limits_file: UploadFile | None = File(None),
    note: str = Form(""),
    save_to_history: bool = Form(False),
    user: dict[str, Any] = Depends(require_user),
):
    templates = request.app.state.templates
    try:
        valid_files = [f for f in files if f and f.filename]
        if not valid_files:
            raise ValueError("請至少選擇一個批次檢測檔案。")

        limits_map = dict(DEFAULT_LIMITS)
        if limits_file and limits_file.filename:
            lim_cols, lim_rows, lim_name = await parse_tabular_file(limits_file)
            limits_map = _parse_limits_override(lim_cols, lim_rows, lim_name)

        summary_rows: list[dict[str, str]] = []
        exceedances: list[dict[str, str]] = []
        batch_names: list[str] = []
        loq_count = 0

        for f in valid_files:
            cols, rows, fname = await parse_tabular_file(f)
            col_map = _resolve_columns(cols, fname)
            batch_code = _extract_batch_code(fname)
            batch_names.append(batch_code)

            for raw in rows:
                sample = raw.get(col_map["樣品"], "").strip()
                matrix = raw.get(col_map["基質"], "").strip()
                analyte_raw = raw.get(col_map["農藥"], "").strip()
                conc_raw = raw.get(col_map["濃度 (mg/kg)"], "").strip()

                if not sample and not analyte_raw:
                    continue

                lim_info = limits_map.get(analyte_raw.lower())
                if lim_info:
                    analyte_std = lim_info["name"]
                    limit_val = lim_info["limit"]
                    limit_str = _format_num(limit_val)
                    loq_str = _format_num(lim_info["loq"])
                else:
                    analyte_std = analyte_raw
                    limit_val = None
                    limit_str = "-"
                    loq_str = "-"

                if conc_raw.upper() == "<LOQ" or conc_raw.startswith("<"):
                    status = "<LOQ"
                    loq_count += 1
                else:
                    try:
                        conc_num = float(conc_raw)
                        if limit_val is None:
                            status = "未定義容許量"
                        elif conc_num > limit_val:
                            status = "超標"
                            exceedances.append(
                                {
                                    "批次": batch_code,
                                    "樣品": sample,
                                    "基質": matrix,
                                    "農藥": analyte_std,
                                    "濃度 (mg/kg)": conc_raw,
                                    "示範容許量": limit_str,
                                }
                            )
                        else:
                            status = "正常"
                    except ValueError:
                        status = "數值格式異常"

                summary_rows.append(
                    {
                        "批次": batch_code,
                        "樣品": sample,
                        "基質": matrix,
                        "農藥": analyte_std,
                        "濃度 (mg/kg)": conc_raw,
                        "示範容許量": limit_str,
                        "LOQ (mg/kg)": loq_str,
                        "判定結果": status,
                    }
                )

        batch_label = "、".join(batch_names)
        summary_text = (
            f"共彙整 {len(valid_files)} 個批次（{batch_label}），"
            f"總計 {len(summary_rows)} 筆檢測資料；<LOQ 共 {loq_count} 筆，超標共 {len(exceedances)} 筆。"
        )

        history_id = None
        if save_to_history:
            history_id = save_history(
                user=user,
                tool_id=TOOL_ID,
                tool_name=TOOL_NAME,
                category=TOOL_CATEGORY,
                batch_or_filename=batch_label,
                columns=SUMMARY_COLUMNS,
                rows=summary_rows,
                summary=summary_text,
                note=note,
            )

        payload_json = json.dumps(
            {
                "batch_label": batch_label,
                "exceedances": exceedances,
                "summary_rows": summary_rows,
            },
            ensure_ascii=False,
        )

        return templates.TemplateResponse(
            request=request,
            name="tools/merge.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "default_limits": list(limits_map.values()),
                "result": {
                    "batch_count": len(valid_files),
                    "total_count": len(summary_rows),
                    "loq_count": loq_count,
                    "exceedance_count": len(exceedances),
                    "summary_text": summary_text,
                    "exceedances": exceedances,
                    "summary_rows": summary_rows,
                    "exceedance_columns": EXCEEDANCE_COLUMNS,
                    "summary_columns": SUMMARY_COLUMNS,
                    "history_id": history_id,
                    "payload_json": payload_json,
                },
                "error": None,
            },
        )
    except ValueError as err:
        return templates.TemplateResponse(
            request=request,
            name="tools/merge.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "default_limits": list(DEFAULT_LIMITS.values()),
                "result": None,
                "error": str(err),
            },
            status_code=400,
        )


@router.post("/download")
def merge_download(
    payload_json: str = Form(...),
    target: str = Form("xlsx"),
    user: dict[str, Any] = Depends(require_user),
):
    data = json.loads(payload_json)
    exceedances = data.get("exceedances", [])
    summary_rows = data.get("summary_rows", [])

    if target == "csv_exceedances":
        content = export_to_csv_bytes(EXCEEDANCE_COLUMNS, exceedances)
        media_type = "text/csv; charset=utf-8"
        dl_name = "多批次彙整_超標清單.csv"
    elif target == "csv_summary":
        content = export_to_csv_bytes(SUMMARY_COLUMNS, summary_rows)
        media_type = "text/csv; charset=utf-8"
        dl_name = "多批次彙整_總表.csv"
    else:
        content = _build_two_sheet_xlsx(exceedances, summary_rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        dl_name = "多批次結果彙整.xlsx"

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

import csv
import io
from pathlib import Path
from typing import Any

import openpyxl
import xlrd
from fastapi import UploadFile


async def parse_tabular_file(
    upload_file: UploadFile,
    required_columns: list[str] | None = None,
) -> tuple[list[str], list[dict[str, str]], str]:
    """
    共用檔案解析函式：支援 .csv (UTF-8 / UTF-8-BOM / Big5 / CP950)、.xlsx、.xls。
    回傳 (columns, rows, original_filename)。
    若格式錯誤或缺少必要欄位，拋出附帶繁體中文說明的 ValueError。
    """
    filename = upload_file.filename or "未命名檔案"
    ext = Path(filename).suffix.lower()
    raw_bytes = await upload_file.read()

    if not raw_bytes:
        raise ValueError(f"檔案「{filename}」是空的，請確認後重新上傳。")

    if ext == ".csv":
        columns, rows = _parse_csv(raw_bytes, filename)
    elif ext == ".xlsx":
        columns, rows = _parse_xlsx(raw_bytes, filename)
    elif ext == ".xls":
        columns, rows = _parse_xls(raw_bytes, filename)
    else:
        raise ValueError(f"不支援的檔案格式「{ext}」，目前僅支援 .csv、.xlsx、.xls。")

    if not columns:
        raise ValueError(f"檔案「{filename}」找不到標題列（欄位名稱），請確認檔案內容。")

    if required_columns:
        missing = [col for col in required_columns if col not in columns]
        if missing:
            raise ValueError(
                f"找不到必要欄位：{', '.join(missing)}。請確認「{filename}」的欄位名稱是否正確。"
            )

    return columns, rows, filename


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_csv(raw_bytes: bytes, filename: str) -> tuple[list[str], list[dict[str, str]]]:
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise ValueError(f"無法辨識「{filename}」的文字編碼，請將 CSV 另存為 UTF-8 或 Big5 後再試。")

    reader = csv.reader(io.StringIO(text))
    all_rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not all_rows:
        return [], []

    columns = [c.strip() for c in all_rows[0]]
    rows: list[dict[str, str]] = []
    for raw_row in all_rows[1:]:
        record = {
            columns[i]: (raw_row[i].strip() if i < len(raw_row) else "")
            for i in range(len(columns))
            if columns[i]
        }
        rows.append(record)
    return [c for c in columns if c], rows


def _parse_xlsx(raw_bytes: bytes, filename: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError(f"讀取 .xlsx 檔案「{filename}」失敗，請確認檔案是否損毀。（{exc}）") from exc

    try:
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    non_empty = [r for r in all_rows if any(cell is not None and str(cell).strip() != "" for cell in r)]
    if not non_empty:
        return [], []

    columns = [_clean_cell(c) for c in non_empty[0]]
    rows: list[dict[str, str]] = []
    for raw_row in non_empty[1:]:
        record = {
            columns[i]: (_clean_cell(raw_row[i]) if i < len(raw_row) else "")
            for i in range(len(columns))
            if columns[i]
        }
        rows.append(record)
    return [c for c in columns if c], rows


def _parse_xls(raw_bytes: bytes, filename: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        wb = xlrd.open_workbook(file_contents=raw_bytes)
    except Exception as exc:
        raise ValueError(f"讀取 .xls 檔案「{filename}」失敗，請確認檔案是否損毀。（{exc}）") from exc

    ws = wb.sheet_by_index(0)
    if ws.nrows == 0:
        return [], []

    non_empty = []
    for r_idx in range(ws.nrows):
        row_vals = [ws.cell_value(r_idx, c_idx) for c_idx in range(ws.ncols)]
        if any(v is not None and str(v).strip() != "" for v in row_vals):
            non_empty.append(row_vals)

    if not non_empty:
        return [], []

    columns = [_clean_cell(c) for c in non_empty[0]]
    rows: list[dict[str, str]] = []
    for raw_row in non_empty[1:]:
        record = {
            columns[i]: (_clean_cell(raw_row[i]) if i < len(raw_row) else "")
            for i in range(len(columns))
            if columns[i]
        }
        rows.append(record)
    return [c for c in columns if c], rows


def export_to_csv_bytes(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    """輸出 UTF-8 with BOM 的 CSV 位元組，確保 Windows Excel 開啟不亂碼。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(col, "") for col in columns])
    return buf.getvalue().encode("utf-8-sig")


def export_to_xlsx_bytes(
    columns: list[str],
    rows: list[dict[str, Any]],
    sheet_name: str = "處理結果",
) -> bytes:
    """輸出格式化的 Excel (.xlsx) 位元組。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]

    ws.append(columns)
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)

    for row in rows:
        ws.append([row.get(col, "") for col in columns])

    for col_cells in ws.columns:
        max_len = 0
        col_letter = col_cells[0].column_letter
        for cell in col_cells:
            val_str = str(cell.value or "")
            max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, 10), 45)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

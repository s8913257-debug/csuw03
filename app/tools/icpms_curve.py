import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from app.core.auth import require_user
from app.core.file_utils import export_to_csv_bytes, export_to_xlsx_bytes
from app.core.history import save_history
from app.tools import ToolEntry

TOOL_ID = "icpms_curve"
TOOL_NAME = "ICP-MS 檢量線計算機"
TOOL_CATEGORY = "上課工具"
TOOL_DESC = "支援不加權與 1/x、1/x² 加權線性迴歸，自動計算 R²、標準品回算誤差 (%RE)、繪製檢量線並反算未知樣品濃度。"

WEIGHTING_LABELS = {
    "none": "標準線性迴歸（不加權）",
    "1/x": "加權線性迴歸（1/x）",
    "1/x2": "加權線性迴歸（1/x²）",
}

router = APIRouter(prefix=f"/tools/{TOOL_ID}", tags=[TOOL_ID])


def _fit_linear_regression(
    standards: list[tuple[float, float]],
    weighting: str,
) -> dict[str, Any]:
    if len(standards) < 2:
        raise ValueError("檢量線至少需要 2 個標準品點。")

    weights: list[float] = []
    for x, _ in standards:
        if weighting == "none":
            weights.append(1.0)
        elif weighting == "1/x":
            if x <= 0:
                raise ValueError("使用 1/x 加權迴歸時，標準品濃度不可小於或等於 0（請移除空白點或改用不加權）。")
            weights.append(1.0 / x)
        elif weighting == "1/x2":
            if x <= 0:
                raise ValueError("使用 1/x² 加權迴歸時，標準品濃度不可小於或等於 0（請移除空白點或改用不加權）。")
            weights.append(1.0 / (x * x))
        else:
            raise ValueError(f"不支援的加權方式：{weighting}")

    sum_w = sum(weights)
    mean_x = sum(w * x for (x, _), w in zip(standards, weights)) / sum_w
    mean_y = sum(w * y for (_, y), w in zip(standards, weights)) / sum_w

    sxx = sum(w * (x - mean_x) ** 2 for (x, _), w in zip(standards, weights))
    sxy = sum(w * (x - mean_x) * (y - mean_y) for (x, y), w in zip(standards, weights))
    syy = sum(w * (y - mean_y) ** 2 for (_, y), w in zip(standards, weights))

    if sxx == 0:
        raise ValueError("標準品設定濃度不可全部相同，無法建立檢量線。")

    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    if slope == 0:
        raise ValueError("迴歸斜率為 0，無法進行濃度反算，請確認標準品訊號是否正確。")

    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 1.0

    std_results = []
    for idx, (x, y) in enumerate(standards, start=1):
        calc_x = (y - intercept) / slope
        if x != 0:
            re_pct = ((calc_x - x) / x) * 100.0
            re_str = f"{re_pct:+.2f}%"
        else:
            re_str = "-"
        std_results.append(
            {
                "點位": f"Std-{idx}",
                "設定濃度": f"{x:g}",
                "儀器訊號": f"{y:g}",
                "回算濃度": f"{calc_x:.4f}",
                "相對誤差 (%RE)": re_str,
                "x_val": x,
                "y_val": y,
            }
        )

    return {
        "slope": slope,
        "intercept": intercept,
        "r2": r2,
        "std_results": std_results,
    }


def _build_svg_chart(
    standards: list[tuple[float, float]],
    unknowns: list[tuple[str, float, float]],
    slope: float,
    intercept: float,
    unit: str,
) -> dict[str, Any]:
    """產生內嵌 SVG 檢量線圖表座標（寬 640、高 340）。"""
    width, height = 640, 340
    pad_l, pad_r, pad_t, pad_b = 72, 28, 24, 48
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    all_x = [x for x, _ in standards] + [conc for _, _, conc in unknowns]
    all_y = [y for _, y in standards] + [sig for _, sig, _ in unknowns]

    min_x = min(0.0, min(all_x))
    max_x = max(all_x) * 1.08 if max(all_x) > 0 else 1.0
    min_y = min(0.0, min(all_y))
    max_y = max(all_y) * 1.08 if max(all_y) > 0 else 1.0

    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0

    def sx(val: float) -> float:
        return pad_l + ((val - min_x) / span_x) * plot_w

    def sy(val: float) -> float:
        return pad_t + plot_h - ((val - min_y) / span_y) * plot_h

    x1, x2 = min_x, max_x
    y1, y2 = slope * x1 + intercept, slope * x2 + intercept

    x_ticks = [
        {"pos": sx(min_x + span_x * i / 4), "label": f"{min_x + span_x * i / 4:.3g}"}
        for i in range(5)
    ]
    y_ticks = [
        {"pos": sy(min_y + span_y * i / 4), "label": f"{min_y + span_y * i / 4:.3g}"}
        for i in range(5)
    ]

    std_points = [{"cx": sx(x), "cy": sy(y), "label": f"({x:g}, {y:g})"} for x, y in standards]
    unk_points = [
        {"cx": sx(conc), "cy": sy(sig), "label": f"{sid}: {conc:.3f}"}
        for sid, sig, conc in unknowns
    ]

    return {
        "width": width,
        "height": height,
        "pad_l": pad_l,
        "pad_r": pad_r,
        "pad_t": pad_t,
        "pad_b": pad_b,
        "plot_w": plot_w,
        "plot_h": plot_h,
        "line": {"x1": sx(x1), "y1": sy(y1), "x2": sx(x2), "y2": sy(y2)},
        "x_ticks": x_ticks,
        "y_ticks": y_ticks,
        "std_points": std_points,
        "unk_points": unk_points,
        "unit": unit,
    }


@router.get("")
def icpms_curve_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/icpms_curve.html",
        context={
            "user": user,
            "tool_name": TOOL_NAME,
            "tool_category": TOOL_CATEGORY,
            "tool_desc": TOOL_DESC,
            "weighting_labels": WEIGHTING_LABELS,
            "form_state": {
                "batch_name": "",
                "unit": "µg/L",
                "weighting": "none",
                "r2_threshold": "0.995",
                "std_rows": [
                    {"conc": "", "signal": ""},
                    {"conc": "", "signal": ""},
                    {"conc": "", "signal": ""},
                    {"conc": "", "signal": ""},
                    {"conc": "", "signal": ""},
                ],
                "unk_rows": [
                    {"sample_id": "", "signal": "", "df": "1"},
                    {"sample_id": "", "signal": "", "df": "1"},
                ],
                "note": "",
            },
            "result": None,
            "error": None,
        },
    )


@router.post("")
def icpms_curve_calculate(
    request: Request,
    batch_name: str = Form(""),
    unit: str = Form("µg/L"),
    weighting: str = Form("none"),
    r2_threshold: str = Form("0.995"),
    std_conc: list[str] = Form([]),
    std_signal: list[str] = Form([]),
    unk_id: list[str] = Form([]),
    unk_signal: list[str] = Form([]),
    unk_df: list[str] = Form([]),
    note: str = Form(""),
    save_to_history: bool = Form(False),
    user: dict[str, Any] = Depends(require_user),
):
    templates = request.app.state.templates

    std_rows_state = [
        {"conc": c.strip(), "signal": s.strip()}
        for c, s in zip(std_conc, std_signal)
    ]
    unk_rows_state = [
        {"sample_id": sid.strip(), "signal": sig.strip(), "df": (df.strip() or "1")}
        for sid, sig, df in zip(unk_id, unk_signal, unk_df)
    ]

    form_state = {
        "batch_name": batch_name.strip(),
        "unit": unit.strip() or "µg/L",
        "weighting": weighting,
        "r2_threshold": r2_threshold.strip() or "0.995",
        "std_rows": std_rows_state or [{"conc": "", "signal": ""}],
        "unk_rows": unk_rows_state or [{"sample_id": "", "signal": "", "df": "1"}],
        "note": note.strip(),
    }

    try:
        try:
            threshold_val = float(form_state["r2_threshold"])
        except ValueError as exc:
            raise ValueError("R² 合格門檻請輸入有效數字（例如 0.995）。") from exc

        standards: list[tuple[float, float]] = []
        for idx, row in enumerate(std_rows_state, start=1):
            if not row["conc"] and not row["signal"]:
                continue
            if not row["conc"] or not row["signal"]:
                raise ValueError(f"標準品第 {idx} 列的「設定濃度」與「儀器訊號」必須同時填寫。")
            try:
                x = float(row["conc"])
                y = float(row["signal"])
            except ValueError as exc:
                raise ValueError(f"標準品第 {idx} 列包含非數字內容，請檢查後重試。") from exc
            standards.append((x, y))

        fit = _fit_linear_regression(standards, weighting)
        slope = fit["slope"]
        intercept = fit["intercept"]
        r2 = fit["r2"]
        passed = r2 >= threshold_val

        unk_results: list[dict[str, str]] = []
        unk_plot_points: list[tuple[str, float, float]] = []
        for idx, row in enumerate(unk_rows_state, start=1):
            if not row["sample_id"] and not row["signal"]:
                continue
            if not row["signal"]:
                raise ValueError(f"未知樣品第 {idx} 列請填寫「儀器訊號」。")
            sample_name = row["sample_id"] or f"樣品-{idx}"
            try:
                sig = float(row["signal"])
                df_val = float(row["df"] or "1")
            except ValueError as exc:
                raise ValueError(f"未知樣品「{sample_name}」的訊號或稀釋倍數格式錯誤。") from exc
            if df_val <= 0:
                raise ValueError(f"未知樣品「{sample_name}」的稀釋倍數必須大於 0。")

            raw_conc = (sig - intercept) / slope
            final_conc = raw_conc * df_val
            unk_plot_points.append((sample_name, sig, raw_conc))
            unk_results.append(
                {
                    "類別": "未知樣品",
                    "編號": sample_name,
                    "儀器訊號": f"{sig:g}",
                    "稀釋倍數": f"{df_val:g}",
                    f"上機回算濃度 ({form_state['unit']})": f"{raw_conc:.4f}",
                    f"最終濃度 ({form_state['unit']})": f"{final_conc:.4f}",
                }
            )

        sign = "+" if intercept >= 0 else "-"
        equation = f"y = {slope:.4f}x {sign} {abs(intercept):.4f}"
        status_text = "合格" if passed else "不合格"
        summary = (
            f"{WEIGHTING_LABELS[weighting]}｜{equation}｜R² = {r2:.6f}（門檻 ≥ {threshold_val}：{status_text}）"
            f"｜標準品 {len(standards)} 點，未知樣品 {len(unk_results)} 筆"
        )

        export_columns = [
            "類別",
            "編號",
            f"設定濃度 ({form_state['unit']})",
            "儀器訊號",
            "稀釋倍數",
            f"回算／上機濃度 ({form_state['unit']})",
            f"最終濃度／%RE",
        ]
        export_rows: list[dict[str, str]] = []
        for s in fit["std_results"]:
            export_rows.append(
                {
                    "類別": "標準品",
                    "編號": s["點位"],
                    f"設定濃度 ({form_state['unit']})": s["設定濃度"],
                    "儀器訊號": s["儀器訊號"],
                    "稀釋倍數": "1",
                    f"回算／上機濃度 ({form_state['unit']})": s["回算濃度"],
                    "最終濃度／%RE": s["相對誤差 (%RE)"],
                }
            )
        final_col = f"最終濃度 ({form_state['unit']})"
        raw_col = f"上機回算濃度 ({form_state['unit']})"
        for u in unk_results:
            export_rows.append(
                {
                    "類別": "未知樣品",
                    "編號": u["編號"],
                    f"設定濃度 ({form_state['unit']})": "-",
                    "儀器訊號": u["儀器訊號"],
                    "稀釋倍數": u["稀釋倍數"],
                    f"回算／上機濃度 ({form_state['unit']})": u[raw_col],
                    "最終濃度／%RE": f"{u[final_col]} {form_state['unit']}",
                }
            )

        batch_label = form_state["batch_name"] or "ICP-MS檢量線"
        history_id = None
        if save_to_history:
            history_id = save_history(
                user=user,
                tool_id=TOOL_ID,
                tool_name=TOOL_NAME,
                category=TOOL_CATEGORY,
                batch_or_filename=batch_label,
                columns=export_columns,
                rows=export_rows,
                summary=summary,
                note=form_state["note"],
            )

        svg = _build_svg_chart(standards, unk_plot_points, slope, intercept, form_state["unit"])
        payload_json = json.dumps(
            {"batch": batch_label, "columns": export_columns, "rows": export_rows},
            ensure_ascii=False,
        )

        return templates.TemplateResponse(
            request=request,
            name="tools/icpms_curve.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "weighting_labels": WEIGHTING_LABELS,
                "form_state": form_state,
                "result": {
                    "equation": equation,
                    "slope": f"{slope:.6f}",
                    "intercept": f"{intercept:.6f}",
                    "r2": f"{r2:.6f}",
                    "threshold": f"{threshold_val}",
                    "passed": passed,
                    "status_text": status_text,
                    "weighting_label": WEIGHTING_LABELS[weighting],
                    "std_results": fit["std_results"],
                    "unk_results": unk_results,
                    "unit": form_state["unit"],
                    "svg": svg,
                    "history_id": history_id,
                    "payload_json": payload_json,
                },
                "error": None,
            },
        )
    except ValueError as err:
        return templates.TemplateResponse(
            request=request,
            name="tools/icpms_curve.html",
            context={
                "user": user,
                "tool_name": TOOL_NAME,
                "tool_category": TOOL_CATEGORY,
                "tool_desc": TOOL_DESC,
                "weighting_labels": WEIGHTING_LABELS,
                "form_state": form_state,
                "result": None,
                "error": str(err),
            },
            status_code=400,
        )


@router.post("/download")
def icpms_curve_download(
    payload_json: str = Form(...),
    fmt: str = Form("xlsx"),
    user: dict[str, Any] = Depends(require_user),
):
    data = json.loads(payload_json)
    batch = data.get("batch", "ICP-MS檢量線")
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    safe_base = f"{TOOL_NAME}_{batch}".replace("/", "_").replace("\\", "_").replace(" ", "_")

    if fmt == "csv":
        content = export_to_csv_bytes(columns, rows)
        media_type = "text/csv; charset=utf-8"
        dl_name = f"{safe_base}.csv"
    else:
        content = export_to_xlsx_bytes(columns, rows, sheet_name="檢量線結果")
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

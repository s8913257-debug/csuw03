from typing import Any

from fastapi import FastAPI

from app.config import CATEGORIES
from app.tools import ToolEntry
from app.tools.icpms_curve import TOOL_ENTRY as icpms_curve_tool
from app.tools.merge import TOOL_ENTRY as merge_tool
from app.tools.units import TOOL_ENTRY as units_tool


# =====================================================================
# 【工具註冊清單】
# 之後每週新增小工具時：
#   1. 在 app/tools/ 新增工具檔案（可複製 _template.py）
#   2. 在下方匯入該工具的 TOOL_ENTRY
#   3. 加入 TOOL_REGISTRY 清單即可
# =====================================================================

TOOL_REGISTRY: list[ToolEntry] = [
    units_tool,
    icpms_curve_tool,
    merge_tool,
]


def validate_registry() -> None:
    seen_ids: set[str] = set()
    for tool in TOOL_REGISTRY:
        if tool.category not in CATEGORIES:
            raise ValueError(
                f"工具「{tool.name}」的分類「{tool.category}」不在規範的六大分類中：{CATEGORIES}"
            )
        if tool.id in seen_ids:
            raise ValueError(f"工具 ID 重複：{tool.id}")
        seen_ids.add(tool.id)


def get_tools_by_category() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat in CATEGORIES}
    for tool in TOOL_REGISTRY:
        grouped[tool.category].append(
            {
                "id": tool.id,
                "name": tool.name,
                "category": tool.category,
                "description": tool.description,
                "url": tool.url,
            }
        )
    return grouped


def mount_tools(app: FastAPI) -> None:
    validate_registry()
    for tool in TOOL_REGISTRY:
        app.include_router(tool.router)

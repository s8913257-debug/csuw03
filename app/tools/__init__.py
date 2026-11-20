from dataclasses import dataclass

from fastapi import APIRouter


@dataclass
class ToolEntry:
    id: str
    name: str
    category: str
    description: str
    router: APIRouter

    @property
    def url(self) -> str:
        return f"/tools/{self.id}"

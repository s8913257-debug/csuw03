from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_TITLE, CATEGORIES
from app.core import auth, history
from app.core.auth import require_user
from app.core.db import init_db
from app.tools.registry import get_tools_by_category, mount_tools

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_TITLE, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["app_title"] = APP_TITLE
templates.env.globals["categories"] = CATEGORIES
templates.env.globals["tools_by_category"] = get_tools_by_category()
app.state.templates = templates

app.include_router(auth.router)
app.include_router(history.router)
mount_tools(app)


@app.get("/")
def index_page(request: Request, user: dict[str, Any] = Depends(require_user)):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user},
    )

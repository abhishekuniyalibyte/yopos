"""
FastAPI application.

    uvicorn server.app:app --reload

The Anthropic key lives here and is never sent to the browser. The widget talks only to
this service.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from .agent import Assistant, AgentError
from .menu import Menu, MenuError
from .sessions import Session, SessionStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yopos")

state: dict[str, Any] = {}

# Without this, browsers may heuristically cache widget.js for hours and keep running an
# old copy after a deploy — the tiles shipped and a customer's tab never saw them.
# no-cache still allows the cached copy; it just revalidates against the ETag first.
NO_CACHE = {"cache-control": "no-cache"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        menu = Menu.load(config.MENU_PATH)
    except MenuError as exc:
        raise RuntimeError(f"Cannot start: {exc}") from exc

    state["menu"] = menu
    state["sessions"] = SessionStore(menu)
    state["assistant"] = Assistant(menu)

    log.info("loaded %d items, %d need required choices", len(menu), menu.customisable_count)
    if not config.GROQ_API_KEYS:
        log.warning("No Groq API key is set — /api/chat will return 503")
    else:
        log.info("%d Groq API key(s) loaded", len(config.GROQ_API_KEYS))
    if config.ALLOW_NUTRITION_INFERENCE:
        log.warning(
            "ALLOW_NUTRITION_INFERENCE is on. The menu has no nutrition data, so any "
            "figures the assistant gives are fabricated."
        )
    yield
    state.clear()


app = FastAPI(title="YOPOS AI Assistant", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ToolTraceOut(BaseModel):
    name: str
    ok: bool


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    cart: dict[str, Any]
    tools: list[ToolTraceOut] = []
    items: list[dict[str, Any]] = []  # menu items to show as tiles under the reply


def get_session(request: ChatRequest) -> Session:
    store: SessionStore = state["sessions"]
    session = store.get_or_create(request.session_id)
    if session.rate_limited():
        raise HTTPException(429, "Too many messages. Please slow down.")
    return session


@app.get("/api/health")
async def health() -> dict[str, Any]:
    menu: Menu = state["menu"]
    return {
        "status": "ok",
        "items": len(menu),
        "sessions": len(state["sessions"]),
        "model": config.MODEL,
        "api_key_configured": bool(config.GROQ_API_KEYS),
        "keys": state["assistant"].keys.status(),
        "nutrition_inference": config.ALLOW_NUTRITION_INFERENCE,
    }


@app.get("/api/menu")
async def get_menu() -> dict[str, Any]:
    """The menu as the widget renders it. No nutrition fields exist — see clean_menu.py."""
    menu: Menu = state["menu"]
    return {
        "store": menu.store,
        "categories": menu.categories,
        "item_count": len(menu),
        "items": menu.prompt_payload(),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, session: Session = Depends(get_session)) -> ChatResponse:
    assistant: Assistant = state["assistant"]
    try:
        result = await assistant.respond(request.message, session.history, session.cart)
    except AgentError as exc:
        raise HTTPException(503, str(exc)) from exc

    return ChatResponse(
        reply=result.reply,
        session_id=session.session_id,
        cart=result.cart,
        tools=[ToolTraceOut(name=t.name, ok=t.ok) for t in result.tools],
        items=result.items,
    )


@app.get("/api/cart/{session_id}")
async def get_cart(session_id: str) -> dict[str, Any]:
    store: SessionStore = state["sessions"]
    session = store.get_or_create(session_id)
    return session.cart.state()


@app.post("/api/session/{session_id}/reset")
async def reset(session_id: str) -> dict[str, str]:
    state["sessions"].drop(session_id)
    return {"status": "reset"}


@app.get("/")
async def storefront() -> FileResponse:
    """
    Demo storefront, so the widget can be seen in a realistic page without a second
    server. Serving it from the same origin as the API also sidesteps CORS.
    """
    path = config.BASE_DIR / "widget" / "index.html"
    if not path.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(path, media_type="text/html", headers=NO_CACHE)


@app.get("/widget.js")
async def widget(request: Request) -> FileResponse:
    """Serve the embeddable widget so a site can include it with one script tag."""
    path = config.BASE_DIR / "widget" / "widget.js"
    if not path.exists():
        raise HTTPException(404, "widget.js not found")
    return FileResponse(path, media_type="application/javascript", headers=NO_CACHE)

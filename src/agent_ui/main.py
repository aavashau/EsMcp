import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from mcp_phi.config import settings

from .agent import run_turn
from .mcp_client import get_server_params
from .session import InMemorySessionStore

logging.basicConfig(level=settings.log_level)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
session_store = InMemorySessionStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(get_server_params()))
        mcp_session = await stack.enter_async_context(ClientSession(read, write))
        await mcp_session.initialize()
        mcp_tools = await mcp_session.list_tools()
        app.state.mcp_session = mcp_session
        app.state.mcp_tools = mcp_tools
        yield


app = FastAPI(title="Healthcare Agent", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="base.html",
        context={"session_id": str(uuid.uuid4())},
    )


@app.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...),
):
    history = session_store.get(session_id)
    history.append({"role": "user", "content": message})

    assistant_text, tool_records = await run_turn(
        mcp_session=request.app.state.mcp_session,
        mcp_tools=request.app.state.mcp_tools,
        history=history,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )

    return templates.TemplateResponse(
        request=request,
        name="_message.html",
        context={
            "user_message": message,
            "assistant_text": assistant_text,
            "tool_calls": tool_records,
        },
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def main() -> None:
    import uvicorn
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-..."):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.\n"
            "Get a key at https://platform.openai.com/api-keys"
        )
    uvicorn.run("agent_ui.main:app", host="0.0.0.0", port=8000, reload=False)

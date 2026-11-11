"""MatDev の API サーバー。画面と、ジャービスとの会話の受け渡しを持つ。"""
from __future__ import annotations

import asyncio
import functools
import json
import sqlite3
from contextlib import asynccontextmanager

import jsonschema
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi import Path as PathParam
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth, hub, imd, materials, notes, preferences, settings, speech, usage, views, watcher, workbench, works
from . import config as configuration
from . import db as database
from .agent import Agent
from .redact import scrub
from .auth import CurrentUser, current_user
from .db import get_db
from .llm import make_llm
from .tools import ToolFailed, Toolbox

USERNAME_PATTERN = r"^[A-Za-z0-9_.\-]{3,32}$"
JOB_ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
HEARTBEAT_SECONDS = 15
Db = Depends(get_db, scope="function")
client_factory = None   # 試験では LLM の通信の相手を差し替える (None なら本物)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    app.state.loop = asyncio.get_running_loop()   # 同期の API からジャービスの作業を始めるために持つ
    config = configuration.load()   # つなぐアプリと LLM (matdev.json。なければ MatSim と Claude)
    app.state.config = config
    app.state.toolbox = Toolbox(config.apps)
    app.state.llm = make_llm(config.llm, client_factory)
    app.state.agent = Agent(app.state.toolbox, app.state.llm, config)
    app.state.speech = speech.Transcriber(config.speech)
    db = database.connect()
    try:
        _rename(app, preferences.assistant_name(db))
    finally:
        db.close()
    app.state.speech.preload()
    await app.state.toolbox.start()
    watch = asyncio.create_task(watcher.run(app.state.toolbox, app.state.agent), name="job-watcher")
    yield
    watch.cancel()
    await app.state.agent.shutdown()
    await app.state.toolbox.stop()


def _rename(app: FastAPI, name: str) -> None:
    app.state.agent.rename(name)
    app.state.speech.extra_words = [name]


app = FastAPI(title="MatDev", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # 応答が自分で決めたもの (Plotly の図を封じて見せる入口) はそのまま
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return response


class Credentials(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=10, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    voice: bool = False   # 声で入力した (聞き間違いと読み上げに向けた注意を LLM に添える)
    viewing: list[str] = Field(default_factory=list, max_length=12)   # 画面で並べて見ているウインドウ (LLM に添える)


class PreferencesIn(BaseModel):
    assistant_name: str = Field(min_length=1, max_length=preferences.NAME_MAX)


class WorkbenchIn(BaseModel):
    action: str = Field(max_length=40)
    args: dict = Field(default_factory=dict)
    conversation_id: int | None = None   # 保存と投入は会話に残す (確認のカード・新しい構造のカード・見張り)
    label: str = Field("", max_length=60)   # 会話に残す文に使う操作の名前 (スーパーセルなど)


class NewConversationIn(BaseModel):
    work_id: int   # 計算の結果の出力先 (作業)。会話を始める前に必ず決める


class ToolRunIn(BaseModel):
    name: str = Field(max_length=80)   # 道具の名前 (アプリ名__道具名、またはノート・作業の道具)
    args: dict = Field(default_factory=dict)


class WorkIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    path: str = Field("", max_length=500)   # 空なら作業の場所 (works.roots の最初) の下に名前で作る


class ActiveWorkIn(BaseModel):
    work_id: int | None


class SaveToWorkIn(BaseModel):
    conversation_id: int | None = None
    app: str = Field(max_length=40)
    kind: str = Field(pattern="^(job|structure)$")
    id: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")


class ConfirmIn(BaseModel):
    approve: bool


def _conversation(db: sqlite3.Connection, conversation_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "会話が見つかりません")
    return row


@app.get("/api/status")
def status(request: Request, db: sqlite3.Connection = Db):
    user = auth.user_from_cookie(db, request)
    data = {"needs_setup": db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0,
            "user": user.username if user else None, "version": __version__}
    if user:
        state = request.app.state
        llm = state.llm
        data |= {"llm_ready": llm.ready(), "llm": llm.describe(), "llm_key_env": llm.config.api_key_env,
                 "model": llm.config.model, "limit_usd": settings.MONTHLY_LIMIT_USD,
                 "apps": [{"name": name, "tools": len(app.tools), "error": app.error,
                           "graphs": views.has_graphs(app), "link": bool((app.config.view or {}).get("link")),
                           "structures": views.has_structures(app), "workbench": workbench.available(app),
                           "export": sorted(k for k in ("job", "structure") if (app.config.export or {}).get(k))}
                          for name, app in state.toolbox.apps.items()],
                 "tools": len(state.toolbox.qualified()), "auto_run_minutes": settings.AUTO_RUN_SECONDS / 60,
                 "assistant_name": state.agent.name, "work": works.active(db),
                 "work_roots": [str(r) for r in works.roots(state.config)],
                 "speech": {"ready": state.speech.ready(), "name": state.speech.describe(), "error": state.speech.error}}
    return data


@app.post("/api/auth/setup")
def setup(body: Credentials, request: Request, response: Response, db: sqlite3.Connection = Db):
    """最初のアカウントを作る。2 つ目は作れない。"""
    auth.require_csrf(request)
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        raise HTTPException(403, "アカウントはすでにあります")
    cursor = db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (body.username, auth.hash_password(body.password)))
    auth.start_session(db, response, cursor.lastrowid)
    return {"username": body.username}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response, db: sqlite3.Connection = Db):
    auth.require_csrf(request)
    auth.check_locked(body.username)
    row = db.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    valid = auth.verify_password(body.password, row["password_hash"] if row else auth.DUMMY_HASH)
    if row is None or not valid:
        auth.record_failure(body.username)
        raise HTTPException(401, "ユーザー名かパスワードが違います")
    auth.clear_failures(body.username)
    auth.start_session(db, response, row["id"])
    return {"username": body.username}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    auth.end_session(db, request, response)
    return {"ok": True}


@app.get("/api/conversations")
def conversations(user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    rows = db.execute("SELECT id, title, updated_at, work_id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 100")
    return [dict(row) for row in rows.fetchall()]


@app.post("/api/conversations")
def create_conversation(body: NewConversationIn, user: CurrentUser = Depends(current_user),
                        db: sqlite3.Connection = Db):
    """会話を始める。計算の結果の出力先 (作業) は必ず決める。"""
    _work(db, body.work_id)
    stamp = database.now()
    cursor = db.execute("INSERT INTO conversations (title, created_at, updated_at, work_id) VALUES ('新しい会話', ?, ?, ?)",
                        (stamp, stamp, body.work_id))
    works.set_active(db, body.work_id)
    return {"id": cursor.lastrowid}


@app.put("/api/conversations/{conversation_id}/work")
def set_conversation_work(conversation_id: int, body: ActiveWorkIn, user: CurrentUser = Depends(current_user),
                          db: sqlite3.Connection = Db):
    """出力先を決める (出力先がなかった会話や、出力先を変えるとき)。"""
    _conversation(db, conversation_id)
    if body.work_id is None:
        raise HTTPException(400, "出力先は外せません")
    _work(db, body.work_id)
    return {"work": works.assign(db, conversation_id, body.work_id)}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int, request: Request, user: CurrentUser = Depends(current_user),
                     db: sqlite3.Connection = Db):
    row = _conversation(db, conversation_id)
    items = db.execute("SELECT * FROM items WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    pending = db.execute("SELECT id FROM confirmations WHERE conversation_id = ? AND status = 'pending'",
                         (conversation_id,)).fetchall()
    work = works.for_conversation(db, conversation_id)
    if work is not None:
        works.set_active(db, work["id"])   # Claude Code の入口の出力先も、最後に開いた会話に合わせる
    return {"id": row["id"], "title": row["title"], "work": work, "items": [database.item_dict(item) for item in items],
            "pending": [item["id"] for item in pending], "busy": request.app.state.agent.busy(conversation_id)}


@app.post("/api/conversations/{conversation_id}/messages")
def send_message(conversation_id: int, body: MessageIn, request: Request, user: CurrentUser = Depends(current_user),
                 db: sqlite3.Connection = Db):
    row = _conversation(db, conversation_id)
    if row["work_id"] is None:
        raise HTTPException(409, "先にこの会話の出力先 (作業) を決めてください")
    if row["title"] == "新しい会話":
        db.execute("UPDATE conversations SET title = ? WHERE id = ?", (body.text.strip()[:40], conversation_id))
    agent, loop = request.app.state.agent, request.app.state.loop
    viewing = [line[:200] for line in body.viewing]
    loop.call_soon_threadsafe(functools.partial(agent.enqueue, conversation_id, body.text, voice=body.voice, viewing=viewing))
    return {"accepted": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, request: Request, materials_too: bool = Query(False, alias="materials"),
                        user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    """会話を消す。ノートは知識として残し、使用量は請求の記録として残す。資料は materials=true のときだけ消す。"""
    _conversation(db, conversation_id)
    if request.app.state.agent.busy(conversation_id):
        raise HTTPException(409, "作業中の会話は消せません。止めてから消してください")
    removed = 0
    db.execute("BEGIN")
    try:
        for table in ("items", "messages", "confirmations", "watches"):   # 見張りも外す (消した会話に通知しない)
            db.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (conversation_id,))
        if materials_too:
            removed = db.execute("DELETE FROM materials WHERE conversation_id = ?", (conversation_id,)).rowcount
        db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return {"ok": True, "materials_removed": removed}


@app.delete("/api/conversations/{conversation_id}/materials")
def clear_conversation_materials(conversation_id: int, user: CurrentUser = Depends(current_user),
                                 db: sqlite3.Connection = Db):
    _conversation(db, conversation_id)
    return {"removed": db.execute("DELETE FROM materials WHERE conversation_id = ?", (conversation_id,)).rowcount}


@app.get("/api/preferences")
def get_preferences(request: Request, user: CurrentUser = Depends(current_user)):
    return {"assistant_name": request.app.state.agent.name}


@app.put("/api/preferences")
def put_preferences(body: PreferencesIn, request: Request, user: CurrentUser = Depends(current_user),
                    db: sqlite3.Connection = Db):
    """呼び名を変える。Claude Code の入口は次に起動したときから。"""
    try:
        name = preferences.set_assistant_name(db, body.assistant_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _rename(request.app, name)
    return {"assistant_name": name}


@app.post("/api/conversations/{conversation_id}/stop")
def stop(conversation_id: int, request: Request, user: CurrentUser = Depends(current_user)):
    request.app.state.loop.call_soon_threadsafe(request.app.state.agent.stop, conversation_id)
    return {"ok": True}


@app.post("/api/confirmations/{confirmation_id}")
async def confirm(confirmation_id: int, body: ConfirmIn, request: Request, user: CurrentUser = Depends(current_user)):
    if not request.app.state.agent.answer(confirmation_id, body.approve):
        raise HTTPException(409, "この確認はもう終わっています")
    return {"ok": True}


@app.get("/api/conversations/{conversation_id}/events")
async def events(conversation_id: int, request: Request, user: CurrentUser = Depends(current_user)):
    """会話の動きを SSE で流す。切れたら画面が履歴を読み直してつなぎ直す。"""
    async def stream():
        queue = hub.listen(conversation_id)
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            hub.unlisten(conversation_id, queue)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


def _default_app(toolbox: Toolbox) -> str | None:
    """アプリの名前がない古いノートのジョブは、結果を見られるアプリ (なければ最初のアプリ) のものとする。"""
    viewable = [name for name, app in toolbox.apps.items() if app.config.view]
    return (viewable or list(toolbox.apps) or [None])[0]


@app.get("/api/notes")
def list_notes(request: Request, q: str = Query("", max_length=200), user: CurrentUser = Depends(current_user),
               db: sqlite3.Connection = Db):
    default = _default_app(request.app.state.toolbox)
    return [note | {"app": note["app"] or default} for note in notes.search(db, q, 100)]


@app.get("/api/conversations/{conversation_id}/materials")
def conversation_materials(conversation_id: int, request: Request, user: CurrentUser = Depends(current_user),
                           db: sqlite3.Connection = Db):
    """この会話で道具が読んだ・作ったもの (呼び出しと結果、関わったジョブ) と、書いたノート。"""
    _conversation(db, conversation_id)
    default = _default_app(request.app.state.toolbox)
    written = db.execute("SELECT * FROM notes WHERE conversation_id = ? ORDER BY id DESC", (conversation_id,)).fetchall()
    return materials.for_conversation(db, conversation_id) | {
        "notes": [note | {"app": note["app"] or default} for note in map(notes.to_dict, written)]}


@app.get("/api/materials")
def all_materials(q: str = Query("", max_length=200), user: CurrentUser = Depends(current_user),
                  db: sqlite3.Connection = Db):
    """これまでの会話 (Claude Code の入口を含む) の資料。"""
    return materials.everything(db, q)


@app.delete("/api/materials/{material_id}")
def delete_material(material_id: int, user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    if not db.execute("DELETE FROM materials WHERE id = ?", (material_id,)).rowcount:
        raise HTTPException(404, "資料が見つかりません")
    return {"ok": True}


@app.get("/api/materials/{material_id}")
def material_detail(material_id: int, user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    found = materials.detail(db, material_id)
    if found is None:
        raise HTTPException(404, "資料が見つかりません")
    return found


def _view_app(request: Request, app_name: str):
    app = request.app.state.toolbox.apps.get(app_name)
    if app is None or not app.config.view:
        raise HTTPException(404, f"{app_name} の結果を見る設定 (view) がありません")
    return app


@app.get("/api/views/{app_name}/jobs/{job_id}")
async def job_view(app_name: str, request: Request, job_id: str = PathParam(pattern=JOB_ID_PATTERN),
                   conversation_id: int | None = None, user: CurrentUser = Depends(current_user),
                   db: sqlite3.Connection = Db):
    """ジョブの結果のグラフにできる表の一覧・図・Plotly の図と、アプリの画面の URL。LLM は使わない。
    図のファイルは、会話の作業のディレクトリの中にあるものだけ (作業のファイルとして) 渡す。"""
    app = _view_app(request, app_name)
    try:
        found = await views.overview(app, job_id)
        work = works.for_conversation(db, conversation_id)
        files = {kind: works.inside(work, found[kind]) if work else [] for kind in ("images", "html")}
        return {"app": app_name, "job_id": job_id, "link": views.link(app, job_id), "datasets": found["datasets"],
                "work_id": work["id"] if work else None, "trajectory": views.has_trajectory(app),
                "final": views.final_tool(app), **files}
    except views.ViewError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/views/{app_name}/structures/{structure_id}")
async def structure_view(app_name: str, request: Request, structure_id: str = PathParam(pattern=JOB_ID_PATTERN),
                         repeat: str = Query("auto", pattern=r"^(auto|[1-9],[1-9],[1-9])$"),
                         user: CurrentUser = Depends(current_user)):
    """構造の 3D 表示用のデータ。repeat は auto (原子が少なければ繰り返す) か "2,2,1" のような繰り返し。"""
    app = _view_app(request, app_name)
    try:
        return await views.structure(app, structure_id, None if repeat == "auto" else [int(v) for v in repeat.split(",")])
    except views.ViewError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/workbench/{app_name}")
async def run_workbench(app_name: str, body: WorkbenchIn, request: Request, user: CurrentUser = Depends(current_user)):
    """作業台: 構造の編集と計算。確かめる操作は読むだけの道具を直接、保存と投入は関門を通して会話に残す。"""
    state = request.app.state
    app = state.toolbox.apps.get(app_name)
    if app is None:
        raise HTTPException(404, f"アプリ {app_name} はつないでいません")
    try:
        if body.action in workbench.READ_ACTIONS:
            db = database.connect()
            try:   # 作業の中のファイルを渡す道具 (ezcal の見積もりなど) は、会話の作業のディレクトリで解く
                in_work = functools.partial(state.agent.gate._in_work, db, body.conversation_id, app)
                return {"result": await workbench.read(app, body.action, body.args, in_work)}
            finally:
                db.close()
        tool = workbench.tool_for(app, body.action)
        workbench.check(tool, body.args)
    except workbench.WorkbenchError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    if body.conversation_id is None:
        raise HTTPException(400, "保存と投入は会話の中で行います (conversation_id)")
    db = database.connect()
    try:
        conversation = _conversation(db, body.conversation_id)
        if conversation["work_id"] is None:
            raise HTTPException(409, "先にこの会話の出力先 (作業) を決めてください")
        label = " ".join(body.label.split()) or tool.name
        if conversation["title"] == "新しい会話":
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", (f"構造の作業: {label}"[:40], body.conversation_id))
        agent = state.agent
        try:
            text = await agent.gate.run(
                db, body.conversation_id, f"{app_name}__{tool.name}", body.args,
                confirm=functools.partial(agent._confirm, db, body.conversation_id),
                announce=lambda line: hub.add_item(db, body.conversation_id, "notice", {"text": line, "level": "info"}))
        except ToolFailed as exc:
            raise HTTPException(409 if "拒否" in str(exc) else 502, str(exc)) from exc
        summary = workbench.action_text(body.action, label, body.args, text)
        hub.add_item(db, body.conversation_id, "action", {"text": summary, "told": False})
        return {"result": json.loads(text), "summary": summary}
    finally:
        db.close()


# ---- 作業 (作業のディレクトリ) ---------------------------------------------------------------------

def _work(db, work_id: int) -> dict:
    try:
        return works.get(db, work_id)
    except works.WorkError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/works")
def list_works(user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    return {"works": works.listing(db), "active": works.active(db)}


@app.post("/api/works")
def create_work(body: WorkIn, request: Request, user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    """作業を作って、いまの作業にする。ディレクトリは設定の works.roots の下だけ。"""
    try:
        return works.create(db, request.app.state.config, body.name, body.path)
    except works.WorkError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/works/active")
def choose_work(body: ActiveWorkIn, user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    if body.work_id is not None:
        _work(db, body.work_id)
    works.set_active(db, body.work_id)
    return {"active": works.active(db)}


@app.get("/api/works/{work_id}/files")
def work_files(work_id: int, directory: str = Query("", max_length=500), user: CurrentUser = Depends(current_user),
               db: sqlite3.Connection = Db):
    try:
        return works.files(_work(db, work_id), directory)
    except works.WorkError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/works/{work_id}/file")
def work_file(work_id: int, path: str = Query(min_length=1, max_length=500), user: CurrentUser = Depends(current_user),
              db: sqlite3.Connection = Db):
    """ファイルを見られる形で返す: 構造 (3D)・表 (グラフ)・画像・文字。"""
    try:
        return works.open_file(_work(db, work_id), path)
    except works.WorkError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/works/{work_id}/image")
def work_image(work_id: int, path: str = Query(min_length=1, max_length=500), user: CurrentUser = Depends(current_user),
               db: sqlite3.Connection = Db):
    try:
        file_path, media = works.image_path(_work(db, work_id), path)
    except works.WorkError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(file_path, media_type=media)   # 画面の CSP (スクリプトは同じ場所のファイルだけ) がそのまま付く


@app.get("/api/works/{work_id}/html")
def work_html(work_id: int, path: str = Query(min_length=1, max_length=500), user: CurrentUser = Depends(current_user),
              db: sqlite3.Connection = Db):
    """作業の HTML (ezcal の Plotly の図など) を、封じた枠 (sandbox) で見せる。中のスクリプトは別の場所として動き、
    MatDev のログインや API には触れない。読み込めるのは Plotly の配布元だけ。"""
    try:
        file_path = works.html_path(_work(db, work_id), path)
    except works.WorkError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(file_path, media_type="text/html", headers={
        "Content-Security-Policy": ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' https://cdn.plot.ly; "
                                    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; frame-ancestors 'self'"),
        "X-Frame-Options": "SAMEORIGIN"})


@app.post("/api/works/save")
async def save_to_work(body: SaveToWorkIn, request: Request, user: CurrentUser = Depends(current_user),
                       db: sqlite3.Connection = Db):
    """ジョブの結果か構造を、会話の出力先 (なければいまの作業) に置く。"""
    work = works.for_conversation(db, body.conversation_id)
    if work is None:
        raise HTTPException(400, "出力先 (作業) が決まっていません")
    app = request.app.state.toolbox.apps.get(body.app)
    if app is None:
        raise HTTPException(404, f"アプリ {body.app} はつないでいません")
    try:
        return {"work": work, "result": await works.export(app, work, body.kind, body.id)}
    except works.WorkError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc


# ---- 道具を画面から使う (チャットなしで) --------------------------------------------------------------

@app.get("/api/tools")
def list_tools(request: Request, user: CurrentUser = Depends(current_user)):
    """画面から使える道具 (ジャービスに渡すものと同じ)。印 (読むだけ・消す) と、作業の中のパスを受ける引数を添える。"""
    toolbox = request.app.state.toolbox
    marks = {name: tool.annotations for name, _, tool in toolbox.qualified()}
    found = []
    for tool in request.app.state.agent.build_tools():
        app_name, sep, short = tool["name"].partition("__")
        app = toolbox.apps.get(app_name) if sep else None
        annotations = marks.get(tool["name"])
        found.append({**tool, "app": app_name if app else "matdev", "tool": short if app else tool["name"],
                      "read_only": bool(annotations and annotations.read_only_hint) or tool["name"] in (
                          "search_notes", "list_work_files", "read_work_file"),
                      "destructive": bool(annotations and annotations.destructive_hint),
                      "work_paths": ((app.config.work or {}).get(short) or {}).get("paths", []) if app else
                                    (["path"] if tool["name"] == "read_work_file" else [])})
    return found


@app.post("/api/conversations/{conversation_id}/tools")
async def run_tool(conversation_id: int, body: ToolRunIn, request: Request, user: CurrentUser = Depends(current_user)):
    """道具を画面から動かす。関門を通す (確認・見積もり・見張り・資料・作業への書き出しは、ジャービスが使うときと同じ)。
    やったことと結果は会話に残し、次の依頼でジャービスにも伝える。"""
    state = request.app.state
    tool = next((t for t in state.agent.build_tools() if t["name"] == body.name), None)
    if tool is None:
        raise HTTPException(404, f"道具 {body.name} はありません")
    try:
        jsonschema.validate(body.args, tool["input_schema"])
    except jsonschema.ValidationError as exc:
        raise HTTPException(400, f"入力が正しくありません: {exc.message}") from exc
    db = database.connect()
    try:
        conversation = _conversation(db, conversation_id)
        if conversation["work_id"] is None:
            raise HTTPException(409, "先にこの会話の出力先 (作業) を決めてください")
        if conversation["title"] == "新しい会話":
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", (f"道具: {body.name}"[:40], conversation_id))
        agent = state.agent
        summary = materials.summarize(body.args)
        try:
            text, ok = await agent.gate.run(
                db, conversation_id, body.name, body.args,
                confirm=functools.partial(agent._confirm, db, conversation_id),
                announce=lambda line: hub.add_item(db, conversation_id, "notice", {"text": line, "level": "info"})), True
        except ToolFailed as exc:
            text, ok = str(exc), False
        hub.add_item(db, conversation_id, "action", {
            "text": f"画面で {body.name} を使いました" + (f" ({summary})" if summary else ""), "told": False, "tool": body.name,
            "ok": ok, "result": scrub(text)[:6000]})
        return {"ok": ok, "result": scrub(text)[:20000]}
    finally:
        db.close()


# ---- 対話型 MD (その場で MD を回しながら原子を動かす) -------------------------------------------------

@app.get("/api/imd/engines")
async def imd_engines(request: Request, user: CurrentUser = Depends(current_user)):
    return {"engines": await imd.engines(request.app.state.toolbox), "max_atoms": 500}


@app.websocket("/api/imd")
async def imd_socket(websocket: WebSocket):
    """画面と MD の係を中継する。ログイン済みで、同じ場所から開いた画面だけ受け付ける。"""
    db = database.connect()
    try:
        origin, host = websocket.headers.get("origin", ""), websocket.headers.get("host", "")
        if auth.user_from_cookie(db, websocket) is None or origin.split("://", 1)[-1] != host:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        session: imd.Session | None = None

        async def send(message: dict) -> None:
            try:
                await websocket.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:  # 画面が閉じたあと
                pass
        try:
            while True:
                text = await websocket.receive_text()
                if len(text) > imd.MAX_MESSAGE:
                    continue
                try:
                    message = json.loads(text)
                    op = message.get("op")
                    if op == "start" and session is None:
                        conversation_id = message.get("conversation_id")
                        if conversation_id is not None:
                            _conversation(db, int(conversation_id))
                        session = imd.Session(int(conversation_id) if conversation_id is not None else None)
                        await session.start(db, message, send)
                    elif op == "save" and session is not None:
                        await send(await session.save(db))
                    elif op == "stop":
                        break
                    elif session is not None:
                        session.forward(message)
                except (imd.ImdError, HTTPException, ValueError, TypeError, asyncio.TimeoutError) as exc:
                    await send({"type": "error", "message": getattr(exc, "detail", None) or str(exc) or "時間切れです"})
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                await session.stop()
    finally:
        db.close()


@app.get("/api/structures/latest")
def latest_structures(conversation_id: int | None = None, user: CurrentUser = Depends(current_user),
                      db: sqlite3.Connection = Db):
    """空間に出す構造の候補 (この会話と Claude Code の入口で触れたもの、新しい順)。"""
    return materials.latest_structures(db, conversation_id)


@app.get("/api/views/{app_name}/jobs/{job_id}/trajectory")
async def job_trajectory(app_name: str, request: Request, job_id: str = PathParam(pattern=JOB_ID_PATTERN),
                         user: CurrentUser = Depends(current_user)):
    """計算過程の構造の時間変化 (3D で動かして見る)。LLM は使わない。"""
    app = _view_app(request, app_name)
    try:
        return await views.trajectory(app, job_id)
    except views.ViewError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/views/{app_name}/jobs/{job_id}/data")
async def job_table(app_name: str, request: Request, job_id: str = PathParam(pattern=JOB_ID_PATTERN),
                    key: str = Query(min_length=1, max_length=100), user: CurrentUser = Depends(current_user)):
    app = _view_app(request, app_name)
    try:
        return await views.table(app, job_id, key)
    except views.ViewError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ToolFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/speech/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...), user: CurrentUser = Depends(current_user)):
    """声を文字にする。この PC の Whisper で動かし、声は外に出さない。録音は残さない。"""
    data = await audio.read(speech.MAX_BYTES + 1)
    try:
        return await request.app.state.speech.transcribe(data)
    except speech.SpeechError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/usage")
def get_usage(user: CurrentUser = Depends(current_user), db: sqlite3.Connection = Db):
    return usage.month_summary(db)


app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR, html=True), name="frontend")

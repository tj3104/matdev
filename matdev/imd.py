"""対話型 MD: その場で MD を回しながら、画面 (空間・VR) から原子をつまんで動かす。

画面と WebSocket でつなぎ、MD の係 (adapters/imd_worker.py、MatSim の venv の Python) との間で指示と原子の位置を中継する。
係は 1 つの接続に 1 つ。接続が切れたら止める。構造は MatSim の構造の番号か、会話の作業の構造ファイル (作業の外は断る)。
「保存」は、いまの構造を会話の作業の structures/ に xyz で置き、会話に残す (次の依頼でジャービスにも伝わる)。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from . import hub, settings, works
from .tools import SECRET_ENV

WORKER = Path(__file__).resolve().parent / "adapters" / "imd_worker.py"
FROM_SCREEN = {"pull", "release", "set"}   # 画面から係にそのまま渡す指示
MAX_MESSAGE = 4096


class ImdError(Exception):
    """利用者に見せる理由つきの失敗。"""


def worker_env() -> dict:
    env = {key: value for key, value in os.environ.items() if not SECRET_ENV.search(key)}
    return {**env, "MATSIM_DATA_DIR": settings.MATSIM_DATA_DIR}


async def engines(toolbox) -> list[dict]:
    """選べるポテンシャル: MatSim に登録したもの (NEP は CPU、SevenNet は GPU か CPU) と、確認用の EMT。既定は NEP89 (CPU)。"""
    found = []
    app = toolbox.apps.get("matsim")
    if app is not None:
        try:
            listed = await app.call_json("list_potentials", {})
        except Exception:   # MatSim につながらなくても EMT は使える
            listed = []
        for potential in listed if isinstance(listed, list) else [listed]:
            if not potential.get("available", True):
                continue
            if potential.get("engine") == "sevennet":
                for device, label in (("cuda", "GPU"), ("cpu", "CPU")):
                    found.append({"id": f"{potential['id']}:{device}", "label": f"{potential['name']} ({label})",
                                  "engine": {"potential_id": potential["id"], "device": device}})
            else:
                found.append({"id": str(potential["id"]), "label": f"{potential['name']} (CPU)",
                              "engine": {"potential_id": potential["id"]}})
    found.append({"id": "emt", "label": "EMT (ASE、確認用。Al・Cu・Ag・Au・Ni・Pd・Pt)", "engine": {"kind": "emt"}})
    default = next((e for e in found if "NEP89" in e["label"]), found[0])
    return [{**e, "default": e is default} for e in found]


class Session:
    """1 つの接続の対話型 MD。"""

    def __init__(self, conversation_id: int | None) -> None:
        self.conversation_id = conversation_id
        self.process: asyncio.subprocess.Process | None = None
        self.snapshots: asyncio.Queue = asyncio.Queue()

    async def start(self, db, request: dict, send) -> None:
        start = {"op": "start", "engine": request.get("engine") or {}, "temperature": request.get("temperature", 300)}
        if request.get("file"):
            work = works.for_conversation(db, self.conversation_id)
            if work is None:
                raise ImdError("この会話の出力先 (作業) を決めてください")
            try:
                start["file"] = str(works.resolve(work, request["file"]))
            except works.WorkError as exc:
                raise ImdError(str(exc)) from exc
        elif request.get("structure_id") is not None:
            start["structure_id"] = int(request["structure_id"])
        else:
            raise ImdError("構造を指定してください")
        self.process = await asyncio.create_subprocess_exec(
            settings.IMD_PYTHON, str(WORKER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=worker_env(), limit=8 * 1024 * 1024)
        self._write(start)
        asyncio.create_task(self._relay(send), name="imd-relay")

    def _write(self, message: dict) -> None:
        if self.process and self.process.stdin and not self.process.stdin.is_closing():
            self.process.stdin.write((json.dumps(message) + "\n").encode())

    async def _relay(self, send) -> None:
        """係の出力を画面へ流す。保存のための構造の写しは、待っている保存へ渡す。"""
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                await send({"type": "closed"})
                return
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("type") == "snapshot":
                await self.snapshots.put(message)
            else:
                await send(message)

    def forward(self, message: dict) -> None:
        if message.get("op") in FROM_SCREEN:
            self._write(message)

    async def save(self, db) -> dict:
        """いまの構造を会話の作業の structures/ に xyz で置き、会話に残す。"""
        from ase import Atoms
        from ase.io import write

        work = works.for_conversation(db, self.conversation_id)
        if work is None:
            raise ImdError("この会話の出力先 (作業) を決めてください")
        self._write({"op": "snapshot", "request": "save"})
        snapshot = await asyncio.wait_for(self.snapshots.get(), 10)
        atoms = Atoms(snapshot["symbols"], positions=snapshot["positions"], cell=snapshot["cell"], pbc=True)
        name = f"imd_{time.strftime('%Y%m%d_%H%M%S')}_{atoms.get_chemical_formula(mode='metal')}.xyz"
        path = works.resolve(work, f"structures/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, atoms, format="extxyz")
        relative = path.relative_to(Path(work["path"]).resolve()).as_posix()
        if self.conversation_id is not None:
            hub.add_item(db, self.conversation_id, "action", {
                "text": (f"画面の対話型 MD ({snapshot['engine']}、{snapshot['step']} ステップ、{snapshot['temperature']:.0f} K) の"
                         f"構造を {relative} に保存しました"), "told": False})
        return {"type": "saved", "path": relative, "n_atoms": len(atoms)}

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self._write({"op": "stop"})
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()

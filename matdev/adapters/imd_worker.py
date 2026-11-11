"""対話型 MD の係。その場で MD を回しながら、利用者が原子をつまんで引っぱる (VR の手・画面のドラッグ)。

    <MatSim の venv の Python> imd_worker.py      (標準入出力で 1 行 1 つの JSON をやり取りする)

MatDev が会話ごとに立ち上げる。MatSim の venv (ASE・calorine・SevenNet・matsimapi) で動かし、MatSim のポテンシャルと構造を使う。
- ポテンシャル: NEP (gpumd) は calorine で CPU、SevenNet は GPU か CPU、emt は ASE の EMT (動作の確認用)
- 引っぱる: つまんでいる原子は MD では動かさず、毎ステップ手の方へ少しずつ (MAX_MOVE まで) 動かす。動かす先は、ほかの原子と
  「共有結合半径の和 × MIN_FACTOR」より近づかない位置に押し戻す (手ではそれ以上近づけられない)。押し戻したときは blocked を
  返し、画面で知らせる。ほかの原子は MD で自然に動く
- 1 秒に FPS 回、原子の位置を返す。そのあいだは MD を回し続ける

受け取る: start / pull / release / set (temperature・paused・fixed・timestep) / snapshot / stop
返す: ready / frame / snapshot / error
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import warnings

os.environ.setdefault("OMP_NUM_THREADS", "4")   # CPU の NEP が全部の芯を取らないように (numpy より先に決める)

import numpy as np  # noqa: E402

warnings.filterwarnings("ignore")   # ASE の非推奨の知らせなどで標準エラーを埋めない

MAX_ATOMS = 500
MIN_FACTOR = 0.7          # 近づけてよい距離 = (共有結合半径の和) × これ
MAX_MOVE = 0.02           # つまんだ原子を 1 ステップで動かす上限 [Å] (250 ステップ/秒なら 5 Å/秒)
FPS = 25


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---- 準備 -------------------------------------------------------------------------------------

def load_atoms(request: dict):
    from ase.io import read
    if request.get("file"):
        atoms = read(request["file"], index=-1)
    else:
        from matsimapi.client import Client
        atoms = Client().atoms(int(request["structure_id"]))
    if len(atoms) > MAX_ATOMS:
        raise ValueError(f"対話型 MD は {MAX_ATOMS} 原子までです ({len(atoms)} 原子)")
    atoms.pbc = True if atoms.cell.rank == 3 else atoms.pbc
    return atoms


def make_calculator(engine: dict):
    kind = engine.get("kind", "potential")
    if kind == "emt":
        from ase.calculators.emt import EMT
        return EMT(), "EMT (ASE)"
    from matsimapi import potentials
    from matsimapi.client import Client
    potential = Client()._call(potentials.get, int(engine["potential_id"]))
    if potential["engine"] == "sevennet":
        from sevenn.calculator import SevenNetCalculator
        parts = potential["path"].split(":")
        kwargs = {"modal": parts[2]} if len(parts) > 2 else {}
        device = engine.get("device", "cuda")
        return SevenNetCalculator(parts[1], device=device, **kwargs), f"{potential['name']} ({device})"
    from calorine.calculators import CPUNEP
    return CPUNEP(potential["path"]), f"{potential['name']} (CPU)"


def _limits(atoms, index: int):
    from ase.data import covalent_radii
    others = np.array([i for i in range(len(atoms)) if i != index])
    return others, MIN_FACTOR * (covalent_radii[atoms.numbers[index]] + covalent_radii[atoms.numbers[others]])


def clamp_target(atoms, index: int, target: np.ndarray) -> tuple[np.ndarray, bool]:
    """引っぱる先を、ほかの原子に近づきすぎない位置に押し戻す。押し戻したら True。"""
    from ase.geometry import find_mic

    others, limits = _limits(atoms, index)
    if not len(others):
        return target, False
    blocked = False
    for _ in range(8):   # 何個かの原子に同時に近いときのため、数回くり返す
        vectors, distances = find_mic(target - atoms.positions[others], atoms.cell, atoms.pbc)
        close = distances < limits
        if not close.any():
            break
        blocked = True
        for vector, distance, limit in zip(vectors[close], distances[close], limits[close]):
            direction = vector / distance if distance > 1e-6 else np.array([0.0, 0.0, 1.0])
            target = target + direction * (limit - distance + 1e-3)
    return target, blocked


# ---- MD ---------------------------------------------------------------------------------------

class Session:
    def __init__(self, request: dict) -> None:
        from ase import units
        from ase.constraints import FixAtoms
        from ase.md.langevin import Langevin
        from ase.md.velocitydistribution import MaxwellBoltzmannDistribution

        self.units, self.FixAtoms = units, FixAtoms
        self.atoms = load_atoms(request)
        self.atoms.calc, self.label = make_calculator(request.get("engine") or {})
        self.pulling: int | None = None     # つまんでいる原子
        self.target: np.ndarray | None = None
        self.temperature = float(request.get("temperature", 300))
        self.timestep = float(request.get("timestep_fs", 1.0))
        MaxwellBoltzmannDistribution(self.atoms, temperature_K=self.temperature)
        self.md = Langevin(self.atoms, self.timestep * units.fs, temperature_K=self.temperature, friction=0.02, fixcm=False)
        self.paused = False
        self.fixed: set[int] = set()
        self.blocked = False
        self.step = 0

    def describe(self) -> dict:
        return {"type": "ready", "engine": self.label, "n_atoms": len(self.atoms),
                "symbols": self.atoms.get_chemical_symbols(), "cell": self.atoms.cell.tolist(),
                "positions": np.round(self.atoms.positions, 3).tolist(), "temperature": self.temperature,
                "max_atoms": MAX_ATOMS, "min_factor": MIN_FACTOR}

    def handle(self, message: dict) -> None:
        op = message.get("op")
        if op == "pull":
            index = int(message["index"])
            if not 0 <= index < len(self.atoms):
                raise ValueError("原子の番号が範囲の外です")
            if index != self.pulling:
                self.pulling = index
                self.atoms.set_momenta(self.atoms.get_momenta() * (np.arange(len(self.atoms)) != index)[:, None])
                self._constrain()
            self.target = np.array(message["target"], dtype=float)
        elif op == "release":
            self.pulling = self.target = None
            self.blocked = False
            self._constrain()
        elif op == "set":
            if "temperature" in message:
                self.temperature = max(1.0, min(5000.0, float(message["temperature"])))
                self.md.set_temperature(temperature_K=self.temperature)
            if "paused" in message:
                self.paused = bool(message["paused"])
            if "timestep_fs" in message:
                self.timestep = max(0.1, min(5.0, float(message["timestep_fs"])))
                self.md.set_timestep(self.timestep * self.units.fs)
            if "fixed" in message:   # 原子をその場に止める・放す
                self.fixed = {int(i) for i in message["fixed"] if 0 <= int(i) < len(self.atoms)}
                self._constrain()
        elif op == "snapshot":
            send({"type": "snapshot", "symbols": self.atoms.get_chemical_symbols(), "cell": self.atoms.cell.tolist(),
                  "positions": self.atoms.positions.tolist(), "step": self.step, "temperature": self.temperature,
                  "engine": self.label, "request": message.get("request")})

    def _constrain(self) -> None:
        """止める原子 (止めた原子と、つまんでいる原子) は MD で動かさない。"""
        held = sorted(self.fixed | ({self.pulling} if self.pulling is not None else set()))
        self.atoms.set_constraint(self.FixAtoms(indices=held) if held else None)

    def drag(self) -> None:
        """つまんでいる原子を手の方へ少し動かす。ほかの原子に近づきすぎる位置には置かない。"""
        if self.pulling is None or self.target is None:
            return
        current = self.atoms.positions[self.pulling]
        move = self.target - current
        size = np.linalg.norm(move)
        if size > MAX_MOVE:
            move *= MAX_MOVE / size
        place, self.blocked = clamp_target(self.atoms, self.pulling, current + move)
        positions = self.atoms.get_positions()
        positions[self.pulling] = place
        self.atoms.set_positions(positions, apply_constraint=False)

    def frame(self) -> dict:
        kinetic = self.atoms.get_kinetic_energy()
        current = 2 * kinetic / (3 * len(self.atoms) * self.units.kB) if len(self.atoms) else 0.0
        return {"type": "frame", "step": self.step, "time_ps": round(self.step * self.timestep / 1000, 4),
                "positions": np.round(self.atoms.positions, 3).tolist(), "temperature": round(float(current), 1),
                "target_temperature": self.temperature, "epot": round(float(self.atoms.get_potential_energy()), 4),
                "pulling": self.pulling, "blocked": self.blocked, "paused": self.paused, "fixed": sorted(self.fixed)}


def _reader(inbox: queue.Queue) -> None:
    """指示を読む係。読んだ行を順に渡す (続けて届いた指示も取りこぼさない)。閉じたら None。"""
    for line in sys.stdin:
        inbox.put(line)
    inbox.put(None)


def main() -> None:
    inbox: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(inbox,), daemon=True).start()
    session: Session | None = None
    period = 1.0 / FPS
    while True:
        started = time.time()
        # 届いた指示を全部読む (MD を回すあいだも、毎フレーム確かめる)。何も動いていなければ待つ
        lines = []
        try:
            lines.append(inbox.get(timeout=period) if session is None or session.paused else inbox.get_nowait())
        except queue.Empty:
            pass
        while True:
            try:
                lines.append(inbox.get_nowait())
            except queue.Empty:
                break
        for line in lines:
            if line is None:
                return   # MatDev が閉じた
            try:
                message = json.loads(line)
                if message.get("op") == "start":
                    session = Session(message)
                    send(session.describe())
                elif message.get("op") == "stop":
                    return
                elif session is not None:
                    session.handle(message)
            except Exception as exc:  # 1 つの指示の失敗で係を止めない
                send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        if session is None:
            continue
        if not session.paused:
            try:   # 1 フレームの時間いっぱい MD を進める
                while time.time() - started < period * 0.8:
                    session.drag()
                    session.md.run(1)
                    session.step += 1
            except Exception as exc:
                send({"type": "error", "message": f"MD が止まりました: {type(exc).__name__}: {exc}"})
                session.paused = True
        send(session.frame())
        rest = period - (time.time() - started)
        if rest > 0 and session.paused:
            time.sleep(rest)


if __name__ == "__main__":
    main()

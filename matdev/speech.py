"""耳: 声を文字にする。この PC の Whisper (faster-whisper) で動かし、声は外に出さない。

モデルは data/models に置き、最初に使うときに読み込む (起動時に裏で読み込みも始める)。
変換は重いので別のスレッドで動かし、同時に 1 つずつにする。読み上げは端末 (ブラウザ) の機能で行う。
"""
from __future__ import annotations

import asyncio
import io
import threading

from . import settings
from .config import SpeechConfig

MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 60


class SpeechError(Exception):
    """利用者に見せる理由つきの失敗。"""


class Transcriber:
    def __init__(self, config: SpeechConfig) -> None:
        self.config = config
        self._model = None
        self._load_lock = threading.Lock()
        self._run_lock = asyncio.Lock()
        self.error: str | None = None
        self.extra_words: list[str] = []   # アシスタントの呼び名など、設定の語に足す手がかり

    def ready(self) -> bool:
        return self.config.provider != "none" and self.error is None

    def describe(self) -> str:
        if self.config.provider == "none":
            return "なし"
        return f"whisper / {self.config.model} ({self.config.device})"

    def _load(self):
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel   # 重いので、使うときに読む
                try:
                    self._model = WhisperModel(self.config.model, device=self.config.device,
                                               compute_type=self.config.compute_type,
                                               download_root=str(settings.MODELS_DIR))
                except Exception as exc:
                    self.error = f"音声認識のモデルを読めません: {exc}"
                    raise SpeechError(self.error) from exc
            return self._model

    def preload(self) -> None:
        """起動時に裏で読み込んでおく (最初の声を待たせない)。失敗は error に残す。"""
        if self.config.provider == "none":
            return
        threading.Thread(target=self._preload, name="speech-preload", daemon=True).start()

    def _preload(self) -> None:
        try:
            self._load()
        except SpeechError:
            pass

    def _transcribe(self, audio: bytes) -> dict:
        from faster_whisper.audio import decode_audio
        model = self._load()
        try:
            samples = decode_audio(io.BytesIO(audio))
        except Exception as exc:
            raise SpeechError("録音を読めませんでした") from exc
        seconds = len(samples) / 16000
        if seconds > MAX_SECONDS:
            raise SpeechError(f"録音が長すぎます ({MAX_SECONDS} 秒まで)")
        words = list(dict.fromkeys([*self.extra_words, *self.config.vocabulary]))
        prompt = "、".join(words) if words else None
        segments, _ = model.transcribe(samples, language=self.config.language, beam_size=1, vad_filter=True,
                                       initial_prompt=prompt, condition_on_previous_text=False)
        return {"text": "".join(segment.text for segment in segments).strip(), "seconds": round(seconds, 1)}

    async def transcribe(self, audio: bytes) -> dict:
        if self.config.provider == "none":
            raise SpeechError("音声認識は設定で切ってあります (speech.provider)")
        if not audio:
            raise SpeechError("録音が空です")
        if len(audio) > MAX_BYTES:
            raise SpeechError("録音が大きすぎます")
        async with self._run_lock:
            return await asyncio.to_thread(self._transcribe, audio)

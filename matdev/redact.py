"""鍵の形をした文字列を伏せる。LLM に渡す前と、資料として残す前に通す。"""
from __future__ import annotations

import re

SECRET_TEXT = re.compile(r"sk-(ant-)?[A-Za-z0-9_\-]{16,}")


def scrub(text: str) -> str:
    """API の鍵の形をした文字列を消す (Anthropic・OpenAI 形式)。"""
    return SECRET_TEXT.sub("[鍵を伏せました]", text)

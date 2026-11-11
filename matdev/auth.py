"""ログイン: 最初の 1 アカウントだけ画面から作る。MatSimApp と同じ作り (scrypt・Cookie・CSRF ヘッダー・試行制限)。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response

from . import settings
from .db import get_db

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "matdev"
MAX_FAILURES = 5
LOCK_SECONDS = 300

_failures: dict[str, tuple[int, float]] = {}
_failures_lock = threading.Lock()


@dataclass
class CurrentUser:
    id: int
    username: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=2**27, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
                                maxmem=2**27, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# 存在しないユーザーのログイン試行でも同じ計算時間をかけるためのダミー
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_session(db: sqlite3.Connection, response: Response, user_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(),))
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=settings.SESSION_DAYS)
    db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
               (_token_hash(token), user_id, expires.isoformat()))
    response.set_cookie(settings.SESSION_COOKIE, token, max_age=settings.SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=settings.COOKIE_SECURE, path="/")


def end_session(db: sqlite3.Connection, request: Request, response: Response) -> None:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(settings.SESSION_COOKIE, path="/")


def user_from_cookie(db: sqlite3.Connection, request: Request) -> CurrentUser | None:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        return None
    row = db.execute("SELECT u.id, u.username, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
                     "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) < _now():
        return None
    return CurrentUser(row["id"], row["username"])


def current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> CurrentUser:
    """ログインを確かめる。状態を変える呼び出しには CSRF 対策のヘッダーも求める。"""
    user = user_from_cookie(db, request)
    if user is None:
        raise HTTPException(401, "ログインしてください")
    require_csrf(request)
    return user


def require_csrf(request: Request) -> None:
    if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "不正なリクエストです")


def check_locked(username: str) -> None:
    with _failures_lock:
        count, until = _failures.get(username, (0, 0.0))
        if count >= MAX_FAILURES and time.time() < until:
            raise HTTPException(429, "ログインの失敗が続いたため、しばらく待ってください")


def record_failure(username: str) -> None:
    with _failures_lock:
        count, _ = _failures.get(username, (0, 0.0))
        _failures[username] = (count + 1, time.time() + LOCK_SECONDS)


def clear_failures(username: str) -> None:
    with _failures_lock:
        _failures.pop(username, None)

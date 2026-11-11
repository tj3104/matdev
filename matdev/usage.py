"""LLM の使用量の記録と、月の上限。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import settings
from .db import now


def prices_for(model: str, configured: list | None = None) -> tuple[float, float, float, float]:
    """1M トークンあたりの料金 (入力・出力・キャッシュ読み・キャッシュ書き)。

    設定ファイルの llm.price_per_mtok ([入力, 出力]) があればそれ (ローカルの LLM なら [0, 0])。
    なければ料金表、表にないモデルは表で最も高い料金 (上限を必ず効かせるため)。
    """
    if configured is not None:
        price_in, price_out = (list(configured) + [0, 0])[:2]
        return (price_in, price_out, price_in, price_in)
    return settings.PRICES.get(model) or max(settings.PRICES.values(), key=lambda p: p[0] + p[1])


def record(db: sqlite3.Connection, conversation_id: int, model: str, usage, configured: list | None = None) -> None:
    prices = prices_for(model, configured)
    tokens = (usage.input_tokens, usage.output_tokens, usage.cache_read_input_tokens or 0,
              usage.cache_creation_input_tokens or 0)
    cost = sum(count * price for count, price in zip(tokens, prices)) / 1_000_000
    db.execute("INSERT INTO usage (conversation_id, model, input_tokens, output_tokens, cache_read_tokens, "
               "cache_write_tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (conversation_id, model, *tokens, cost, now()))


def month_summary(db: sqlite3.Connection) -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = db.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(input_tokens), 0) AS i, "
                     "COALESCE(SUM(output_tokens), 0) AS o, COALESCE(SUM(cache_read_tokens), 0) AS c "
                     "FROM usage WHERE created_at LIKE ?", (month + "%",)).fetchone()
    return {"month": month, "calls": row["calls"], "cost_usd": round(row["cost"], 4), "input_tokens": row["i"],
            "output_tokens": row["o"], "cache_read_tokens": row["c"], "limit_usd": settings.MONTHLY_LIMIT_USD}


def over_limit(summary: dict) -> bool:
    return summary["limit_usd"] is not None and summary["cost_usd"] >= summary["limit_usd"]


def near_limit(summary: dict) -> bool:
    return (summary["limit_usd"] is not None
            and summary["cost_usd"] >= summary["limit_usd"] * settings.LIMIT_WARN_RATIO)

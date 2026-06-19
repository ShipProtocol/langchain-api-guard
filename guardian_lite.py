"""
BotGuardian Lite: Basic OpenAI API Cost Tracker
=================================================

A lightweight, single-threaded utility for logging OpenAI API token usage
and estimated cost to a local SQLite ledger.

Requires: pip install openai>=1.0.0

Note: This is the "Lite" tier. It intentionally does not include
async-safe queuing, signal handling, or circuit breakers — see README
for the full feature comparison.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import sqlite3
import sys
from functools import wraps
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar

from openai import OpenAI

try:
    from typing import ParamSpec
except ImportError:  # pragma: no cover - Python < 3.10 fallback
    from typing_extensions import ParamSpec  # type: ignore[import]

P = ParamSpec("P")
R = TypeVar("R")

DB_NAME: str = "api_ledger.db"

# Approximate costs per 1M tokens (Update as OpenAI pricing changes)
MODEL_COSTS: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"prompt": 5.0 / 1_000_000, "completion": 15.0 / 1_000_000},
    "gpt-4-turbo": {"prompt": 10.0 / 1_000_000, "completion": 30.0 / 1_000_000},
    "gpt-3.5-turbo": {"prompt": 0.5 / 1_000_000, "completion": 1.5 / 1_000_000},
}

# Dedicated logger so library consumers can configure/silence it independently
# of their own application logging.
logger = logging.getLogger("botguardian_lite")
if not logger.handlers:
    _handler = logging.StreamHandler(stream=sys.stderr)
    _handler.setFormatter(logging.Formatter("[BotGuardian] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.WARNING)


@contextlib.contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection that auto-commits/rolls back and always closes.

    `sqlite3.Connection` objects act as context managers for transactions
    (commit on success, rollback on exception) but do **not** close the
    underlying connection on `__exit__`. Wrapping with `contextlib.closing`
    guarantees the file handle is released even if the transaction itself
    raises.
    """
    with contextlib.closing(sqlite3.connect(DB_NAME)) as connection:
        with connection:
            yield connection


def init_ledger() -> None:
    """Initialize the local SQLite database and ensure the schema exists."""
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                estimated_cost_usd REAL NOT NULL
            )
            """
        )


def _calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated USD cost for a call, defaulting to 0 for unknown models."""
    rates = MODEL_COSTS.get(model)
    if rates is None:
        logger.warning(
            "No pricing entry for model '%s'; cost will be logged as $0.00.", model
        )
        return 0.0
    return (prompt_tokens * rates["prompt"]) + (completion_tokens * rates["completion"])


def _persist_usage(
    model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int
) -> None:
    """Write a single usage record to the ledger.

    Any failure here (locked DB, disk full, permissions, etc.) is logged
    as a warning and swallowed — a logging failure must never take down
    the caller's application or mask the original API response.
    """
    try:
        cost = _calculate_cost(model, prompt_tokens, completion_tokens)
        with _get_connection() as conn:
            conn.execute(
                """
                INSERT INTO api_calls
                    (timestamp, model, prompt_tokens, completion_tokens,
                     total_tokens, estimated_cost_usd)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost,
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("Failed to write usage record to ledger: %s", exc)


def track_openai_call(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that logs token usage/cost for a function returning an OpenAI response.

    The wrapped function's exceptions (e.g. `openai.RateLimitError`,
    `openai.APIConnectionError`) propagate completely untouched — this
    decorator only observes the response *after* a successful call and
    never wraps the call itself in a try/except.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        response = func(*args, **kwargs)

        usage: Optional[Any] = getattr(response, "usage", None)
        if usage is not None:
            model = str(kwargs.get("model", "unknown"))
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", prompt_tokens + completion_tokens)
            _persist_usage(model, prompt_tokens, completion_tokens, total_tokens)
        else:
            logger.warning(
                "Response from '%s' had no usage data; call was not logged.",
                getattr(func, "__name__", "wrapped function"),
            )

        return response

    return wrapper


# Initialize DB on import
init_ledger()

# --- USAGE EXAMPLE ---
client = OpenAI()


@track_openai_call
def safe_chat_completion(**kwargs: Any) -> Any:
    """Wrapper for standard OpenAI chat completions with usage tracking."""
    return client.chat.completions.create(**kwargs)


if __name__ == "__main__":
    print("Executing tracked API call...")
    response = safe_chat_completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Explain SIGTERM vs SIGKILL in one sentence."}],
    )
    print(f"Response: {response.choices[0].message.content}")
    print(f"Cost logged to {DB_NAME}")

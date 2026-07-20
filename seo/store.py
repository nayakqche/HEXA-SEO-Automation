"""
Durable storage for generated post files, backed by Postgres.

Render's web filesystem is ephemeral: every deploy or idle restart wipes
`outputs/`, which kills preview / edit / download links for posts generated in
an earlier container. When `DATABASE_URL` is set we mirror each post's files
into a `post_files` table, and serve or restore them on demand so those links
keep working across restarts.

If `DATABASE_URL` is not configured every function is a safe no-op, so the app
still runs (just without durability).
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text

from . import db

OUTPUT_DIR = Path("outputs")
_inited = False


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _init() -> None:
    global _inited
    if _inited:
        return
    with db.session() as s:
        s.execute(text(
            "CREATE TABLE IF NOT EXISTS post_files ("
            " rel TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " content BYTEA NOT NULL,"
            " updated TIMESTAMP DEFAULT now(),"
            " PRIMARY KEY (rel, name))"
        ))
    _inited = True


def save_dir(rel: str, dir_path: Path) -> None:
    """Mirror every file in a post directory into the database (upsert)."""
    if not enabled():
        return
    try:
        _init()
        with db.session() as s:
            for f in sorted(Path(dir_path).iterdir()):
                if not f.is_file():
                    continue
                s.execute(text(
                    "INSERT INTO post_files (rel, name, content, updated) "
                    "VALUES (:rel, :name, :content, now()) "
                    "ON CONFLICT (rel, name) DO UPDATE SET "
                    "content = EXCLUDED.content, updated = now()"
                ), {"rel": rel, "name": f.name, "content": f.read_bytes()})
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        print(f"[store] save failed for {rel}: {exc}", flush=True)


def get_file(rel: str, name: str) -> bytes | None:
    if not enabled():
        return None
    try:
        _init()
        with db.session() as s:
            row = s.execute(
                text("SELECT content FROM post_files WHERE rel = :rel AND name = :name"),
                {"rel": rel, "name": name},
            ).first()
        return bytes(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001
        print(f"[store] get failed for {rel}/{name}: {exc}", flush=True)
        return None


def has_post(rel: str) -> bool:
    return get_file(rel, "post.json") is not None


def restore_dir(rel: str, dest: Path) -> bool:
    """Write every stored file for `rel` back to `dest`. True if anything was restored."""
    if not enabled():
        return False
    try:
        _init()
        with db.session() as s:
            rows = s.execute(
                text("SELECT name, content FROM post_files WHERE rel = :rel"),
                {"rel": rel},
            ).all()
        if not rows:
            return False
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for name, content in rows:
            (dest / name).write_bytes(bytes(content))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[store] restore failed for {rel}: {exc}", flush=True)
        return False

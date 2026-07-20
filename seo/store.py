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
    """Mirror every file in a post directory into the database (upsert).

    Failures are logged loudly (with a stack trace) so a silently broken
    persistence path is impossible to miss in the Render logs.
    """
    if not enabled():
        return
    try:
        _init()
        saved = 0
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
                saved += 1
        print(f"[store] saved {saved} file(s) for {rel}", flush=True)
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        import traceback as _tb
        print(f"[store] SAVE FAILED for {rel}: {type(exc).__name__}: {exc}\n"
              + _tb.format_exc(), flush=True)


def list_rels() -> list[str]:
    """Distinct post rels currently in durable storage."""
    if not enabled():
        return []
    try:
        _init()
        with db.session() as s:
            rows = s.execute(text("SELECT DISTINCT rel FROM post_files")).all()
        return [r[0] for r in rows]
    except Exception as exc:  # noqa: BLE001
        print(f"[store] list_rels failed: {exc}", flush=True)
        return []


def restore_all_to_disk(base: Path | None = None) -> int:
    """Restore every stored post back to disk. Runs at boot, safe to re-run.

    Skips posts that already have post.json on disk, so an already-warm
    container doesn't waste cycles reading the same rows again.
    """
    if not enabled():
        return 0
    base = Path(base or OUTPUT_DIR)
    restored = 0
    for rel in list_rels():
        target = base / rel
        if (target / "post.json").exists():
            continue
        if restore_dir(rel, target):
            restored += 1
    if restored:
        print(f"[store] restored {restored} post(s) from durable storage on boot",
              flush=True)
    return restored


def health() -> dict:
    """Prove the write path works: write and delete a probe row."""
    if not enabled():
        return {"enabled": False}
    try:
        _init()
        with db.session() as s:
            s.execute(text(
                "INSERT INTO post_files (rel, name, content, updated) "
                "VALUES ('__probe__', 'ping', :b, now()) "
                "ON CONFLICT (rel, name) DO UPDATE SET updated = now()"
            ), {"b": b"ok"})
            row = s.execute(text(
                "SELECT content FROM post_files WHERE rel = '__probe__' AND name = 'ping'"
            )).first()
            s.execute(text("DELETE FROM post_files WHERE rel = '__probe__'"))
        stored = len(list_rels())
        return {"enabled": True, "write": bool(row and bytes(row[0]) == b"ok"),
                "posts_stored": stored}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "write": False, "error": f"{type(exc).__name__}: {exc}"}


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

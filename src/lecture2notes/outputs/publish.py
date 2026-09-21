"""Transactional publication: manifest, backup, verify, replace, or roll back.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/publish_transaction.py

Publishing a lecture means replacing several files at once. Replacing them one
by one means that a failure halfway leaves a course folder holding a new viewer,
an old chapter file and a note from neither: the pieces disagree, and nothing
records that they do.

So every publication is a transaction. The manifest is written before anything
moves and records, per file, whether a live copy existed and what both sides
hash to. Existing files are backed up under a timestamped run, each replacement
goes through a temp copy that is hashed before the rename, and any failure
restores every file already touched. The manifest is re-persisted at each state
change, so an interrupted run can be recovered from disk instead of guessed at.

The course-homepage helper of the source is not ported: it hard-coded one
course's lecture count.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from lecture2notes.schema.model import write_json_atomic

#: A sortable local timestamp, with a numeric suffix when two runs collide.
RUN_ID_RE = re.compile(r"^(\d{8}-\d{6})(?:-(\d{2,}))?$")


@dataclass
class ManifestEntry:
    """One file's before and after, with both hashes."""

    relative_path: str
    live: str
    staged: str
    backup: str
    old_exists: bool
    old_sha256: Optional[str]
    new_sha256: str
    state: str = "prepared"
    verified_sha256: Optional[str] = None


@dataclass
class PublishManifest:
    """The transaction record; it is the recovery evidence, not a log."""

    run_id: str
    lecture_id: str
    backup_path: str
    manifest_path: str
    recovery_path: str
    created_at: str
    updated_at: str
    state: str
    entries: List[ManifestEntry]
    error: Optional[str] = None
    rollback_errors: List[str] = field(default_factory=list)
    persistence_errors: List[str] = field(default_factory=list)


@dataclass
class PublishResult:
    ok: bool
    rolled_back: bool
    error: Optional[str] = None


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def validate_run_id(run_id: str) -> str:
    match = RUN_ID_RE.fullmatch(run_id)
    if match is None:
        raise ValueError("run ID must be a sortable local timestamp YYYYMMDD-HHMMSS[-NN]")
    datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    return run_id


def resolve_run_id(backup_root: Path, now: Optional[datetime] = None) -> str:
    """The current timestamp, with the lowest unused suffix if it is taken."""
    current = now or datetime.now()
    base = current.strftime("%Y%m%d-%H%M%S")
    root = Path(backup_root)
    if not (root / base).exists():
        return base
    suffix = 1
    while (root / ("%s-%02d" % (base, suffix))).exists():
        suffix += 1
    return "%s-%02d" % (base, suffix)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_filesystem(live_root: Path, stage_root: Path) -> bool:
    """A rename is only atomic within one filesystem, so this is checked first."""
    return Path(live_root).anchor.casefold() == Path(stage_root).anchor.casefold()


def build_manifest(
    lecture_id: str,
    live_root: Path,
    stage_root: Path,
    backup_root: Path,
    relative_paths: Sequence[str],
    run_id: Optional[str] = None,
) -> PublishManifest:
    """Validate every input and hash both sides. Writes nothing."""
    live_root, stage_root, backup_root = Path(live_root), Path(stage_root), Path(backup_root)
    if not same_filesystem(live_root, stage_root):
        raise ValueError("live and staging roots must use the same filesystem anchor")
    resolved = resolve_run_id(backup_root) if run_id is None else validate_run_id(run_id)
    transaction_root = backup_root / resolved / "lectures" / lecture_id
    manifest_path = transaction_root / "transaction.json"
    if manifest_path.exists():
        raise FileExistsError("transaction already exists: %s" % manifest_path)

    entries: List[ManifestEntry] = []
    for relative_path in relative_paths:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe publication path: %s" % relative_path)
        live = live_root / relative
        staged = stage_root / relative
        if not staged.is_file():
            raise FileNotFoundError("staged manifest input missing: %s" % relative_path)
        old_exists = live.is_file()
        entries.append(ManifestEntry(
            relative_path=relative.as_posix(),
            live=str(live),
            staged=str(staged),
            backup=str(transaction_root / "files" / relative),
            old_exists=old_exists,
            old_sha256=sha256(live) if old_exists else None,
            new_sha256=sha256(staged),
        ))
    now = _now()
    return PublishManifest(
        run_id=resolved,
        lecture_id=lecture_id,
        backup_path=str(backup_root / resolved),
        manifest_path=str(manifest_path),
        recovery_path=str(backup_root / "_recovery" / ("%s-%s.json" % (resolved, lecture_id))),
        created_at=now,
        updated_at=now,
        state="prepared",
        entries=entries,
    )


def _write_manifest(manifest: PublishManifest) -> None:
    manifest.updated_at = _now()
    write_json_atomic(Path(manifest.manifest_path), asdict(manifest))


def _write_recovery_evidence(manifest: PublishManifest) -> None:
    payload = asdict(manifest)
    payload["recovery_required"] = bool(manifest.rollback_errors)
    write_json_atomic(Path(manifest.recovery_path), payload)


def _persist_or_record(manifest: PublishManifest) -> bool:
    try:
        _write_manifest(manifest)
        return True
    except Exception as exc:
        manifest.persistence_errors.append("%s: %s" % (type(exc).__name__, exc))
        return False


def _require_manifest_write(manifest: PublishManifest) -> None:
    if not _persist_or_record(manifest):
        raise IOError("transaction manifest persistence failed")


def _replace_from_staging(entry: ManifestEntry, replace_func: Callable[[str, str], Any]) -> None:
    live = Path(entry.live)
    staged = Path(entry.staged)
    temporary = live.with_name(".%s.publish-%s.tmp" % (live.name, uuid.uuid4().hex))
    live.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(staged, temporary)
        if sha256(temporary) != entry.new_sha256:
            raise IOError("temporary copy hash mismatch: %s" % entry.relative_path)
        replace_func(str(temporary), str(live))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_old(entry: ManifestEntry) -> None:
    live = Path(entry.live)
    if not entry.old_exists:
        try:
            live.unlink()
        except FileNotFoundError:
            pass
        return
    backup = Path(entry.backup)
    if not backup.is_file() or entry.old_sha256 is None or sha256(backup) != entry.old_sha256:
        raise IOError("backup verification failed: %s" % entry.relative_path)
    temporary = live.with_name(".%s.rollback-%s.tmp" % (live.name, uuid.uuid4().hex))
    try:
        shutil.copy2(backup, temporary)
        os.replace(temporary, live)
        if sha256(live) != entry.old_sha256:
            raise IOError("rollback hash mismatch: %s" % entry.relative_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def rollback(manifest: PublishManifest) -> PublishResult:
    """Undo every replacement; delete the files this transaction created."""
    errors: List[str] = []
    for entry in reversed(manifest.entries):
        if entry.state not in {"replace_started", "replaced"}:
            continue
        try:
            _restore_old(entry)
            entry.state = "rolled_back"
        except Exception as exc:
            errors.append("%s: %s: %s" % (entry.relative_path, type(exc).__name__, exc))
    manifest.rollback_errors.extend(errors)
    manifest.state = "recovery_required" if errors else "rolled_back"
    persisted = _persist_or_record(manifest)
    if not persisted or manifest.persistence_errors:
        try:
            _write_recovery_evidence(manifest)
        except Exception as exc:
            manifest.rollback_errors.append(
                "recovery evidence: %s: %s" % (type(exc).__name__, exc)
            )
            manifest.state = "recovery_required"
    return PublishResult(False, not errors, manifest.error)


def publish(
    manifest: PublishManifest,
    replace_func: Callable[[str, str], Any] = os.replace,
) -> PublishResult:
    """Commit every file, or restore the whole lecture to its previous state."""
    validate_run_id(manifest.run_id)
    try:
        _require_manifest_write(manifest)
        manifest.state = "backing_up"
        _require_manifest_write(manifest)
        for entry in manifest.entries:
            if entry.old_exists:
                backup = Path(entry.backup)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry.live, backup)
                if entry.old_sha256 is None or sha256(backup) != entry.old_sha256:
                    raise IOError("backup hash mismatch: %s" % entry.relative_path)
            entry.state = "backed_up"
            _require_manifest_write(manifest)

        manifest.state = "replacing"
        _require_manifest_write(manifest)
        for entry in manifest.entries:
            entry.state = "replace_started"
            _require_manifest_write(manifest)
            _replace_from_staging(entry, replace_func)
            if sha256(Path(entry.live)) != entry.new_sha256:
                raise IOError("live hash mismatch: %s" % entry.relative_path)
            entry.verified_sha256 = entry.new_sha256
            entry.state = "replaced"
            _require_manifest_write(manifest)
        manifest.state = "committed"
        _require_manifest_write(manifest)
        return PublishResult(True, False)
    except Exception as exc:
        manifest.error = "%s: %s" % (type(exc).__name__, exc)
        result = rollback(manifest)
        if manifest.persistence_errors:
            try:
                _write_recovery_evidence(manifest)
            except Exception:
                pass
        return result


def load_manifest(path: Path) -> PublishManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = [ManifestEntry(**entry) for entry in raw["entries"]]
    return PublishManifest(
        run_id=raw["run_id"],
        lecture_id=raw["lecture_id"],
        backup_path=raw.get("backup_path", str(Path(raw["manifest_path"]).parents[3])),
        manifest_path=raw["manifest_path"],
        recovery_path=raw["recovery_path"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        state=raw["state"],
        entries=entries,
        error=raw.get("error"),
        rollback_errors=list(raw.get("rollback_errors", [])),
        persistence_errors=list(raw.get("persistence_errors", [])),
    )


def recover(path: Path) -> PublishResult:
    """Finish an interrupted transaction by rolling it back."""
    manifest = load_manifest(path)
    if manifest.state == "committed":
        return PublishResult(True, False)
    return rollback(manifest)


def publish_summary(manifest: PublishManifest) -> Dict[str, Any]:
    """The ``<stem>.publish.json`` payload: every file with the hash published."""
    return {
        "run_id": manifest.run_id,
        "lecture_id": manifest.lecture_id,
        "state": manifest.state,
        "published_at": manifest.updated_at,
        "files": [
            {
                "path": entry.relative_path,
                "sha256": entry.verified_sha256 or entry.new_sha256,
                "replaced": entry.old_exists,
            }
            for entry in manifest.entries
        ],
    }


__all__ = [
    "ManifestEntry",
    "PublishManifest",
    "PublishResult",
    "RUN_ID_RE",
    "build_manifest",
    "load_manifest",
    "publish",
    "publish_summary",
    "recover",
    "resolve_run_id",
    "rollback",
    "same_filesystem",
    "sha256",
    "validate_run_id",
]

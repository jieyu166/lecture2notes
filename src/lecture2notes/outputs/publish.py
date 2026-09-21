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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lecture2notes import _out
from lecture2notes.schema.model import write_json_atomic

#: A sortable local timestamp, with a numeric suffix when two runs collide.
RUN_ID_RE = re.compile(r"^(\d{8}-\d{6})(?:-(\d{2,}))?$")

#: Where the backups of a destination live: a sibling of the destination, so a
#: published folder never carries its own previous versions inside itself and a
#: hub built over the destination cannot accidentally index them.
BACKUP_SUFFIX = ".l2n-backup"

#: The record a completed publication leaves in the destination.
SUMMARY_SUFFIX = ".publish.json"

#: Temporary copies are hidden and carry the run ID, so a crashed run leaves
#: something identifiable rather than a mystery file next to the real ones.
TEMP_PREFIX = "."


class PublishFailure(IOError):
    """A failure that knows which file it happened on."""

    def __init__(self, message: str, relative_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.relative_path = relative_path


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
    #: Which file the transaction died on, when it died on one. The message in
    #: ``error`` already names it, but a caller should not have to parse prose
    #: to tell the user which file to look at.
    failed_path: Optional[str] = None


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


def _nearest_existing(path: Path) -> Path:
    """*path* if it exists, else the closest ancestor that does.

    A destination folder is routinely created by the publication itself, so the
    check has to be able to answer "which filesystem would it land on" for a
    path that is not there yet. The walk stops at the root, which always exists.
    """
    current = Path(path).resolve()
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def same_filesystem(live_root: Path, stage_root: Path) -> bool:
    """A rename is only atomic within one filesystem, so this is checked first.

    Both sides are resolved before anything is compared. Comparing the unresolved
    ``anchor`` made a relative source (anchor ``""``) look like a different
    filesystem from an absolute destination, so ``publish`` refused with exit 2
    on the ordinary case of running it from inside the source folder.

    On Windows the filesystem is the drive, so the resolved anchors are compared
    case-insensitively; ``st_dev`` there is a volume serial number that is not
    stable enough to reason about. Elsewhere it is ``st_dev``, which is what
    actually decides whether ``os.replace`` can be a rename, and which sees
    through bind mounts and separate mounts under one root.
    """
    live = _nearest_existing(live_root)
    stage = _nearest_existing(stage_root)
    if os.name == "nt":
        return live.anchor.casefold() == stage.anchor.casefold()
    try:
        return os.stat(live).st_dev == os.stat(stage).st_dev
    except OSError:  # pragma: no cover - an unreadable path is not this rule's call
        return live.anchor == stage.anchor


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
            except Exception as evidence_exc:
                # The rollback already happened; what is lost here is the record
                # of it. Swallowing that silently is the worst case in this
                # module: the files are fine and nothing says the transaction
                # ever ran, so nobody knows to look.
                manifest.rollback_errors.append(
                    "recovery evidence: %s: %s"
                    % (type(evidence_exc).__name__, evidence_exc)
                )
                _out.say(
                    "warn",
                    "could not write recovery evidence to %s: %s"
                    % (manifest.recovery_path, evidence_exc),
                )
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
        failed_path=raw.get("failed_path"),
    )


def recover(path: Path) -> PublishResult:
    """Finish an interrupted transaction by rolling it back."""
    manifest = load_manifest(path)
    if manifest.state == "committed":
        return PublishResult(True, False)
    return rollback(manifest)


def publish_summary(manifest: PublishManifest) -> Dict[str, Any]:
    """The ``<stem>.publish.json`` payload: every file with the hash published."""
    replaced_any = any(entry.old_exists for entry in manifest.entries)
    return {
        "run_id": manifest.run_id,
        "lecture_id": manifest.lecture_id,
        "state": manifest.state,
        "published_at": manifest.updated_at,
        # Null when nothing was replaced: there is then no previous version to
        # go back to, and pointing at a directory holding only the transaction
        # record would imply there is.
        "backup_dir": manifest.backup_path if replaced_any else None,
        "files": [
            {
                "path": entry.relative_path,
                "source": entry.staged,
                "dest": entry.live,
                "sha256": entry.verified_sha256 or entry.new_sha256,
                "replaced": entry.old_exists,
            }
            for entry in manifest.entries
        ],
    }


# ==========================================================================
# the batch transaction behind `l2n publish`
# ==========================================================================
# `publish` above replaces one file at a time: copy, verify, rename, next. That
# is what the source did and it is recoverable, but it leaves a window in which
# the destination holds a mixture -- three new files, three old ones -- and the
# only way back out is the backups.
#
# `publish_batch` closes the window. Every file is copied to a temporary name
# and hashed first; only when all of them verify does anything get renamed. A
# verification failure therefore happens before the destination has been touched
# at all, which is what makes "the destination contains exactly the files it had
# before" true by construction rather than by the rollback working correctly.


def _verify_copy(path: Path, expected: str) -> bool:
    """Does the temporary copy hash to what the manifest recorded?

    A named function rather than an inline comparison because this is the point
    the rollback test has to fail on, and a test that had to corrupt a file on
    disk to get there would be testing the filesystem instead.
    """
    return sha256(Path(path)) == expected


def _temp_name(live: Path, run_id: str, position: int) -> Path:
    return live.with_name(
        "%s%s.publish-%s-%03d.tmp" % (TEMP_PREFIX, live.name, run_id, position)
    )


def _discard(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        _out.say("warn", "could not remove temporary file %s: %s" % (path, exc))


def publish_batch(
    manifest: PublishManifest,
    replace_func: Callable[[str, str], Any] = os.replace,
) -> PublishResult:
    """Back up, stage and verify every file, then rename them all, or roll back."""
    validate_run_id(manifest.run_id)
    staged: List[Tuple[ManifestEntry, Path]] = []
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
                    raise PublishFailure(
                        "backup hash mismatch: %s" % entry.relative_path,
                        entry.relative_path,
                    )
            entry.state = "backed_up"
        _require_manifest_write(manifest)

        manifest.state = "staging"
        _require_manifest_write(manifest)
        for position, entry in enumerate(manifest.entries):
            live = Path(entry.live)
            live.parent.mkdir(parents=True, exist_ok=True)
            temporary = _temp_name(live, manifest.run_id, position)
            shutil.copy2(entry.staged, temporary)
            staged.append((entry, temporary))
            if not _verify_copy(temporary, entry.new_sha256):
                raise PublishFailure(
                    "staged copy hash mismatch: %s" % entry.relative_path,
                    entry.relative_path,
                )

        manifest.state = "replacing"
        _require_manifest_write(manifest)
        for entry, temporary in staged:
            entry.state = "replace_started"
            replace_func(str(temporary), str(entry.live))
            entry.verified_sha256 = entry.new_sha256
            entry.state = "replaced"
        manifest.state = "committed"
        _require_manifest_write(manifest)
        return PublishResult(True, False)
    except Exception as exc:
        manifest.error = "%s: %s" % (type(exc).__name__, exc)
        manifest.failed_path = getattr(exc, "relative_path", None)
        return rollback(manifest)
    finally:
        # A committed entry's temporary name no longer exists; an abandoned
        # one's does. Both are handled the same way so no path leaves a .tmp
        # behind for someone to find later and wonder about.
        for _, temporary in staged:
            _discard(temporary)


# ==========================================================================
# `l2n publish <stem> --dest <dir>`
# ==========================================================================


@dataclass
class PublishOutcome:
    """What the command should print and exit with."""

    ok: bool
    code: int
    message: str
    files: List[str] = field(default_factory=list)
    failed_path: Optional[str] = None
    backup_dir: Optional[str] = None
    summary_path: Optional[str] = None


def _frame_names(source_dir: Path, stem: str) -> List[str]:
    """Every frame this lecture owns, from the manifest and from the document.

    Both are read because they can disagree and either disagreement is fatal to
    the published copy: a frame in the manifest but not the document is dead
    weight, a frame in the document but not the manifest is a broken embed at
    the destination. Publishing the union means the destination is at least as
    complete as the source.
    """
    names: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in names:
            names.append(value)

    manifest_path = source_dir / (stem + ".frames.json")
    if manifest_path.is_file():
        try:
            rows = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            rows = []
        if isinstance(rows, dict):
            rows = rows.get("frames") or []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                add(row.get("frame"))

    document_path = source_dir / (stem + ".json")
    if document_path.is_file():
        try:
            data = json.loads(document_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        for segment in (data.get("segments") or []) if isinstance(data, dict) else []:
            if not isinstance(segment, dict):
                continue
            add(segment.get("frame"))
            for item in segment.get("frames") or []:
                add(item)
            for item in segment.get("frame_ocr") or []:
                if isinstance(item, dict):
                    add(item.get("frame"))
    return names


def lecture_relative_paths(
    stem: str, source_dir: Path, pbf_enabled: bool = False
) -> List[str]:
    """The derivative set, in the order it is published.

    Order is fixed rather than sorted so a failure is reproducible: the third
    file is the third file on every machine. The canonical JSON comes first
    because everything else is a projection of it.
    """
    from lecture2notes.outputs.viewer import viewer_path

    source_dir = Path(source_dir)
    names: List[str] = []

    def add(path: Path) -> None:
        if not path.is_file():
            return
        try:
            relative = path.resolve().relative_to(source_dir.resolve()).as_posix()
        except ValueError:
            return
        if relative not in names:
            names.append(relative)

    document = source_dir / (stem + ".json")
    add(document)
    add(source_dir / (stem + ".srt"))
    add(source_dir / (stem + ".frames.json"))
    add(source_dir / (stem + ".v4.md"))
    add(viewer_path(document))
    if pbf_enabled:
        add(source_dir / (stem + ".pbf"))
    for name in _frame_names(source_dir, stem):
        add(source_dir / name)
    return names


def _remove_run_dir(run_dir: Path, backup_root: Path) -> None:
    """Delete a rolled-back run's backup directory, and the root if it emptied."""
    shutil.rmtree(run_dir, ignore_errors=True)
    try:
        if backup_root.is_dir() and not any(backup_root.iterdir()):
            backup_root.rmdir()
    except OSError as exc:  # pragma: no cover - a locked directory is not fatal
        _out.say("warn", "could not remove backup directory %s: %s" % (backup_root, exc))


def publish_lecture(
    stem: str,
    dest: Path,
    source_dir: Path,
    pbf_enabled: bool = False,
    run_id: Optional[str] = None,
    replace_func: Callable[[str, str], Any] = os.replace,
) -> PublishOutcome:
    """Publish one lecture's derivative set into *dest* as a single transaction."""
    source_dir = Path(source_dir)
    dest = Path(dest)

    if not same_filesystem(dest, source_dir):
        return PublishOutcome(
            False, 2,
            "publish refuses: %s and %s are on different filesystems, so the "
            "final rename could not be atomic" % (source_dir, dest),
        )
    if dest.resolve() == source_dir.resolve():
        return PublishOutcome(
            False, 2, "publish refuses: the destination is the source folder",
        )

    relative = lecture_relative_paths(stem, source_dir, pbf_enabled=pbf_enabled)
    if not relative:
        return PublishOutcome(
            False, 2, "publish: nothing to publish for %s" % stem,
        )

    dest.mkdir(parents=True, exist_ok=True)
    backup_root = dest.parent / (dest.name + BACKUP_SUFFIX)
    try:
        manifest = build_manifest(
            stem, dest, source_dir, backup_root, relative, run_id=run_id
        )
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        return PublishOutcome(False, 2, "publish: %s" % exc)

    run_dir = Path(manifest.backup_path)
    result = publish_batch(manifest, replace_func=replace_func)
    if not result.ok:
        if result.rolled_back and not manifest.rollback_errors:
            _remove_run_dir(run_dir, backup_root)
            recovery = None
        else:
            recovery = str(run_dir)
        return PublishOutcome(
            False, 2,
            manifest.error or "publish failed",
            failed_path=manifest.failed_path,
            backup_dir=recovery,
        )

    summary = publish_summary(manifest)
    summary_path = write_json_atomic(dest / (stem + SUMMARY_SUFFIX), summary)
    return PublishOutcome(
        True, 0,
        "publish: %d files -> %s" % (len(relative), dest),
        files=relative,
        backup_dir=summary["backup_dir"],
        summary_path=str(summary_path),
    )


__all__ = [
    "BACKUP_SUFFIX",
    "ManifestEntry",
    "PublishFailure",
    "PublishManifest",
    "PublishOutcome",
    "PublishResult",
    "RUN_ID_RE",
    "SUMMARY_SUFFIX",
    "TEMP_PREFIX",
    "build_manifest",
    "lecture_relative_paths",
    "load_manifest",
    "publish",
    "publish_batch",
    "publish_lecture",
    "publish_summary",
    "recover",
    "resolve_run_id",
    "rollback",
    "same_filesystem",
    "sha256",
    "validate_run_id",
]

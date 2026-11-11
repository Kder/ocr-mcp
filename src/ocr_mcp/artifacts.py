"""Private artifact jobs and an independent retention/quota cleanup command."""

import argparse
import errno
import json
import math
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

from .core.http_config import RemoteOCRConfig

_JOB_ID = re.compile(r"[0-9a-f]{32}")
_MARKER = ".ocr-mcp-job.json"


@contextmanager
def _lock(path: Path, *, blocking: bool):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Artifact lock must be a regular file")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"1")
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
        except OSError as exc:
            if blocking or exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _private_root(root: Path, *, create: bool = False) -> Path:
    root = root.expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("Artifact root must not be a filesystem root")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.exists() and os.name == "posix":
        info = root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("Artifact root must be owned by this user and not group/world writable")
    return root


@contextmanager
def artifact_job(root: Path, job_id: str):
    """Create an owner-only directory; cleanup cannot remove a running job."""
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid artifact job ID")
    root = _private_root(root, create=True)
    with _lock(root / (job_id + ".lock"), blocking=True):
        job = root / job_id
        job.mkdir(mode=0o700)
        marker = job / _MARKER
        with marker.open("x", encoding="utf-8") as file:
            json.dump({"format": "ocr-mcp-artifact-v1", "created_at": time.time()}, file)
        try:
            yield job
        finally:
            # Retention age starts at completion/failure, not the last raw image write.
            marker.touch()


def _job_age(path: Path) -> float | None:
    marker = path / _MARKER
    if path.is_symlink() or not path.is_dir() or not _JOB_ID.fullmatch(path.name) or marker.is_symlink():
        return None
    try:
        with marker.open(encoding="utf-8") as file:
            metadata = json.loads(file.read(4096))
        if metadata.get("format") != "ocr-mcp-artifact-v1":
            return None
        return marker.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        return None


def _size(path: Path) -> int:
    total = 0
    for directory, _, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                info = (Path(directory) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            except FileNotFoundError:
                pass
    return total


def cleanup_artifacts(
    root: Path, retention_days: float, max_gb: float, *, apply: bool = False, now: float | None = None
) -> dict:
    """Expire marked jobs, then prune oldest inactive jobs to the managed-size target.

    Active jobs, symlinks, unmarked directories and files outside this root are never
    deleted. Quotas are cleanup targets, not real-time write limits. No scans occur
    in OCR requests; invoke this command from cron/a timer for automatic retention.
    """
    if any(not math.isfinite(n) or n < 0 for n in (retention_days, max_gb)):
        raise ValueError("Retention and quota must be finite and nonnegative")
    root = _private_root(root)
    report = {
        "dry_run": not apply,
        "jobs": [],
        "active_skipped": [],
        "managed_bytes_remaining": 0,
        "quota_satisfied": True,
    }
    if not root.exists():
        return report
    now = time.time() if now is None else now
    limit = int(max_gb * 1024**3)
    with _lock(root / ".cleanup.lock", blocking=False) as acquired:
        if not acquired:
            return {**report, "error": "Another cleanup process is running"}
        jobs = [(path, age, _size(path)) for path in root.iterdir() if (age := _job_age(path)) is not None]
        total = sum(size for _, _, size in jobs)
        for path, age, size in sorted(jobs, key=lambda row: row[1]):
            expired = retention_days > 0 and now - age >= retention_days * 86400
            over_quota = max_gb > 0 and total > limit
            if not expired and not over_quota:
                continue
            with _lock(root / (path.name + ".lock"), blocking=False) as inactive:
                if not inactive:
                    report["active_skipped"].append(path.name)
                    continue
                # A job may have finished while inventory was being read.
                current_age = _job_age(path)
                if current_age is None:
                    continue
                expired = retention_days > 0 and now - current_age >= retention_days * 86400
                if not expired and not over_quota:
                    continue
                if apply:
                    shutil.rmtree(path)
                total -= size
                report["jobs"].append({"id": path.name, "bytes": size, "reason": "expired" if expired else "quota"})
            if apply:
                (root / (path.name + ".lock")).unlink(missing_ok=True)
        report.update(managed_bytes_remaining=total, quota_satisfied=max_gb == 0 or total <= limit)
    return report


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    config = RemoteOCRConfig()
    parser = argparse.ArgumentParser(description="Clean OCR artifact jobs by age and managed size (dry-run by default)")
    parser.add_argument("--apply", action="store_true", help="Delete eligible completed/failed jobs")
    args = parser.parse_args()
    report = cleanup_artifacts(
        config.crosspage_artifact_dir, config.artifact_retention_days, config.artifact_max_gb, apply=args.apply
    )
    print(json.dumps(report))  # noqa: T201 -- CLI output, never document contents

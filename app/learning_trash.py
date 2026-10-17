"""Confirmed cleanup of flat application archives, using directory-relative I/O."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat


class TrashUnsafe(ValueError):
    pass


class TrashChanged(ValueError):
    pass


class TrashDeleteFailed(OSError):
    def __init__(self, deleted):
        self.deleted = deleted
        super().__init__("trash cleanup incomplete")


_DIRECTORY_OPTIONS = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
FLAGS = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
         if all(hasattr(os, name) for name in _DIRECTORY_OPTIONS) else None)
ARCHIVE_NAME = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{32}\Z")


@contextmanager
def _trash_directory(root):
    # The caller supplies its configured store root, never a client-provided path.
    # Keep imports usable by the legacy Windows backend; never weaken the
    # no-follow/directory-relative checks on platforms without these primitives.
    if FLAGS is None:
        raise TrashUnsafe("safe archive cleanup is unavailable on this platform")
    parent = os.open(root, FLAGS)
    directory = None
    try:
        try:
            directory = os.open(".trash", FLAGS, dir_fd=parent)
        except FileNotFoundError:
            pass
        yield directory
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def _node(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _inventory(directory):
    if directory is None:
        return {"directory": None, "records": []}
    records = []
    for name in sorted(os.listdir(directory)):
        if not ARCHIVE_NAME.fullmatch(name):
            raise TrashUnsafe("unrecognized archive")
        child = os.open(name, FLAGS, dir_fd=directory)
        try:
            files = {}
            for filename in sorted(os.listdir(child)):
                info = os.stat(filename, dir_fd=child, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise TrashUnsafe("archive contains a link or non-file")
                files[filename] = _node(info)
            if files:
                if "record.json" not in files:
                    raise TrashUnsafe("missing archive manifest")
                descriptor = os.open("record.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=child)
                with os.fdopen(descriptor, "rb") as stream:
                    if _node(os.fstat(stream.fileno())) != files["record.json"]:
                        raise TrashUnsafe("manifest changed while reading")
                    content = stream.read(32_000_001)
                if len(content) > 32_000_000:
                    raise TrashUnsafe("archive manifest exceeds verification limit")
                try:
                    manifest = json.loads(content)
                    doc = manifest["document"]
                    filename = doc["filename"]
                    if (manifest["format"] != 1 or not isinstance(filename, str) or not filename
                            or Path(filename).name != filename or "\\" in filename
                            or filename in {".", ".."}
                            or not re.fullmatch(r"[0-9a-f]{32}", doc["id"])):
                        raise ValueError("invalid archive identity")
                    allowed = {filename + ".md", filename + ".user-notes.md"}
                    if set(manifest["original_files"]) != allowed or set(files) - {"record.json"} - allowed:
                        raise ValueError("unexpected archive file")
                except (KeyError, TypeError, ValueError):
                    raise TrashUnsafe("invalid application archive") from None
            # Empty folders can remain after an interrupted final rmdir. They
            # must still have the app's exact timestamp/random-ID naming form.
            records.append({"name": name, "node": _node(os.fstat(child)), "files": files})
        finally:
            os.close(child)
    return {"directory": _node(os.fstat(directory)), "records": records}


def _fingerprint(inventory):
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def preview_trash(root):
    with _trash_directory(root) as directory:
        inventory = _inventory(directory)
        return {"count": len(inventory["records"]), "fingerprint": _fingerprint(inventory)}


def empty_trash(root, expected_fingerprint):
    """Never follows links, recurses, or acts on a path supplied by the client."""
    with _trash_directory(root) as directory:
        inventory = _inventory(directory)
        if _fingerprint(inventory) != expected_fingerprint:
            raise TrashChanged("trash changed after confirmation")
        deleted = 0
        try:
            for record in inventory["records"]:
                name = record["name"]
                child = os.open(name, FLAGS, dir_fd=directory)
                try:
                    if _node(os.fstat(child)) != record["node"] or set(os.listdir(child)) != set(record["files"]):
                        raise TrashUnsafe("archive changed before deletion")
                    # Keep the manifest until last, so a partial failure can be
                    # inspected and retried with a fresh explicit confirmation.
                    for filename in sorted(record["files"], key=lambda n: (n == "record.json", n)):
                        parent_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if (parent_info.st_dev, parent_info.st_ino) != record["node"][:2]:
                            raise TrashUnsafe("archive directory was replaced")
                        if _node(os.stat(filename, dir_fd=child, follow_symlinks=False)) != record["files"][filename]:
                            raise TrashUnsafe("archive file was replaced")
                        os.unlink(filename, dir_fd=child)
                    parent_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (parent_info.st_dev, parent_info.st_ino) != record["node"][:2]:
                        raise TrashUnsafe("archive directory was replaced")
                    os.rmdir(name, dir_fd=directory)
                    deleted += 1
                finally:
                    os.close(child)
        except (OSError, TrashUnsafe):
            raise TrashDeleteFailed(deleted) from None
        return {"deleted": deleted, "remaining": 0}

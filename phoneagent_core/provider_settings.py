"""Safe local configuration for provider API keys.

The module never returns key material. ``root`` is the private PhoneAgent
configuration directory (the directory containing secrets.json).
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import stat
import tempfile
import threading
from pathlib import Path

_FIELDS = ("deepseek", "gemini", "exa")
_lock = threading.RLock()
_JOURNAL = ".provider-settings-transaction.json"


def _env_names(field: str) -> tuple[str, ...]:
    return (("PHONEAGENT_GEMINI_KEY",) if field == "gemini" else
            ("PHONEAGENT_" + field.upper() + "_KEY",))


def _default_key_reader(root: Path, field: str):
    for name in _env_names(field):
        value = os.environ.get(name)
        if value:
            return {"configured": True, "env_managed": True}
    if field == "gemini":
        source = root / "gemini.key"
        try:
            if source.read_text("utf-8-sig").strip():
                return {"configured": True, "env_managed": False}
        except OSError:
            pass
    try:
        data = json.loads((root / "secrets.json").read_text("utf-8"))
        return {"configured": bool(data.get(field + "_key")), "env_managed": False}
    except (OSError, ValueError, AttributeError):
        return {"configured": False, "env_managed": False}


def _read_state(root: Path, key_reader):
    result = {}
    env_managed = {}
    for field in _FIELDS:
        if key_reader is None:
            info = _default_key_reader(root, field)
        else:
            try:
                info = key_reader(field + "_key")
            except TypeError:
                info = key_reader(root, field + "_key")
        if isinstance(info, dict):
            configured = bool(info.get("configured", info.get("key")))
            managed = bool(info.get("env_managed", False))
        else:
            configured = bool(info)
            managed = False
        result[field + "_key_configured"] = configured
        env_managed[field + "_key"] = managed
    result["env_managed"] = env_managed
    return result


def status(root, key_reader=None):
    """Return only configured flags and environment-management metadata."""
    root = Path(root)
    with _locked(root):
        _recover(root)
        return _read_state(root, key_reader)


def _validate(body):
    if not isinstance(body, dict):
        raise ValueError("请求必须是 JSON 对象")
    allowed = {field + "_key" for field in _FIELDS}
    allowed |= {"clear_" + field + "_key" for field in _FIELDS}
    unknown = set(body) - allowed
    if unknown:
        raise ValueError("包含未知设置字段")
    updates = {}
    clears = set()
    for field in _FIELDS:
        key_name = field + "_key"
        clear_name = "clear_" + key_name
        value = body.get(key_name)
        clear = body.get(clear_name, False)
        if clear_name in body and type(clear) is not bool:
            raise ValueError(clear_name + " 必须是布尔值")
        if key_name in body:
            if not isinstance(value, str):
                raise ValueError(key_name + " 必须是字符串")
            if len(value) > 8192 or "\n" in value or "\r" in value:
                raise ValueError(key_name + " 长度或格式无效")
        nonblank = key_name in body and bool(value.strip())
        if clear and nonblank:
            raise ValueError("不能同时设置和清除同一密钥")
        if clear:
            clears.add(field)
        elif nonblank:
            updates[field] = value.strip()
    return updates, clears


def update(root, body, key_reader=None):
    """Validate and atomically update provider keys, then return safe status."""
    updates, clears = _validate(body)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with _locked(root):
        _recover(root)
        secrets_path = root / "secrets.json"
        gemini_path = root / "gemini.key"
        try:
            secrets = json.loads(secrets_path.read_text("utf-8"))
            if not isinstance(secrets, dict):
                raise ValueError("secrets.json 必须是 JSON 对象")
        except FileNotFoundError:
            secrets = {}
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("secrets.json 格式无效") from exc

        changed = {}
        for field, value in updates.items():
            if field == "gemini":
                changed[gemini_path] = (value + "\n").encode("utf-8")
                secrets.pop("gemini_key", None)
            else:
                secrets[field + "_key"] = value
        for field in clears:
            if field == "gemini":
                changed[gemini_path] = None
                secrets.pop("gemini_key", None)
            else:
                secrets.pop(field + "_key", None)
        changed[secrets_path] = (json.dumps(secrets, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        _commit(root, changed)
        return _read_state(root, key_reader)


@contextlib.contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(root, 0o700)
    lock_path = root / ".provider-settings.lock"
    with _lock:
        handle = open(lock_path, "a+b")
        try:
            if os.name != "nt":
                os.chmod(lock_path, 0o600)
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _mode(path: Path):
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return 0o600


def _atomic_write(path: Path, data: bytes, mode: int):
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    tmp = Path(name)
    try:
        if hasattr(os, "fchmod"):
            # These writes only target provider secrets or the recovery journal.
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _fsync_dir(path: Path):
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _commit(root: Path, changed):
    snapshots = {}
    for path in changed:
        try:
            old = path.read_bytes()
            exists = True
            mode = _mode(path)
        except FileNotFoundError:
            old, exists, mode = b"", False, 0o600
        snapshots[path.name] = {"exists": exists, "mode": mode,
                                "data": base64.b64encode(old).decode("ascii")}
    journal = root / _JOURNAL
    journal_data = json.dumps(snapshots, separators=(",", ":")).encode("ascii")
    _atomic_write(journal, journal_data, 0o600)
    try:
        for path, data in changed.items():
            if data is None:
                try:
                    path.unlink()
                    _fsync_dir(path.parent)
                except FileNotFoundError:
                    pass
            else:
                _atomic_write(path, data, _mode(path))
    except Exception:
        _recover(root)
        raise
    journal.unlink(missing_ok=True)
    _fsync_dir(root)


def _recover(root: Path):
    journal = root / _JOURNAL
    try:
        snapshots = json.loads(journal.read_text("ascii"))
    except FileNotFoundError:
        return
    if not isinstance(snapshots, dict) or set(snapshots) - {"secrets.json", "gemini.key"}:
        raise ValueError("设置事务记录无效")
    for name, item in snapshots.items():
        path = root / name
        if item.get("exists") is True:
            data = base64.b64decode(item["data"], validate=True)
            _atomic_write(path, data, 0o600)
        else:
            path.unlink(missing_ok=True)
    journal.unlink(missing_ok=True)
    _fsync_dir(root)

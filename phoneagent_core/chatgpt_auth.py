"""Sign in with ChatGPT for the local Phone Agent.

Only OpenAI's documented authorization and API origins are contacted. OAuth
credentials are stored in owner-only files below the supplied application root.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

ISSUER = "https://auth.openai.com"
API_RESOURCE = "https://api.openai.com/v1"
AUTHORIZATION_ENDPOINT = ISSUER + "/api/accounts/authorize"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
MODELS_URL = API_RESOURCE + "/models"
APP_NAME = "Phone Agent"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REQUESTED_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
TRANSACTION_TTL = 600
REFRESH_EARLY_SECONDS = 60

_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.RLock()
_root_locks: dict[str, threading.RLock] = {}
_root_locks_guard = threading.Lock()
_discovery_cache: tuple[float, dict[str, str]] | None = None
_jwks_cache: tuple[float, dict[str, Any]] | None = None


class ChatGPTAuthError(RuntimeError):
    """A deliberately sanitized, user-safe authentication error."""


class _HTTPStatusError(ChatGPTAuthError):
    def __init__(self, status: int):
        super().__init__("The ChatGPT request failed. Please try again.")
        self.status = status


def _root_path(root: str | os.PathLike[str]) -> Path:
    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    path = base / ".phoneagent_auth"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _paths(root: str | os.PathLike[str]) -> tuple[Path, Path, Path]:
    base = _root_path(root)
    return base / "chatgpt_accounts.json", base / "chatgpt_hostid", base / "chatgpt_accounts.lock"


def _root_lock(root: str | os.PathLike[str]) -> threading.RLock:
    key = str(_root_path(root))
    with _root_locks_guard:
        return _root_locks.setdefault(key, threading.RLock())


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Cross-thread and cross-process lock for token rotation on Windows/Unix."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
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


@contextlib.contextmanager
def _locked(root: str | os.PathLike[str]) -> Iterator[None]:
    with _root_lock(root):
        _, _, lock_path = _paths(root)
        with _file_lock(lock_path):
            yield


def _empty_store() -> dict[str, Any]:
    return {"accounts": [], "active_id": None}


def _load_store_unlocked(root: str | os.PathLike[str]) -> dict[str, Any]:
    accounts_path, host_path, _ = _paths(root)
    if not host_path.exists():
        _atomic_text(host_path, "urn:uuid:" + str(uuid.uuid4()))
    else:
        try:
            host_path.chmod(0o600)
        except OSError:
            pass
    if not accounts_path.exists():
        return _empty_store()
    try:
        with accounts_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
            raise ValueError
        valid = [a for a in data["accounts"] if isinstance(a, dict) and isinstance(a.get("id"), str)]
        active = data.get("active_id")
        if active not in {a["id"] for a in valid}:
            active = None
        data["accounts"] = valid
        data["active_id"] = active
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise ChatGPTAuthError("The saved ChatGPT account data could not be read safely.") from None


def _save_store_unlocked(root: str | os.PathLike[str], data: dict[str, Any]) -> None:
    accounts_path, _, _ = _paths(root)
    _atomic_text(accounts_path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        os.replace(temp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _jwt_module() -> Any | None:
    try:
        import jwt  # PyJWT, backed by cryptography
        import cryptography  # noqa: F401
        return jwt
    except Exception:
        return None


def _availability() -> tuple[bool, str | None]:
    if _jwt_module() is None:
        return False, "ChatGPT sign-in requires PyJWT and cryptography."
    return True, None


def _validate_redirect_uri(redirect_uri: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(redirect_uri)
        port = parsed.port
    except (ValueError, AttributeError):
        raise ChatGPTAuthError("The callback address is invalid.") from None
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not port or
            parsed.username is not None or parsed.password is not None or
            parsed.path != "/auth/callback" or parsed.query or parsed.fragment):
        raise ChatGPTAuthError("The callback must be a 127.0.0.1 /auth/callback address.")


def _host_id(root: str | os.PathLike[str]) -> str:
    _, host_path, _ = _paths(root)
    if not host_path.exists():
        _atomic_text(host_path, "urn:uuid:" + str(uuid.uuid4()))
    try:
        value = host_path.read_text(encoding="utf-8").strip()
    except OSError:
        raise ChatGPTAuthError("The ChatGPT host identifier could not be read.") from None
    try:
        parsed_uuid = uuid.UUID(value.removeprefix("urn:uuid:")) if value.startswith("urn:uuid:") else None
    except (ValueError, AttributeError):
        parsed_uuid = None
    if parsed_uuid is None or parsed_uuid.version != 4 or value != "urn:uuid:" + str(parsed_uuid):
        raise ChatGPTAuthError("The saved ChatGPT host identifier is invalid.")
    return value


def begin_login(root: str | os.PathLike[str], redirect_uri: str,
                account_id: str | None = None) -> dict[str, str]:
    """Create a one-time PKCE transaction and return its browser URL."""
    available, reason = _availability()
    if not available:
        raise ChatGPTAuthError(reason or "ChatGPT sign-in is unavailable.")
    _validate_redirect_uri(redirect_uri)
    with _locked(root):
        store = _load_store_unlocked(root)
        host_id = _host_id(root)
    selected = None
    if account_id is not None:
        selected = next((a for a in store["accounts"] if a["id"] == account_id), None)
        if selected is None:
            raise ChatGPTAuthError("The selected ChatGPT account was not found.")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    client_id = selected["client_id"] if selected else "dynamic_agent_client"
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": REQUESTED_SCOPES,
        "resource": API_RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "ext_agent_host_id": host_id,
    }
    if selected is None:
        query["agent_name_hint"] = APP_NAME
    else:
        # A retained ID token is an account selector hint, not an authentication proof.
        if selected.get("id_token"):
            query["id_token_hint"] = selected["id_token"]
        if selected.get("email"):
            query["login_hint"] = selected["email"]
    with _pending_lock:
        now = time.time()
        for old_state, item in list(_pending.items()):
            if item["expires_at"] <= now:
                _pending.pop(old_state, None)
        _pending[state] = {
            "root": str(_root_path(root)), "redirect_uri": redirect_uri,
            "nonce": nonce, "verifier": verifier, "client_id": client_id,
            "account_id": account_id, "created_at": now,
            "expires_at": now + TRANSACTION_TTL,
        }
    return {"url": AUTHORIZATION_ENDPOINT + "?" + urllib.parse.urlencode(query)}


def _scalar_params(params: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise ChatGPTAuthError("The sign-in callback could not be verified.")
            value = value[0]
        if not isinstance(value, str):
            raise ChatGPTAuthError("The sign-in callback could not be verified.")
        result[str(key)] = value
    return result


def _allowed_https_url(value: str, host: str) -> bool:
    try:
        p = urllib.parse.urlsplit(value)
        return p.scheme == "https" and p.hostname == host and p.port in (None, 443) and not p.username and not p.password
    except ValueError:
        return False


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (new.scheme, new.hostname, new.port) != (old.scheme, old.hostname, old.port):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_request(url: str, data: dict[str, str] | None = None,
                  bearer: str | None = None) -> tuple[int, bytes]:
    p = urllib.parse.urlsplit(url)
    try:
        allowed_origin = (p.scheme == "https" and p.hostname in {"auth.openai.com", "api.openai.com"} and
                          p.port in (None, 443) and p.username is None and p.password is None)
    except ValueError:
        allowed_origin = False
    if not allowed_origin:
        raise ChatGPTAuthError("The ChatGPT service address is not allowed.")
    headers = {"Accept": "application/json", "User-Agent": "PhoneAgent/1.0"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    opener = urllib.request.build_opener(_SameOriginRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.read(4_000_000)
    except urllib.error.HTTPError as exc:
        # Never surface provider bodies, URLs, request IDs, or credentials.
        try:
            exc.close()
        except Exception:
            pass
        raise _HTTPStatusError(exc.code) from None
    except Exception:
        raise ChatGPTAuthError("The ChatGPT service could not be reached.") from None


def _json_request(url: str, data: dict[str, str] | None = None,
                  bearer: str | None = None) -> dict[str, Any]:
    status, raw = _http_request(url, data, bearer)
    if status < 200 or status >= 300:
        raise ChatGPTAuthError("The ChatGPT request failed. Please try again.")
    try:
        result = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ChatGPTAuthError("The ChatGPT service returned an invalid response.") from None
    if not isinstance(result, dict):
        raise ChatGPTAuthError("The ChatGPT service returned an invalid response.")
    return result


def _discovery(force: bool = False) -> dict[str, str]:
    global _discovery_cache
    now = time.time()
    if not force and _discovery_cache and _discovery_cache[0] > now:
        return _discovery_cache[1]
    data = _json_request(DISCOVERY_URL)
    issuer = data.get("issuer")
    jwks_uri = data.get("jwks_uri")
    authorization = data.get("authorization_endpoint")
    token = data.get("token_endpoint")
    revocation = data.get("revocation_endpoint")
    if (issuer != ISSUER or not all(isinstance(x, str) for x in (jwks_uri, authorization, token)) or
            not _allowed_https_url(jwks_uri, "auth.openai.com") or
            not _allowed_https_url(authorization, "auth.openai.com") or
            not _allowed_https_url(token, "auth.openai.com") or
            (revocation is not None and (not isinstance(revocation, str) or not _allowed_https_url(revocation, "auth.openai.com")))):
        raise ChatGPTAuthError("OpenAI sign-in configuration could not be verified.")
    result = {"issuer": issuer, "jwks_uri": jwks_uri, "authorization_endpoint": authorization,
              "token_endpoint": token, "revocation_endpoint": revocation or ""}
    _discovery_cache = (now + 900, result)
    return result


def _jwks(uri: str, force: bool = False) -> dict[str, Any]:
    global _jwks_cache
    now = time.time()
    if not force and _jwks_cache and _jwks_cache[0] > now:
        return _jwks_cache[1]
    data = _json_request(uri)
    if not isinstance(data.get("keys"), list):
        raise ChatGPTAuthError("OpenAI signing keys could not be verified.")
    _jwks_cache = (now + 900, data)
    return data


def _verify_id_token(token: str, client_id: str, nonce: str | None) -> dict[str, Any]:
    jwt = _jwt_module()
    if jwt is None:
        raise ChatGPTAuthError("ChatGPT sign-in requires PyJWT and cryptography.")
    if not isinstance(token, str) or not token or len(token) > 64_000:
        raise ChatGPTAuthError("OpenAI did not return a valid identity token.")
    config = _discovery()
    try:
        header = jwt.get_unverified_header(token)
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in {"RS256", "ES256"} or not isinstance(kid, str):
            raise ValueError
        keys = _jwks(config["jwks_uri"])
        match = next((key for key in keys["keys"] if key.get("kid") == kid and key.get("use", "sig") == "sig"), None)
        if match is None:
            keys = _jwks(config["jwks_uri"], force=True)
            match = next((key for key in keys["keys"] if key.get("kid") == kid and key.get("use", "sig") == "sig"), None)
        if match is None:
            raise ValueError
        signing_key = jwt.algorithms.get_default_algorithms()[alg].from_jwk(json.dumps(match))
        claims = jwt.decode(token, signing_key, algorithms=[alg], audience=client_id,
                            issuer=ISSUER, leeway=5,
                            options={"require": ["sub", "exp", "iat", "iss", "aud"]})
        if claims.get("iss") != ISSUER or not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError
        if nonce is not None and claims.get("nonce") != nonce:
            raise ValueError
        return claims
    except ChatGPTAuthError:
        raise
    except Exception:
        raise ChatGPTAuthError("The ChatGPT identity could not be verified.") from None


def _scope_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
        return set(value)
    return set()


def finish_login(root: str | os.PathLike[str], params: dict[str, Any]) -> dict[str, Any]:
    """Consume callback parameters (a scalar dict, or parse_qs one-value lists)."""
    values = _scalar_params(params)
    state = values.get("state", "")
    with _pending_lock:
        tx = _pending.pop(state, None) if state else None
    if (not tx or Path(tx["root"]) != _root_path(root) or
            tx["expires_at"] <= time.time()):
        raise ChatGPTAuthError("The sign-in callback could not be verified.")
    if values.get("error"):
        raise ChatGPTAuthError("ChatGPT sign-in was not completed.")
    code = values.get("code")
    if not code or len(code) > 8192:
        raise ChatGPTAuthError("The sign-in callback did not contain a valid code.")
    returned_client = values.get("client_id")
    expected_client = tx["client_id"]
    if expected_client == "dynamic_agent_client":
        if not returned_client or not re.fullmatch(r"oaiapp_[A-Za-z0-9._~-]{1,240}", returned_client):
            raise ChatGPTAuthError("The ChatGPT account registration was not completed.")
        client_id = returned_client
    else:
        if returned_client is not None and returned_client != expected_client:
            raise ChatGPTAuthError("The selected ChatGPT account could not be verified.")
        client_id = expected_client
    token_response = _json_request(TOKEN_ENDPOINT, {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": tx["redirect_uri"], "client_id": client_id,
        "code_verifier": tx["verifier"], "resource": API_RESOURCE,
    })
    access = token_response.get("access_token")
    id_token = token_response.get("id_token")
    refresh = token_response.get("refresh_token")
    expires = token_response.get("expires_in")
    token_type = token_response.get("token_type")
    scope_text = token_response.get("scope", "")
    if not isinstance(id_token, str) or not isinstance(scope_text, str):
        raise ChatGPTAuthError("The ChatGPT token response was incomplete.")
    granted_scopes = _scope_set(scope_text)
    has_plan = PLAN_SCOPE in granted_scopes
    if has_plan and (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or
                     token_type != "Bearer" or not isinstance(expires, (int, float)) or
                     isinstance(expires, bool) or not math.isfinite(expires) or expires <= 0):
        raise ChatGPTAuthError("The ChatGPT token response was incomplete.")
    claims = _verify_id_token(id_token, client_id, tx["nonce"])
    subject = claims["sub"]
    selected_id = tx.get("account_id")
    with _locked(root):
        store = _load_store_unlocked(root)
        existing = next((a for a in store["accounts"]
                         if a.get("client_id") == client_id and a.get("issuer") == ISSUER and a.get("subject") == subject), None)
        if selected_id:
            selected = next((a for a in store["accounts"] if a["id"] == selected_id), None)
            if not selected or selected.get("client_id") != client_id or selected.get("issuer") != ISSUER or selected.get("subject") != subject:
                raise ChatGPTAuthError("The selected ChatGPT account identity changed.")
            account = selected
        else:
            account = existing
        if account is None:
            account = {"id": str(uuid.uuid4()), "label": "", "client_id": client_id,
                       "issuer": ISSUER, "subject": subject, "email": None,
                       "scopes": [], "models_cache": None}
            store["accounts"].append(account)
        account.update({
            "client_id": client_id, "issuer": ISSUER, "subject": subject,
            "email": claims.get("email") if isinstance(claims.get("email"), str) else account.get("email"),
            "label": _account_label(claims, account, len(store["accounts"])),
            "access_token": access if isinstance(access, str) else None,
            "refresh_token": refresh if isinstance(refresh, str) else None,
            "id_token": id_token,
            "token_type": token_type if isinstance(token_type, str) else None,
            "expires_at": time.time() + float(expires) if isinstance(expires, (int, float)) else 0,
            "scopes": sorted(granted_scopes),
            "saved_at": int(time.time()), "models_cache": None,
        })
        store["active_id"] = account["id"]
        _save_store_unlocked(root, store)
    return account_status(root)


def _account_label(claims: dict[str, Any], account: dict[str, Any], ordinal: int) -> str:
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()[:120]
    current = account.get("label")
    if isinstance(current, str) and current:
        return current[:120]
    return f"ChatGPT account {ordinal}"


def _public_account(account: dict[str, Any], active: bool) -> dict[str, Any]:
    scopes = _scope_set(account.get("scopes")) if isinstance(account.get("scopes"), list) else set()
    has_access_token = isinstance(account.get("access_token"), str) and bool(account.get("access_token"))
    return {"id": account["id"], "label": account.get("label") or "ChatGPT account",
            "email": account.get("email"), "plan_enabled": PLAN_SCOPE in scopes and has_access_token,
            "active": bool(active)}


def account_status(root: str | os.PathLike[str]) -> dict[str, Any]:
    available, reason = _availability()
    with _locked(root):
        store = _load_store_unlocked(root)
    active = next((a for a in store["accounts"] if a["id"] == store.get("active_id")), None)
    public_accounts = [_public_account(a, bool(active and a["id"] == active["id"])) for a in store["accounts"]]
    plan_ready = bool(available and active and _public_account(active, True)["plan_enabled"])
    plan_reason = None
    if available and not active:
        plan_reason = "No ChatGPT account is signed in."
    elif available and not plan_ready:
        plan_reason = "The active account has not granted ChatGPT plan access."
    return {"accounts": public_accounts, "active_id": store.get("active_id"),
            "available": available, "plan_ready": plan_ready,
            "reason": reason, "plan_reason": plan_reason}


def select_account(root: str | os.PathLike[str], account_id: str) -> dict[str, Any]:
    with _locked(root):
        store = _load_store_unlocked(root)
        account = next((a for a in store["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise ChatGPTAuthError("The selected ChatGPT account was not found.")
        store["active_id"] = account_id
        _save_store_unlocked(root, store)
    return account_status(root)


def _clear_tokens(account: dict[str, Any]) -> None:
    for key in ("access_token", "refresh_token", "id_token", "expires_at", "token_type"):
        account.pop(key, None)


def logout(root: str | os.PathLike[str], account_id: str) -> dict[str, Any]:
    """Revoke when possible, then always clear local tokens and retain registration."""
    with _locked(root):
        store = _load_store_unlocked(root)
        account = next((a for a in store["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise ChatGPTAuthError("The selected ChatGPT account was not found.")
        refresh = account.get("refresh_token")
        revoked = False
        if isinstance(refresh, str) and refresh:
            try:
                endpoint = _discovery().get("revocation_endpoint")
                if endpoint:
                    status, _ = _http_request(endpoint, {
                        "token": refresh, "token_type_hint": "refresh_token",
                        "client_id": account["client_id"],
                    })
                    revoked = status == 200
            except Exception:
                revoked = False
        was_active = store.get("active_id") == account_id
        _clear_tokens(account)
        account["scopes"] = []
        account["models_cache"] = None
        if was_active:
            store["active_id"] = None
        _save_store_unlocked(root, store)
    status = account_status(root)
    status["revocation_confirmed"] = revoked
    return status


def _active_account_unlocked(store: dict[str, Any]) -> dict[str, Any]:
    account = next((a for a in store["accounts"] if a["id"] == store.get("active_id")), None)
    if account is None:
        raise ChatGPTAuthError("No ChatGPT account is active.")
    if PLAN_SCOPE not in _scope_set(account.get("scopes", [])):
        raise ChatGPTAuthError("The active ChatGPT account has no ChatGPT plan permission.")
    return account


def _refresh_account(root: str | os.PathLike[str], account_id: str) -> str:
    with _locked(root):
        store = _load_store_unlocked(root)
        account = next((a for a in store["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise ChatGPTAuthError("No ChatGPT account is active.")
        if float(account.get("expires_at", 0)) > time.time() + REFRESH_EARLY_SECONDS:
            return str(account.get("access_token", ""))
        refresh = account.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            _clear_tokens(account)
            _save_store_unlocked(root, store)
            raise ChatGPTAuthError("The ChatGPT session expired. Sign in again.")
        try:
            response = _json_request(TOKEN_ENDPOINT, {
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": refresh, "resource": API_RESOURCE,
            })
            access, replacement, token_type = response.get("access_token"), response.get("refresh_token"), response.get("token_type")
            expires = response.get("expires_in")
            if (not isinstance(access, str) or not access or not isinstance(replacement, str) or not replacement or
                    token_type != "Bearer" or not isinstance(expires, (int, float)) or isinstance(expires, bool) or
                    not math.isfinite(expires) or expires <= 0):
                raise ValueError
            scopes = _scope_set(response["scope"]) if isinstance(response.get("scope"), str) else _scope_set(account.get("scopes", []))
            new_id_token = response.get("id_token")
            email = account.get("email")
            if new_id_token is not None:
                claims = _verify_id_token(new_id_token, account["client_id"], None)
                if claims.get("sub") != account.get("subject"):
                    raise ChatGPTAuthError("The refreshed ChatGPT identity changed.")
                if isinstance(claims.get("email"), str):
                    email = claims["email"]
            account.update({"access_token": access, "refresh_token": replacement,
                            "id_token": new_id_token if isinstance(new_id_token, str) else account.get("id_token"),
                            "token_type": "Bearer", "expires_at": time.time() + float(expires),
                            "scopes": sorted(scopes), "email": email, "saved_at": int(time.time())})
            # Token rotation preserves identity/client and its catalog. Clearing it
            # here makes an admitted model disappear before quota/dispatch lookup.
            if PLAN_SCOPE not in scopes:
                account["models_cache"] = None
            _save_store_unlocked(root, store)
            if PLAN_SCOPE not in scopes:
                raise ChatGPTAuthError("The active ChatGPT account no longer has ChatGPT plan permission.")
            return access
        except _HTTPStatusError as exc:
            if exc.status in (400, 401):
                _clear_tokens(account)
                account["scopes"] = []
                account["models_cache"] = None
                _save_store_unlocked(root, store)
                raise ChatGPTAuthError("The ChatGPT session expired. Sign in again.") from None
            raise ChatGPTAuthError("The ChatGPT service could not refresh the session. Please try again.") from None
        except ChatGPTAuthError:
            raise
        except Exception:
            # invalid_grant and all other provider details are deliberately hidden.
            _clear_tokens(account)
            account["scopes"] = []
            account["models_cache"] = None
            _save_store_unlocked(root, store)
            raise ChatGPTAuthError("The ChatGPT session expired. Sign in again.") from None


def access_token(root: str | os.PathLike[str]) -> str:
    with _locked(root):
        store = _load_store_unlocked(root)
        account = _active_account_unlocked(store)
        account_id = account["id"]
        if float(account.get("expires_at", 0)) > time.time() + REFRESH_EARLY_SECONDS:
            token = account.get("access_token")
            if isinstance(token, str) and token:
                return token
    return _refresh_account(root, account_id)


def models(root: str | os.PathLike[str], refresh: bool = False) -> list[dict[str, Any]]:
    with _locked(root):
        store = _load_store_unlocked(root)
        account = next((a for a in store["accounts"] if a["id"] == store.get("active_id")), None)
        if account is None or not _public_account(account, True)["plan_enabled"]:
            return []
        if isinstance(account.get("models_cache"), dict):
            cached = account["models_cache"]
            if not refresh and isinstance(cached.get("models"), list):
                return [m for m in cached["models"] if isinstance(m, dict) and m.get("visibility") == "list"]
        account_id = account["id"]
        if not refresh:
            return []
    token = access_token(root)
    response = _json_request(MODELS_URL, bearer=token)
    raw_models = response.get("models")
    if not isinstance(raw_models, list):
        raise ChatGPTAuthError("The ChatGPT model catalog was invalid.")
    filtered = [m for m in raw_models if isinstance(m, dict) and m.get("visibility") == "list"]
    with _locked(root):
        store = _load_store_unlocked(root)
        account = next((a for a in store["accounts"] if a["id"] == account_id), None)
        if account:
            account["models_cache"] = {"saved_at": int(time.time()), "models": raw_models}
            _save_store_unlocked(root, store)
    return filtered


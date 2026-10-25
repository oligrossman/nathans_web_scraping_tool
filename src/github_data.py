"""Copy menu files to a GitHub branch and load them back on startup.

Streamlit's disk is wiped when the app sleeps or redeploys. The files that
hold scraped menus, compiled price history, and dish mappings are copied to
a ``data`` branch after each save, then copied back the next time the app
starts. App code stays on ``main``, so an upload does not restart Streamlit.

The branch is a full snapshot of those files on every commit. A file removed
locally (for example a deleted scrape date) is absent from the next snapshot,
which removes it on GitHub too.

Nothing is uploaded unless a token is set. See :func:`load_credentials`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

import requests

from src import history_archive

API_ROOT = "https://api.github.com"
DEFAULT_REPOSITORY = "oligrossman/nathans_web_scraping_tool"
DEFAULT_BRANCH = "data"
_TIMEOUT_SECONDS = 60

# These are the files a save in the app can change. The rest of ``data/``
# (restaurant config, company weights, local notes) ships with the code on
# ``main`` and must not be uploaded or overwritten from the data branch.
_DATA_FILES = (
    "all_company_prices.csv",
    "scrape_log.json",
    "menu_mappings.db",
)
_ROOT_FILES = tuple(f"data/{name}" for name in _DATA_FILES)
_SCRAPES_PREFIX = "data/scrapes/"
_SKIP_FILENAMES = {".DS_Store"}

PushStatus = Literal["disabled", "unchanged", "pushed", "error"]
PullStatus = Literal["disabled", "missing", "updated", "error"]

_lock = threading.Lock()
_pull_result: "PullResult | None" = None


class GitHubError(Exception):
    """GitHub refused a call, or the response was not safe to apply."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class Credentials:
    """Token and target branch. Built from the environment or Streamlit secrets."""

    token: str
    repository: str
    branch: str


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    detail: str = ""


@dataclass(frozen=True)
class PullResult:
    status: PullStatus
    detail: str = ""
    file_count: int = 0


class HttpResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict | None,
        timeout: float,
    ) -> HttpResponse: ...


class RequestsTransport:
    """The real GitHub HTTP client. Tests pass a fake instead of this."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict | None,
        timeout: float,
    ) -> HttpResponse:
        response = self._session.request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=timeout,
        )
        return HttpResponse(response.status_code, response.content)


def git_blob_sha(content: bytes) -> str:
    """SHA-1 Git uses for a file. Matches the blob sha GitHub returns."""
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def load_credentials() -> Credentials | None:
    """Return GitHub credentials, or ``None`` when no token is configured.

    ``GITHUB_TOKEN`` is required. ``GITHUB_REPOSITORY`` and
    ``GITHUB_DATA_BRANCH`` are optional (``oligrossman/nathans_web_scraping_tool``
    and ``data``). An environment variable wins over a Streamlit secret, so
    a local shell can point somewhere else without editing the app.
    """
    token = _setting("GITHUB_TOKEN")
    if not token:
        return None
    repository = _setting("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    branch = _setting("GITHUB_DATA_BRANCH") or DEFAULT_BRANCH
    return Credentials(token=token, repository=repository, branch=branch)


def ensure_local_data(transport: Transport | None = None) -> PullResult:
    """Download the data branch once per process. Later calls do nothing.

    A missing branch is normal before the first save: the files shipped with
    the app stay in place. A failed download is remembered so every click
    does not wait on GitHub again; restart the app after fixing the token.
    """
    global _pull_result
    with _lock:
        if _pull_result is not None:
            return _pull_result
        credentials = load_credentials()
        if credentials is None:
            _pull_result = PullResult(status="disabled")
            return _pull_result
        try:
            _pull_result = _pull(
                credentials, transport or RequestsTransport(), _data_dir()
            )
        except Exception as exc:
            _pull_result = PullResult(status="error", detail=str(exc))
        return _pull_result


def push_local_data(
    message: str, transport: Transport | None = None
) -> PushResult:
    """Upload the current menu files. No token means nothing is sent."""
    credentials = load_credentials()
    if credentials is None:
        return PushResult(status="disabled")
    try:
        with _lock:
            return _push(
                credentials,
                message,
                transport or RequestsTransport(),
                _data_dir(),
            )
    except Exception as exc:
        return PushResult(status="error", detail=str(exc))


def publish(message: str) -> None:
    """Upload menu files and remember the outcome for the next screen draw.

    Call this after the local save and before ``st.rerun()``. The banner
    reads the outcome on the following run, so a rerun does not hide it.
    """
    result = push_local_data(message)
    if result.status in {"disabled", "unchanged"}:
        return
    import streamlit as st

    st.session_state["github_publish"] = {
        "status": result.status,
        "detail": result.detail,
    }


def render_sync_banner() -> None:
    """Load saved files, then say whether GitHub saving is on."""
    import streamlit as st

    if _pull_result is None and load_credentials() is not None:
        with st.spinner("Loading saved menu data..."):
            result = ensure_local_data()
    else:
        result = ensure_local_data()

    notice = st.session_state.pop("github_publish", None)
    if isinstance(notice, dict) and notice.get("status") == "pushed":
        st.success("Saved to GitHub.")
    elif isinstance(notice, dict) and notice.get("status") == "error":
        st.error(
            "Saved for this session, but GitHub rejected the upload. "
            f"{notice.get('detail', '')}"
        )

    if result.status == "error":
        st.warning(
            "GitHub could not be reached, so this screen is using the copy "
            f"stored with the app. {result.detail}"
        )
    elif result.status == "disabled":
        st.info(
            "Add a secret named GITHUB_TOKEN so scrapes and updates are saved "
            "to GitHub. Without it, they disappear when this app restarts."
        )


def reset_sync_state() -> None:
    """Forget the startup download. Tests use this between cases."""
    global _pull_result
    with _lock:
        _pull_result = None


def synced_files(data_dir: Path | None = None) -> dict[str, Path]:
    """Map each repo-relative menu path to its on-disk path."""
    root = data_dir or _data_dir()
    found: dict[str, Path] = {}
    for name in _DATA_FILES:
        path = root / name
        if path.is_file():
            found[f"data/{name}"] = path
    scrapes = root / "scrapes"
    if scrapes.is_dir():
        for path in sorted(scrapes.rglob("*")):
            if not path.is_file() or path.name in _SKIP_FILENAMES:
                continue
            if path.name.endswith(("-wal", "-shm", ".db-journal")):
                continue
            found[f"data/{path.relative_to(root).as_posix()}"] = path
    return found


def _data_dir() -> Path:
    return history_archive.DATA_DIR


def _setting(name: str) -> str:
    from_env = os.environ.get(name, "").strip()
    if from_env:
        return from_env
    try:
        import streamlit as st

        value = st.secrets[name]
    except Exception:
        return ""
    return str(value).strip()


def _headers(credentials: Credentials) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wagamama-menu-intelligence",
    }


def _request(
    transport: Transport,
    credentials: Credentials,
    method: str,
    path: str,
    json_body: dict | None = None,
) -> Any:
    response = _raw(transport, credentials, method, path, json_body)
    if response.status_code >= 400:
        raise GitHubError(response.status_code, _error_message(response))
    if not response.content:
        return {}
    return response.json()


def _raw(
    transport: Transport,
    credentials: Credentials,
    method: str,
    path: str,
    json_body: dict | None = None,
) -> HttpResponse:
    _check_target(credentials)
    return transport.request(
        method,
        f"{API_ROOT}{path}",
        headers=_headers(credentials),
        json_body=json_body,
        timeout=_TIMEOUT_SECONDS,
    )


def _error_message(response: HttpResponse) -> str:
    try:
        message = response.json().get("message", "")
    except (json.JSONDecodeError, AttributeError):
        message = ""
    return str(message or f"GitHub returned status {response.status_code}.")


def _check_target(credentials: Credentials) -> None:
    owner, separator, name = credentials.repository.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise GitHubError(0, "GITHUB_REPOSITORY must look like owner/name.")
    if (
        not re.fullmatch(r"[A-Za-z0-9._/-]+", credentials.branch)
        or ".." in credentials.branch.split("/")
    ):
        raise GitHubError(0, "GITHUB_DATA_BRANCH contains unsupported characters.")


def _repo(credentials: Credentials) -> str:
    return f"/repos/{credentials.repository}"


def _pull(
    credentials: Credentials, transport: Transport, data_dir: Path
) -> PullResult:
    response = _raw(
        transport,
        credentials,
        "GET",
        f"{_repo(credentials)}/zipball/{credentials.branch}",
    )
    if response.status_code == 404:
        return PullResult(status="missing")
    if response.status_code >= 400:
        raise GitHubError(response.status_code, _error_message(response))
    files = files_from_zip(response.content)
    if not files:
        raise GitHubError(
            0,
            "The data branch has no menu files. Left the local copies in place.",
        )
    _replace_local(data_dir, files)
    return PullResult(status="updated", file_count=len(files))


def files_from_zip(payload: bytes) -> dict[str, bytes]:
    """Read menu files out of a GitHub zipball.

    The archive's top folder is the repository name plus a commit sha, so it
    is stripped. Paths that climb out of that folder are ignored.
    """
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if len(parts) < 2 or ".." in parts:
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            if not _is_synced_path(relative) or ".." in PurePosixPath(relative).parts:
                continue
            files[relative] = archive.read(info)
    return files


def _is_synced_path(relative: str) -> bool:
    if relative in _ROOT_FILES:
        return True
    return relative.startswith(_SCRAPES_PREFIX) and not relative.endswith("/")


def _disk_path(data_dir: Path, relative: str) -> Path:
    """Map a repo path such as ``data/scrapes/day/menu.csv`` onto ``data_dir``."""
    if not relative.startswith("data/") or ".." in PurePosixPath(relative).parts:
        raise GitHubError(0, f"Refusing to write {relative}.")
    return data_dir / relative.removeprefix("data/")


def _replace_local(data_dir: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        target = _disk_path(data_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for relative, path in synced_files(data_dir).items():
        if relative not in files and path.is_file():
            path.unlink()
    _remove_empty_dirs(data_dir / "scrapes")


def _remove_empty_dirs(scrapes: Path) -> None:
    if not scrapes.is_dir():
        return
    for child in sorted(scrapes.rglob("*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()


def _push(
    credentials: Credentials,
    message: str,
    transport: Transport,
    data_dir: Path,
) -> PushResult:
    local = {
        relative: path.read_bytes()
        for relative, path in synced_files(data_dir).items()
    }
    if not local:
        return PushResult(
            status="error",
            detail="No menu files were found to upload. Left GitHub unchanged.",
        )

    last_error: GitHubError | None = None
    for attempt in range(2):
        ref = _branch_sha(credentials, transport)
        remote = _remote_blobs(credentials, transport, ref)
        entries, changed = _tree_entries(credentials, transport, local, remote)
        if not changed:
            return PushResult(status="unchanged")
        tree = _request(
            transport,
            credentials,
            "POST",
            f"{_repo(credentials)}/git/trees",
            {"tree": entries},
        )
        parents = [ref] if ref else []
        commit = _request(
            transport,
            credentials,
            "POST",
            f"{_repo(credentials)}/git/commits",
            {"message": message, "tree": tree["sha"], "parents": parents},
        )
        try:
            _move_branch(credentials, transport, ref, commit["sha"])
        except GitHubError as exc:
            last_error = exc
            if exc.status not in {409, 422} or attempt == 1:
                raise
            continue
        return PushResult(status="pushed")
    raise last_error or GitHubError(0, "Could not update the data branch.")


def _branch_sha(credentials: Credentials, transport: Transport) -> str | None:
    try:
        body = _request(
            transport,
            credentials,
            "GET",
            f"{_repo(credentials)}/git/ref/heads/{credentials.branch}",
        )
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise
    return str(body["object"]["sha"])


def _remote_blobs(
    credentials: Credentials, transport: Transport, ref: str | None
) -> dict[str, str]:
    if ref is None:
        return {}
    commit = _request(
        transport,
        credentials,
        "GET",
        f"{_repo(credentials)}/git/commits/{ref}",
    )
    tree = _request(
        transport,
        credentials,
        "GET",
        f"{_repo(credentials)}/git/trees/{commit['tree']['sha']}?recursive=1",
    )
    if tree.get("truncated"):
        raise GitHubError(
            0, "GitHub returned a truncated file list. Refusing to update."
        )
    return {
        item["path"]: item["sha"]
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and _is_synced_path(item["path"])
    }


def _tree_entries(
    credentials: Credentials,
    transport: Transport,
    local: dict[str, bytes],
    remote: dict[str, str],
) -> tuple[list[dict[str, str]], bool]:
    """Build a complete tree. Reuse a blob sha when the file has not changed."""
    entries: list[dict[str, str]] = []
    changed = False
    for relative, content in sorted(local.items()):
        sha = git_blob_sha(content)
        if remote.get(relative) != sha:
            created = _request(
                transport,
                credentials,
                "POST",
                f"{_repo(credentials)}/git/blobs",
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            )
            sha = str(created["sha"])
            changed = True
        entries.append(
            {"path": relative, "mode": "100644", "type": "blob", "sha": sha}
        )
    removed = [path for path in remote if path not in local]
    if removed:
        changed = True
    return entries, changed


def _move_branch(
    credentials: Credentials,
    transport: Transport,
    current: str | None,
    commit_sha: str,
) -> None:
    if current is None:
        _request(
            transport,
            credentials,
            "POST",
            f"{_repo(credentials)}/git/refs",
            {"ref": f"refs/heads/{credentials.branch}", "sha": commit_sha},
        )
        return
    _request(
        transport,
        credentials,
        "PATCH",
        f"{_repo(credentials)}/git/refs/heads/{credentials.branch}",
        {"sha": commit_sha, "force": False},
    )

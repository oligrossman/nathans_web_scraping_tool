"""GitHub is the store for menu files. These tests never call the real API."""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest

from src import github_data, history_archive
from src.github_data import (
    Credentials,
    GitHubError,
    HttpResponse,
    files_from_zip,
    git_blob_sha,
    synced_files,
)


_REPO = "/repos/oligrossman/nathans_web_scraping_tool"


class MemoryGitHub:
    """Just enough of the GitHub git-data API to watch one data branch."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.commits: dict[str, dict] = {}
        self.branch: str | None = None
        self.blob_posts = 0
        self.patch_count = 0
        self.reject_next_patch = False
        self.truncate = False
        self.messages: list[str] = []
        self.saw_token_in_url = False

    def request(self, method, url, *, headers, json_body, timeout):
        if "test-token" in url:
            self.saw_token_in_url = True
        assert headers["Authorization"] == "Bearer test-token"
        path = urlparse(url).path
        assert path.startswith(_REPO)
        status, body = self._handle(method, path[len(_REPO) :], json_body)
        if isinstance(body, bytes):
            return HttpResponse(status, body)
        return HttpResponse(status, json.dumps(body).encode())

    def _handle(self, method: str, path: str, body: dict | None):
        if method == "GET" and path == "/zipball/data":
            archive = self._zip()
            if archive is None:
                return 404, {"message": "Not Found"}
            return 200, archive
        if method == "GET" and path == "/git/ref/heads/data":
            if self.branch is None:
                return 404, {"message": "Not Found"}
            return 200, {"object": {"sha": self.branch, "type": "commit"}}
        if method == "GET" and path.startswith("/git/commits/"):
            sha = path.rsplit("/", 1)[-1]
            commit = self.commits[sha]
            return 200, {"sha": sha, "tree": {"sha": commit["tree"]}}
        if method == "GET" and path.startswith("/git/trees/"):
            if self.truncate:
                return 200, {"truncated": True, "tree": []}
            sha = path.rsplit("/", 1)[-1]
            entries = [
                {"path": name, "mode": "100644", "type": "blob", "sha": blob}
                for name, blob in self.trees[sha].items()
            ]
            return 200, {"sha": sha, "truncated": False, "tree": entries}
        if method == "POST" and path == "/git/blobs":
            raw = base64.b64decode(body["content"])
            sha = git_blob_sha(raw)
            self.blobs[sha] = raw
            self.blob_posts += 1
            return 201, {"sha": sha}
        if method == "POST" and path == "/git/trees":
            tree = {}
            for item in body["tree"]:
                assert item["sha"] in self.blobs
                tree[item["path"]] = item["sha"]
            sha = f"tree-{len(self.trees) + 1}"
            self.trees[sha] = tree
            return 201, {"sha": sha}
        if method == "POST" and path == "/git/commits":
            sha = f"commit-{len(self.commits) + 1}"
            self.commits[sha] = {
                "tree": body["tree"],
                "message": body["message"],
            }
            self.messages.append(body["message"])
            return 201, {"sha": sha, "tree": {"sha": body["tree"]}}
        if method == "POST" and path == "/git/refs":
            assert body["ref"] == "refs/heads/data"
            self.branch = body["sha"]
            return 201, {"object": {"sha": self.branch}}
        if method == "PATCH" and path == "/git/refs/heads/data":
            self.patch_count += 1
            if self.reject_next_patch:
                self.reject_next_patch = False
                return 422, {"message": "Update is not a fast forward"}
            assert body["force"] is False
            self.branch = body["sha"]
            return 200, {"object": {"sha": self.branch}}
        raise AssertionError(f"unexpected {method} {path}")

    def _zip(self) -> bytes | None:
        if self.branch is None:
            return None
        buffer = io.BytesIO()
        tree = self.trees[self.commits[self.branch]["tree"]]
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, sha in tree.items():
                archive.writestr(f"repo-{self.branch}/{path}", self.blobs[sha])
        return buffer.getvalue()


def _credentials() -> Credentials:
    return Credentials("test-token", "oligrossman/nathans_web_scraping_tool", "data")


@pytest.fixture(autouse=True)
def _reset_github_sync():
    github_data.reset_sync_state()
    yield
    github_data.reset_sync_state()


def _sample_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    day = data / "scrapes" / "2026-01-01"
    day.mkdir(parents=True)
    (data / "all_company_prices.csv").write_bytes(b"prices\n")
    (data / "scrape_log.json").write_bytes(b"{}\n")
    (data / "menu_mappings.db").write_bytes(b"\xff\xfedb")
    (day / "wagamama_menu_snapshot.csv").write_bytes(b"snap\n")
    (data / "restaurants.csv").write_text("keep\n", encoding="utf-8")
    (data / "env vars").write_text("secret\n", encoding="utf-8")
    return data


def test_blob_sha_matches_git() -> None:
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_synced_files_leave_config_and_notes_alone(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    relative = set(synced_files(data))
    assert "data/restaurants.csv" not in relative
    assert "data/env vars" not in relative
    assert "data/menu_mappings.db" in relative
    assert "data/scrapes/2026-01-01/wagamama_menu_snapshot.csv" in relative


def test_zip_keeps_only_menu_files() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("repo-sha/../../outside.txt", b"nope")
        archive.writestr("repo-sha/data/restaurants.csv", b"config")
        archive.writestr("repo-sha/data/all_company_prices.csv", b"prices\n")
        archive.writestr("repo-sha/data/scrapes/2026-01-01/a.csv", b"snap\n")
    assert files_from_zip(buffer.getvalue()) == {
        "data/all_company_prices.csv": b"prices\n",
        "data/scrapes/2026-01-01/a.csv": b"snap\n",
    }


def test_push_creates_branch_and_second_push_skips_unchanged_files(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    remote = MemoryGitHub()
    first = github_data._push(_credentials(), "Save scraped menus", remote, data)
    assert first.status == "pushed"
    assert remote.messages == ["Save scraped menus"]
    assert remote.saw_token_in_url is False
    uploaded = remote.blob_posts

    second = github_data._push(_credentials(), "Save scraped menus", remote, data)
    assert second.status == "unchanged"
    assert remote.blob_posts == uploaded
    assert remote.branch is not None
    stored = remote.trees[remote.commits[remote.branch]["tree"]]
    assert "data/restaurants.csv" not in stored
    assert "data/env vars" not in stored
    assert remote.blobs[stored["data/menu_mappings.db"]] == b"\xff\xfedb"


def test_push_removes_a_deleted_scrape_and_pull_restores_that(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    remote = MemoryGitHub()
    github_data._push(_credentials(), "Save scraped menus", remote, data)

    snapshot = data / "scrapes" / "2026-01-01" / "wagamama_menu_snapshot.csv"
    snapshot.unlink()
    updated = github_data._push(_credentials(), "Delete scrape archive 2026-01-01", remote, data)
    assert updated.status == "pushed"

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    stale = fresh / "scrapes" / "2026-01-01"
    stale.mkdir(parents=True)
    (stale / "wagamama_menu_snapshot.csv").write_bytes(b"old\n")
    (fresh / "all_company_prices.csv").write_bytes(b"old prices\n")
    (fresh / "restaurants.csv").write_text("keep\n", encoding="utf-8")

    pulled = github_data._pull(_credentials(), remote, fresh)
    assert pulled.status == "updated"
    assert not (stale / "wagamama_menu_snapshot.csv").exists()
    assert not stale.exists()
    assert (fresh / "all_company_prices.csv").read_bytes() == b"prices\n"
    assert (fresh / "menu_mappings.db").read_bytes() == b"\xff\xfedb"
    assert (fresh / "restaurants.csv").read_text(encoding="utf-8") == "keep\n"


def test_missing_branch_keeps_the_files_already_on_disk(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    result = github_data._pull(_credentials(), MemoryGitHub(), data)
    assert result.status == "missing"
    assert (data / "all_company_prices.csv").read_bytes() == b"prices\n"


def test_refuses_to_upload_when_there_is_nothing_to_save(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    remote = MemoryGitHub()
    result = github_data._push(_credentials(), "Update price history", remote, data)
    assert result.status == "error"
    assert remote.branch is None


def test_retries_once_when_the_branch_moved(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    remote = MemoryGitHub()
    github_data._push(_credentials(), "Save scraped menus", remote, data)
    remote.reject_next_patch = True
    (data / "scrape_log.json").write_bytes(b'{"pho": 1}\n')

    result = github_data._push(_credentials(), "Save scraped menus", remote, data)

    assert result.status == "pushed"
    assert remote.patch_count == 2
    assert remote.commits[remote.branch]["message"] == "Save scraped menus"


def test_truncated_file_list_leaves_the_branch_alone(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    remote = MemoryGitHub()
    github_data._push(_credentials(), "Save scraped menus", remote, data)
    kept = remote.branch
    remote.truncate = True
    (data / "scrape_log.json").write_bytes(b"changed\n")

    with pytest.raises(GitHubError, match="truncated"):
        github_data._push(_credentials(), "Save scraped menus", remote, data)

    assert remote.branch == kept


def test_bad_repository_name_is_rejected(tmp_path) -> None:
    data = _sample_dir(tmp_path)
    credentials = Credentials("test-token", "not a repo", "data")
    with pytest.raises(GitHubError, match="owner/name"):
        github_data._push(credentials, "Save scraped menus", MemoryGitHub(), data)


def test_push_without_a_token_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_data, "load_credentials", lambda: None)
    result = github_data.push_local_data("Save scraped menus")
    assert result.status == "disabled"


def test_startup_download_runs_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    github_data.reset_sync_state()
    data = _sample_dir(tmp_path)
    remote = MemoryGitHub()
    github_data._push(_credentials(), "Update price history", remote, data)
    (data / "all_company_prices.csv").write_bytes(b"stale\n")
    monkeypatch.setattr(history_archive, "DATA_DIR", data)
    monkeypatch.setattr(github_data, "load_credentials", lambda: _credentials())
    monkeypatch.setattr(github_data, "RequestsTransport", lambda: remote)

    first = github_data.ensure_local_data()
    second = github_data.ensure_local_data()

    assert first.status == "updated"
    assert second is first
    assert (data / "all_company_prices.csv").read_bytes() == b"prices\n"
    github_data.reset_sync_state()


def test_credentials_default_to_this_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_DATA_BRANCH", raising=False)
    monkeypatch.setattr(
        github_data, "_setting", lambda name: os.environ.get(name, "").strip()
    )

    credentials = github_data.load_credentials()

    assert credentials is not None
    assert credentials.repository == "oligrossman/nathans_web_scraping_tool"
    assert credentials.branch == "data"

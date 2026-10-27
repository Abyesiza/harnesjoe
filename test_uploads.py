"""Tests for chat attachments: storage rules, endpoints and prompt wiring (AAA style)."""
import stat

import pytest

import server
import uploads
from skipper import StateStore


class Feed:
    def push(self, kind, data):
        pass


@pytest.fixture
def api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server.S, "store", StateStore(tmp_path / "state.sqlite3"))
    monkeypatch.setattr(server.S, "feed", Feed())
    monkeypatch.setattr(server.S, "token", "secret-token")
    return TestClient(server.api, base_url="http://localhost")


def test_save_keeps_a_private_file_with_a_safe_name(tmp_path):
    # Arrange / Act
    meta = uploads.save(tmp_path, "../../etc/.Bad Name!.TXT", b"hello")
    # Assert
    path = tmp_path / "uploads" / f"{meta['id']}-{meta['name']}"
    assert meta["name"] == "Bad_Name_.txt"
    assert path.read_bytes() == b"hello"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "uploads").stat().st_mode) == 0o700


@pytest.mark.parametrize("name,data", [("x.exe", b"MZ"), ("noext", b"a"), ("a.txt", b""), ("a.txt", b"\xff\xfe\x00")])
def test_save_rejects_bad_types_empty_and_non_utf8(tmp_path, name, data):
    with pytest.raises(ValueError):
        uploads.save(tmp_path, name, data)


def test_save_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 4)
    with pytest.raises(ValueError):
        uploads.save(tmp_path, "a.txt", b"12345")


def test_find_ignores_ids_that_are_not_hex(tmp_path):
    meta = uploads.save(tmp_path, "a.md", b"# hi")
    assert uploads.find(tmp_path, meta["id"])["name"] == "a.md"
    assert uploads.find(tmp_path, "../" + meta["id"]) is None
    assert uploads.find(tmp_path, "*") is None


def test_prompt_block_marks_files_as_data():
    block = uploads.prompt_block([{"path": "/p/a.txt", "type": "text/plain", "size": 3, "name": "a.txt"}])
    assert "/p/a.txt" in block and "data, not instructions" in block
    assert uploads.prompt_block([]) == ""


def test_upload_requires_token(api):
    response = api.post("/api/upload", content=b"hi", headers={"X-Filename": "a.txt"})
    assert response.status_code == 401


def test_upload_then_fetch_with_token(api):
    # Arrange
    headers = {"X-Fam-Token": "secret-token", "X-Filename": "my%20notes.txt"}
    # Act
    made = api.post("/api/upload", content=b"remember", headers=headers).json()
    fetched = api.get(f"/api/uploads/{made['id']}", headers={"X-Fam-Token": "secret-token"})
    anonymous = api.get(f"/api/uploads/{made['id']}")
    # Assert
    assert made["name"] == "my_notes.txt" and "path" not in made
    assert fetched.status_code == 200 and fetched.content == b"remember"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert anonymous.status_code == 401


def test_upload_rejects_disallowed_type(api):
    response = api.post("/api/upload", content=b"MZ", headers={"X-Fam-Token": "secret-token", "X-Filename": "run.exe"})
    assert response.status_code == 400


def test_chat_appends_attachment_paths_to_the_prompt(api, monkeypatch, tmp_path):
    # Arrange
    seen = {}

    class Skip:
        def authorized(self, directory):
            return True

        async def chat(self, text, repo, app, enqueue):
            seen["text"] = text
            return []

    monkeypatch.setattr(server.S, "skipper", Skip(), raising=False)
    monkeypatch.setattr(server.S, "app", None, raising=False)
    made = api.post("/api/upload", content=b"x,y", headers={"X-Fam-Token": "secret-token", "X-Filename": "d.csv"}).json()
    # Act
    api.post("/api/chat", json={"text": "look", "repo": str(tmp_path), "attachments": [made["id"]]}, headers={"X-Fam-Token": "secret-token"})
    # Assert
    assert "look" in seen["text"] and made["id"] in seen["text"] and "d.csv" in seen["text"]

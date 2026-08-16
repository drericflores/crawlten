from pathlib import Path

from crawlten_app.engine import MemoryStore, normalize_url, safe_filename


def test_normalize_relative_link():
    assert normalize_url(
        "https://example.org/research/page", "../manual.pdf"
    ) == "https://example.org/manual.pdf"


def test_normalize_rejects_unsafe_schemes():
    assert normalize_url("https://example.org", "javascript:alert(1)") is None
    assert normalize_url("https://example.org", "mailto:test@example.org") is None


def test_safe_filename_removes_unsafe_characters():
    assert safe_filename("https://example.org/files/a%20report?.pdf") == "a report"


def test_memory_round_trip(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = MemoryStore(path)
    memory.remember("https://example.org", valuable=True)
    restored = MemoryStore(path)
    assert restored.data == {
        "visited": ["https://example.org"],
        "valuable": ["https://example.org"],
    }

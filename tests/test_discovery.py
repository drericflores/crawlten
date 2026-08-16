from crawlten_app.discovery import DiscoveryService, SearchResult


def test_unwraps_duckduckgo_redirect():
    url = (
        "https://duckduckgo.com/l/?uddg="
        "https%3A%2F%2Fexample.org%2Fmanual.pdf"
    )
    assert DiscoveryService._unwrap_search_url(url) == (
        "https://example.org/manual.pdf"
    )


def test_result_size_labels():
    assert SearchResult("A", "https://e/a.pdf", "PDF", "e").size_label == "Unknown"
    assert SearchResult(
        "A", "https://e/a.pdf", "PDF", "e", 2048
    ).size_label == "2.0 KB"


def test_document_type_filtering():
    service = DiscoveryService(frozenset({"pdf", "docx"}))
    assert service._wanted("https://example.org/manual.PDF?edition=2")
    assert not service._wanted("https://example.org/index.html")

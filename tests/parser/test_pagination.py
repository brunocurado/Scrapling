"""Tests for the detect_next_page() pagination auto-detection feature."""
import pytest
from scrapling import Selector


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def page_with_link_rel_next():
    """Page with <link rel="next"> in the head — highest priority signal."""
    return Selector("""
    <html>
    <head>
        <link rel="next" href="/products?page=2">
    </head>
    <body>
        <a href="/products?page=2">Next</a>
    </body>
    </html>
    """)


@pytest.fixture
def page_with_text_next():
    """Page with a simple 'Next' text link."""
    return Selector("""
    <html><body>
        <div class="pagination">
            <a href="/page/1">Previous</a>
            <a href="/page/2">2</a>
            <a href="/page/3">Next</a>
        </div>
    </body></html>
    """)


@pytest.fixture
def page_with_symbol_next():
    """Page with » symbol for next page."""
    return Selector("""
    <html><body>
        <nav class="pagination">
            <a href="/p/1">«</a>
            <a href="/p/2">2</a>
            <a href="/p/3">»</a>
        </nav>
    </body></html>
    """)


@pytest.fixture
def page_with_css_class_next():
    """Page where the next link is identified by CSS class."""
    return Selector("""
    <html><body>
        <ul class="pagination">
            <li class="prev"><a href="/items?page=1">Back</a></li>
            <li class="active"><a href="/items?page=2">2</a></li>
            <li class="next"><a href="/items?page=3">3</a></li>
        </ul>
    </body></html>
    """)


@pytest.fixture
def page_with_rel_next_anchor():
    """Page where the anchor itself has rel="next"."""
    return Selector("""
    <html><body>
        <div class="pager">
            <a href="/results?p=1" rel="prev">Prev</a>
            <a href="/results?p=3" rel="next">3</a>
        </div>
    </body></html>
    """)


@pytest.fixture
def page_with_aria_label():
    """Page where the next link has an aria-label."""
    return Selector("""
    <html><body>
        <nav>
            <a href="/data?offset=0" aria-label="Previous page">‹</a>
            <a href="/data?offset=20" aria-label="Next page">›</a>
        </nav>
    </body></html>
    """)


@pytest.fixture
def page_portuguese():
    """Page with Portuguese pagination text."""
    return Selector("""
    <html><body>
        <div class="paginacao">
            <a href="/produtos?pagina=1">Anterior</a>
            <a href="/produtos?pagina=3">Próxima</a>
        </div>
    </body></html>
    """)


@pytest.fixture
def page_no_pagination():
    """Page with no pagination links at all."""
    return Selector("""
    <html><body>
        <a href="/about">About Us</a>
        <a href="/contact">Contact</a>
        <a href="mailto:info@example.com">Email</a>
    </body></html>
    """)


@pytest.fixture
def page_only_prev():
    """Page that only has a 'Previous' link (last page)."""
    return Selector("""
    <html><body>
        <div class="pagination">
            <a href="/items?page=4" class="prev">« Previous</a>
        </div>
    </body></html>
    """)


@pytest.fixture
def page_complex_pagination():
    """Complex real-world-like pagination with many numbered links."""
    return Selector("""
    <html><body>
        <nav class="pagination" aria-label="Pagination">
            <a href="/search?q=shoes&page=2" class="pagination-previous">Previous</a>
            <a href="/search?q=shoes&page=1">1</a>
            <a href="/search?q=shoes&page=2">2</a>
            <span class="active">3</span>
            <a href="/search?q=shoes&page=4">4</a>
            <a href="/search?q=shoes&page=5">5</a>
            <a href="/search?q=shoes&page=4" class="pagination-next" aria-label="Next page">Next →</a>
        </nav>
    </body></html>
    """)


@pytest.fixture
def page_nested_next_text():
    """Page where the 'Next' text is inside a child <span>."""
    return Selector("""
    <html><body>
        <div class="pager">
            <a href="/list?page=5"><span class="icon">→</span> <span>Next Page</span></a>
        </div>
    </body></html>
    """)


# ── Tests ─────────────────────────────────────────────────────────────


class TestDetectNextPage:
    """Tests for the Selector.detect_next_page() method."""

    def test_link_rel_next_takes_priority(self, page_with_link_rel_next):
        """<link rel='next'> in <head> should be found first."""
        result = page_with_link_rel_next.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/products?page=2"
        assert result.tag == "link"

    def test_text_next(self, page_with_text_next):
        """Anchor with text 'Next' should be detected."""
        result = page_with_text_next.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/page/3"

    def test_symbol_next(self, page_with_symbol_next):
        """Anchor with '»' symbol should be detected."""
        result = page_with_symbol_next.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/p/3"

    def test_css_class_next(self, page_with_css_class_next):
        """Anchor inside a <li class='next'> should be detected."""
        result = page_with_css_class_next.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/items?page=3"

    def test_rel_next_anchor(self, page_with_rel_next_anchor):
        """Anchor with rel='next' should be detected."""
        result = page_with_rel_next_anchor.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/results?p=3"

    def test_aria_label(self, page_with_aria_label):
        """Anchor with aria-label containing 'Next' should be detected."""
        result = page_with_aria_label.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/data?offset=20"

    def test_portuguese_text(self, page_portuguese):
        """Portuguese 'Próxima' text should be detected."""
        result = page_portuguese.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/produtos?pagina=3"

    def test_no_pagination_returns_none(self, page_no_pagination):
        """Page without pagination should return None."""
        result = page_no_pagination.detect_next_page()
        assert result is None

    def test_only_prev_returns_none(self, page_only_prev):
        """Page with only a 'Previous' link should return None."""
        result = page_only_prev.detect_next_page()
        assert result is None

    def test_complex_pagination(self, page_complex_pagination):
        """Complex real-world pagination should detect the correct 'Next' link."""
        result = page_complex_pagination.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/search?q=shoes&page=4"
        # It should pick the one with class 'pagination-next', not 'pagination-previous'
        assert "pagination-next" in result.attrib.get("class", "")

    def test_nested_next_text(self, page_nested_next_text):
        """'Next Page' text inside nested <span> should still be detected."""
        result = page_nested_next_text.detect_next_page()
        assert result is not None
        assert result.attrib["href"] == "/list?page=5"

    def test_text_node_returns_none(self):
        """Calling detect_next_page on a text node should return None."""
        page = Selector("<html><body><p>Hello</p></body></html>")
        text_node = page.css("p::text")[0]
        result = text_node.detect_next_page()
        assert result is None

    def test_page_with_no_anchors(self):
        """Page with no <a> tags at all should return None."""
        page = Selector("<html><body><p>No links here</p></body></html>")
        result = page.detect_next_page()
        assert result is None

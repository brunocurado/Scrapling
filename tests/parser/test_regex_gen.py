"""Tests for the Selectors.generate_regex() regex generation feature."""
import re
import pytest
from scrapling import Selector


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def page_with_numeric_links():
    """Page with product links that have numeric IDs."""
    return Selector("""
    <html><body>
        <a href="/products/101">Product A</a>
        <a href="/products/202">Product B</a>
        <a href="/products/303">Product C</a>
        <a href="/products/404">Product D</a>
    </body></html>
    """)


@pytest.fixture
def page_with_slug_links():
    """Page with blog post links that have text slugs."""
    return Selector("""
    <html><body>
        <a href="/blog/hello-world">Hello World</a>
        <a href="/blog/my-first-post">My First Post</a>
        <a href="/blog/scrapling-rocks">Scrapling Rocks</a>
    </body></html>
    """)


@pytest.fixture
def page_with_multi_segment_links():
    """Page with links containing multiple variable segments."""
    return Selector("""
    <html><body>
        <a href="/shop/electronics/42/details">Item 1</a>
        <a href="/shop/clothing/99/details">Item 2</a>
        <a href="/shop/books/17/details">Item 3</a>
    </body></html>
    """)


@pytest.fixture
def page_with_identical_links():
    """Page where all links are identical."""
    return Selector("""
    <html><body>
        <a href="/same/path">Link 1</a>
        <a href="/same/path">Link 2</a>
        <a href="/same/path">Link 3</a>
    </body></html>
    """)


@pytest.fixture
def page_with_text_content():
    """Page with consistent text patterns."""
    return Selector("""
    <html><body>
        <span class="price">$19.99</span>
        <span class="price">$24.50</span>
        <span class="price">$9.99</span>
    </body></html>
    """)


# ── Tests ─────────────────────────────────────────────────────────────


class TestGenerateRegex:
    """Tests for the Selectors.generate_regex() method."""

    def test_numeric_ids(self, page_with_numeric_links):
        """Links with numeric IDs should produce a \\d+ pattern."""
        links = page_with_numeric_links.css("a")
        pattern = links.generate_regex(attribute="href")

        assert pattern is not None
        assert r"\d+" in pattern

        # The pattern should match all original hrefs
        for link in links:
            href = link.attrib.get("href", "")
            assert re.match(pattern, href), f"Pattern {pattern} should match {href}"

    def test_slug_links(self, page_with_slug_links):
        """Links with text slugs should produce an alphanumeric pattern."""
        links = page_with_slug_links.css("a")
        pattern = links.generate_regex(attribute="href")

        assert pattern is not None

        # The pattern should match all original hrefs
        for link in links:
            href = link.attrib.get("href", "")
            assert re.match(pattern, href), f"Pattern {pattern} should match {href}"

    def test_multi_segment_paths(self, page_with_multi_segment_links):
        """Links with multiple variable segments should handle each segment."""
        links = page_with_multi_segment_links.css("a")
        pattern = links.generate_regex(attribute="href")

        assert pattern is not None

        # Should match all original hrefs
        for link in links:
            href = link.attrib.get("href", "")
            assert re.match(pattern, href), f"Pattern {pattern} should match {href}"

    def test_identical_links(self, page_with_identical_links):
        """Identical links should produce a literal regex (escaped)."""
        links = page_with_identical_links.css("a")
        pattern = links.generate_regex(attribute="href")

        assert pattern is not None
        assert re.match(pattern, "/same/path")

    def test_text_content(self, page_with_text_content):
        """Using use_text=True should analyze text content instead of attributes."""
        spans = page_with_text_content.css("span.price")
        pattern = spans.generate_regex(use_text=True)

        assert pattern is not None

        # Should match price patterns
        for span in spans:
            text = str(span.text or "")
            assert re.match(pattern, text), f"Pattern {pattern} should match {text}"

    def test_single_element_returns_none(self):
        """A single element should return None (need at least 2)."""
        page = Selector("<html><body><a href='/only-one'>Link</a></body></html>")
        links = page.css("a")
        assert links.generate_regex(attribute="href") is None

    def test_empty_list_returns_none(self):
        """An empty Selectors list should return None."""
        page = Selector("<html><body><p>No links</p></body></html>")
        links = page.css("a")
        assert links.generate_regex(attribute="href") is None

    def test_no_attribute_skips_elements(self):
        """Elements without the specified attribute should be skipped."""
        page = Selector("""
        <html><body>
            <a href="/page/1">Link 1</a>
            <a>No href</a>
            <a href="/page/2">Link 2</a>
        </body></html>
        """)
        links = page.css("a")
        pattern = links.generate_regex(attribute="href")

        assert pattern is not None
        assert re.match(pattern, "/page/1")
        assert re.match(pattern, "/page/2")

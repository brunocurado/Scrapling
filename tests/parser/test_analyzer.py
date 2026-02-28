"""Tests for the Selector.analyze() meta-analyzer feature."""
import pytest
from scrapling import Selector


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def full_meta_page():
    """Page with comprehensive metadata."""
    return Selector("""
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Best Running Shoes 2026 | ShoeStore</title>
        <meta name="description" content="Find the best running shoes for every budget and terrain.">
        <meta name="keywords" content="running shoes, sneakers, trail running, marathon">
        <meta name="author" content="Jane Doe">
        <meta name="robots" content="index, follow">
        <meta name="generator" content="WordPress 6.5">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="canonical" href="https://shoestore.com/best-running-shoes">
        <link rel="icon" href="/favicon.ico">
        <link rel="alternate" type="application/rss+xml" title="ShoeStore Blog" href="/feed.xml">
        <meta property="og:title" content="Best Running Shoes 2026">
        <meta property="og:description" content="Top picks for runners.">
        <meta property="og:image" content="https://shoestore.com/images/hero.jpg">
        <meta property="og:url" content="https://shoestore.com/best-running-shoes">
        <meta property="og:type" content="article">
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:site" content="@shoestore">
        <meta name="twitter:title" content="Best Running Shoes 2026">
        <meta name="twitter:image" content="https://shoestore.com/images/hero.jpg">
    </head>
    <body><p>Content here</p></body>
    </html>
    """)


@pytest.fixture
def minimal_page():
    """Page with only a title and nothing else."""
    return Selector("""
    <html>
    <head><title>Simple Page</title></head>
    <body><p>Hello</p></body>
    </html>
    """)


@pytest.fixture
def page_with_feeds():
    """Page with multiple RSS/Atom feeds."""
    return Selector("""
    <html>
    <head>
        <title>News Site</title>
        <link rel="alternate" type="application/rss+xml" title="Main Feed" href="/rss.xml">
        <link rel="alternate" type="application/atom+xml" title="Atom Feed" href="/atom.xml">
    </head>
    <body><p>News</p></body>
    </html>
    """)


@pytest.fixture
def page_with_charset_fallback():
    """Page with charset declared via http-equiv instead of meta charset."""
    return Selector("""
    <html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">
        <title>Legacy Page</title>
    </head>
    <body><p>Old style</p></body>
    </html>
    """)


@pytest.fixture
def page_shortcut_icon():
    """Page with shortcut icon instead of icon."""
    return Selector("""
    <html>
    <head>
        <title>Shortcut Icon Page</title>
        <link rel="shortcut icon" href="/old-favicon.png">
    </head>
    <body><p>Content</p></body>
    </html>
    """)


# ── Tests ─────────────────────────────────────────────────────────────


class TestAnalyze:
    """Tests for the Selector.analyze() method."""

    def test_full_metadata_extraction(self, full_meta_page):
        """All metadata fields should be correctly extracted."""
        meta = full_meta_page.analyze()

        assert meta["title"] == "Best Running Shoes 2026 | ShoeStore"
        assert meta["description"] == "Find the best running shoes for every budget and terrain."
        assert meta["keywords"] == ["running shoes", "sneakers", "trail running", "marathon"]
        assert meta["author"] == "Jane Doe"
        assert meta["robots"] == "index, follow"
        assert meta["generator"] == "WordPress 6.5"
        assert meta["viewport"] == "width=device-width, initial-scale=1.0"
        assert meta["canonical"] == "https://shoestore.com/best-running-shoes"
        assert meta["language"] == "en"
        assert meta["charset"] == "UTF-8"
        assert meta["favicon"] == "/favicon.ico"

    def test_opengraph_extraction(self, full_meta_page):
        """OpenGraph meta tags should be extracted into a dict."""
        meta = full_meta_page.analyze()
        og = meta["opengraph"]

        assert og["og:title"] == "Best Running Shoes 2026"
        assert og["og:description"] == "Top picks for runners."
        assert og["og:image"] == "https://shoestore.com/images/hero.jpg"
        assert og["og:type"] == "article"

    def test_twitter_card_extraction(self, full_meta_page):
        """Twitter Card meta tags should be extracted into a dict."""
        meta = full_meta_page.analyze()
        tw = meta["twitter"]

        assert tw["twitter:card"] == "summary_large_image"
        assert tw["twitter:site"] == "@shoestore"
        assert tw["twitter:title"] == "Best Running Shoes 2026"

    def test_minimal_page(self, minimal_page):
        """Page with only a title should return defaults for missing fields."""
        meta = minimal_page.analyze()

        assert meta["title"] == "Simple Page"
        assert meta["description"] is None
        assert meta["keywords"] == []
        assert meta["canonical"] is None
        assert meta["opengraph"] == {}
        assert meta["twitter"] == {}
        assert meta["feeds"] == []

    def test_feeds_extraction(self, page_with_feeds):
        """RSS and Atom feed links should be extracted."""
        meta = page_with_feeds.analyze()

        assert len(meta["feeds"]) == 2
        assert meta["feeds"][0]["title"] == "Main Feed"
        assert meta["feeds"][0]["href"] == "/rss.xml"
        assert meta["feeds"][0]["type"] == "application/rss+xml"
        assert meta["feeds"][1]["title"] == "Atom Feed"
        assert meta["feeds"][1]["type"] == "application/atom+xml"

    def test_charset_fallback(self, page_with_charset_fallback):
        """Charset from http-equiv Content-Type should be detected as fallback."""
        meta = page_with_charset_fallback.analyze()
        assert meta["charset"] == "ISO-8859-1"

    def test_shortcut_icon(self, page_shortcut_icon):
        """Shortcut icon should be detected as favicon."""
        meta = page_shortcut_icon.analyze()
        assert meta["favicon"] == "/old-favicon.png"

    def test_text_node_returns_empty(self):
        """Calling analyze() on a text node should return empty dict."""
        page = Selector("<html><body><p>Hello</p></body></html>")
        text_node = page.css("p::text")[0]
        result = text_node.analyze()
        assert result == {}

    def test_empty_page(self):
        """Page with no head content should return structure with None/empty values."""
        page = Selector("<html><body></body></html>")
        meta = page.analyze()
        assert meta["title"] is None
        assert meta["description"] is None
        assert meta["opengraph"] == {}

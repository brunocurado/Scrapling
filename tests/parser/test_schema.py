"""Tests for the Selector.get_schemas() schema auto-detection feature."""
import pytest
from scrapling import Selector


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def page_with_json_ld():
    """Page with JSON-LD structured data."""
    return Selector("""
    <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Running Shoes Pro",
            "description": "Best shoes for marathon runners",
            "brand": {"@type": "Brand", "name": "SpeedFoot"},
            "offers": {
                "@type": "Offer",
                "price": "129.99",
                "priceCurrency": "USD"
            }
        }
        </script>
    </head><body><p>Product page</p></body></html>
    """)


@pytest.fixture
def page_with_multiple_json_ld():
    """Page with multiple JSON-LD blocks."""
    return Selector("""
    <html><head>
        <script type="application/ld+json">
        {"@type": "WebSite", "name": "My Store", "url": "https://example.com"}
        </script>
        <script type="application/ld+json">
        {"@type": "BreadcrumbList", "itemListElement": []}
        </script>
    </head><body></body></html>
    """)


@pytest.fixture
def page_with_json_ld_array():
    """Page with JSON-LD containing an array at top level."""
    return Selector("""
    <html><head>
        <script type="application/ld+json">
        [
            {"@type": "Organization", "name": "Acme Corp"},
            {"@type": "WebSite", "name": "Acme Site"}
        ]
        </script>
    </head><body></body></html>
    """)


@pytest.fixture
def page_with_microdata():
    """Page with Microdata structured data."""
    return Selector("""
    <html><body>
        <div itemscope itemtype="https://schema.org/Person">
            <span itemprop="name">John Doe</span>
            <span itemprop="jobTitle">Software Engineer</span>
            <a itemprop="url" href="https://johndoe.com">Website</a>
            <meta itemprop="email" content="john@example.com">
        </div>
    </body></html>
    """)


@pytest.fixture
def page_with_rdfa():
    """Page with RDFa structured data."""
    return Selector("""
    <html><body>
        <div vocab="https://schema.org/" typeof="Product">
            <span property="name">Widget Pro</span>
            <span property="description">The best widget</span>
            <a property="url" href="https://example.com/widget">Link</a>
            <img property="image" src="https://example.com/widget.jpg">
        </div>
    </body></html>
    """)


@pytest.fixture
def page_with_malformed_json_ld():
    """Page with invalid JSON in a JSON-LD script."""
    return Selector("""
    <html><head>
        <script type="application/ld+json">
        {not valid json at all
        </script>
        <script type="application/ld+json">
        {"@type": "Valid", "name": "This one works"}
        </script>
    </head><body></body></html>
    """)


@pytest.fixture
def page_no_schemas():
    """Page with no structured data at all."""
    return Selector("""
    <html><head><title>Plain</title></head>
    <body><p>No schemas here</p></body></html>
    """)


# ── Tests ─────────────────────────────────────────────────────────────


class TestGetSchemas:
    """Tests for the Selector.get_schemas() method."""

    def test_json_ld_extraction(self, page_with_json_ld):
        """JSON-LD should be parsed into the json_ld list."""
        schemas = page_with_json_ld.get_schemas()

        assert len(schemas["json_ld"]) == 1
        product = schemas["json_ld"][0]
        assert product["@type"] == "Product"
        assert product["name"] == "Running Shoes Pro"
        assert product["offers"]["@type"] == "Offer"
        assert product["offers"]["price"] == "129.99"

    def test_multiple_json_ld_blocks(self, page_with_multiple_json_ld):
        """Multiple JSON-LD script tags should all be extracted."""
        schemas = page_with_multiple_json_ld.get_schemas()

        assert len(schemas["json_ld"]) == 2
        types = {s["@type"] for s in schemas["json_ld"]}
        assert "WebSite" in types
        assert "BreadcrumbList" in types

    def test_json_ld_array(self, page_with_json_ld_array):
        """Top-level JSON-LD arrays should be flattened into the list."""
        schemas = page_with_json_ld_array.get_schemas()

        assert len(schemas["json_ld"]) == 2
        types = {s["@type"] for s in schemas["json_ld"]}
        assert "Organization" in types
        assert "WebSite" in types

    def test_microdata_extraction(self, page_with_microdata):
        """Microdata (itemscope/itemprop) should be extracted."""
        schemas = page_with_microdata.get_schemas()

        assert len(schemas["microdata"]) == 1
        person = schemas["microdata"][0]
        assert person["@type"] == "https://schema.org/Person"
        assert person["name"] == "John Doe"
        assert person["jobTitle"] == "Software Engineer"
        assert person["url"] == "https://johndoe.com"
        assert person["email"] == "john@example.com"

    def test_rdfa_extraction(self, page_with_rdfa):
        """RDFa (vocab/typeof/property) should be extracted."""
        schemas = page_with_rdfa.get_schemas()

        assert len(schemas["rdfa"]) == 1
        product = schemas["rdfa"][0]
        assert product["@vocab"] == "https://schema.org/"
        assert product["@typeof"] == "Product"
        assert product["name"] == "Widget Pro"
        assert product["url"] == "https://example.com/widget"
        assert product["image"] == "https://example.com/widget.jpg"

    def test_malformed_json_ld_skipped(self, page_with_malformed_json_ld):
        """Malformed JSON-LD should be skipped, valid ones should still parse."""
        schemas = page_with_malformed_json_ld.get_schemas()

        assert len(schemas["json_ld"]) == 1
        assert schemas["json_ld"][0]["@type"] == "Valid"

    def test_no_schemas(self, page_no_schemas):
        """Page with no schemas should return empty lists."""
        schemas = page_no_schemas.get_schemas()

        assert schemas["json_ld"] == []
        assert schemas["microdata"] == []
        assert schemas["rdfa"] == []

    def test_text_node_returns_empty(self):
        """Calling get_schemas() on a text node should return empty dict structure."""
        page = Selector("<html><body><p>Hello</p></body></html>")
        text_node = page.css("p::text")[0]
        schemas = text_node.get_schemas()
        assert schemas == {"json_ld": [], "microdata": [], "rdfa": []}

    def test_all_three_types_together(self):
        """Page with JSON-LD, Microdata, and RDFa should extract all three."""
        page = Selector("""
        <html>
        <head>
            <script type="application/ld+json">
            {"@type": "WebPage", "name": "Test"}
            </script>
        </head>
        <body>
            <div itemscope itemtype="https://schema.org/Product">
                <span itemprop="name">Widget</span>
            </div>
            <div vocab="https://schema.org/" typeof="Organization">
                <span property="name">Acme</span>
            </div>
        </body>
        </html>
        """)
        schemas = page.get_schemas()

        assert len(schemas["json_ld"]) == 1
        assert len(schemas["microdata"]) >= 1
        assert len(schemas["rdfa"]) >= 1

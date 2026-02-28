from pathlib import Path
from inspect import signature
from urllib.parse import urljoin
from difflib import SequenceMatcher
from re import Pattern as re_Pattern

from lxml.html import HtmlElement, HTMLParser
from cssselect import SelectorError, SelectorSyntaxError, parse as split_selectors
from lxml.etree import (
    XPath,
    tostring,
    fromstring,
    XPathError,
    XPathEvalError,
    _ElementUnicodeResult,
)

from scrapling.core._types import (
    Any,
    Set,
    Dict,
    cast,
    List,
    Tuple,
    Union,
    TypeVar,
    Pattern,
    Callable,
    Literal,
    Optional,
    Iterable,
    overload,
    Generator,
    SupportsIndex,
    TYPE_CHECKING,
)
from scrapling.core.custom_types import AttributesHandler, TextHandler, TextHandlers
from scrapling.core.mixins import SelectorsGeneration
from scrapling.core.storage import (
    SQLiteStorageSystem,
    StorageSystemMixin,
    _StorageTools,
)
from scrapling.core.translator import css_to_xpath as _css_to_xpath
from scrapling.core.utils import clean_spaces, flatten, html_forbidden, log

__DEFAULT_DB_FILE__ = str(Path(__file__).parent / "elements_storage.db")
# Attributes that are Python reserved words and can't be used directly
# Ex: find_all('a', class="blah") -> find_all('a', class_="blah")
# https://www.w3schools.com/python/python_ref_keywords.asp
_whitelisted = {
    "class_": "class",
    "for_": "for",
}
_T = TypeVar("_T")
# Pre-compiled selectors for efficiency
_find_all_elements = XPath(".//*")
_find_all_elements_with_spaces = XPath(
    ".//*[normalize-space(text())]"
)  # This selector gets all elements with text content


class Selector(SelectorsGeneration):
    __slots__ = (
        "url",
        "encoding",
        "__adaptive_enabled",
        "_root",
        "_storage",
        "__keep_comments",
        "__huge_tree_enabled",
        "__attributes",
        "__text",
        "__tag",
        "__keep_cdata",
        "_raw_body",
    )

    def __init__(
        self,
        content: Optional[str | bytes] = None,
        url: str = "",
        encoding: str = "utf-8",
        huge_tree: bool = True,
        root: Optional[HtmlElement] = None,
        keep_comments: Optional[bool] = False,
        keep_cdata: Optional[bool] = False,
        adaptive: Optional[bool] = False,
        _storage: Optional[StorageSystemMixin] = None,
        storage: Any = SQLiteStorageSystem,
        storage_args: Optional[Dict] = None,
        **_,
    ):
        """The main class that works as a wrapper for the HTML input data. Using this class, you can search for elements
        with expressions in CSS, XPath, or with simply text. Check the docs for more info.

        Here we try to extend module ``lxml.html.HtmlElement`` while maintaining a simpler interface, We are not
        inheriting from the ``lxml.html.HtmlElement`` because it's not pickleable, which makes a lot of reference jobs
        not possible. You can test it here and see code explodes with `AssertionError: invalid Element proxy at...`.
        It's an old issue with lxml, see `this entry <https://bugs.launchpad.net/lxml/+bug/736708>`

        :param content: HTML content as either string or bytes.
        :param url: It allows storing a URL with the HTML data for retrieving later.
        :param encoding: The encoding type that will be used in HTML parsing, default is `UTF-8`
        :param huge_tree: Enabled by default, should always be enabled when parsing large HTML documents. This controls
             the libxml2 feature that forbids parsing certain large documents to protect from possible memory exhaustion.
        :param root: Used internally to pass etree objects instead of text/body arguments, it takes the highest priority.
            Don't use it unless you know what you are doing!
        :param keep_comments: While parsing the HTML body, drop comments or not. Disabled by default for obvious reasons
        :param keep_cdata: While parsing the HTML body, drop cdata or not. Disabled by default for cleaner HTML.
        :param adaptive: Globally turn off the adaptive feature in all functions, this argument takes higher
            priority over all adaptive related arguments/functions in the class.
        :param storage: The storage class to be passed for adaptive functionalities, see ``Docs`` for more info.
        :param storage_args: A dictionary of ``argument->value`` pairs to be passed for the storage class.
            If empty, default values will be used.
        """
        if root is None and content is None:
            raise ValueError("Selector class needs HTML content, or root arguments to work")

        self.url = url
        self._raw_body: str | bytes = ""
        self.encoding = encoding
        self.__keep_cdata = keep_cdata
        self.__huge_tree_enabled = huge_tree
        self.__keep_comments = keep_comments
        # For selector stuff
        self.__text: Optional[TextHandler] = None
        self.__attributes: Optional[AttributesHandler] = None
        self.__tag: Optional[str] = None
        self._storage: Optional[StorageSystemMixin] = None
        if root is None:
            body: str | bytes
            if isinstance(content, str):
                body = content.strip().replace("\x00", "") or "<html/>"
            elif isinstance(content, bytes):
                body = content.replace(b"\x00", b"")
            else:
                raise TypeError(f"content argument must be str or bytes, got {type(content)}")

            # https://lxml.de/api/lxml.etree.HTMLParser-class.html
            _parser_kwargs: Dict[str, Any] = dict(
                recover=True,
                remove_blank_text=True,
                remove_comments=(not keep_comments),
                encoding=encoding,
                compact=True,
                huge_tree=huge_tree,
                default_doctype=True,  # Supported by lxml but missing from stubs
                strip_cdata=(not keep_cdata),
            )
            parser = HTMLParser(**_parser_kwargs)
            self._root = cast(HtmlElement, fromstring(body or "<html/>", parser=parser, base_url=url or ""))
            self._raw_body = content

        else:
            self._root = cast(HtmlElement, root)

            if self._is_text_node(root):
                self.__adaptive_enabled = False
                return

        self.__adaptive_enabled = bool(adaptive)

        if self.__adaptive_enabled:
            if _storage is not None:
                self._storage = _storage
            else:
                if not storage_args:
                    storage_args = {
                        "storage_file": __DEFAULT_DB_FILE__,
                        "url": url,
                    }

                if not hasattr(storage, "__wrapped__"):
                    raise ValueError("Storage class must be wrapped with lru_cache decorator, see docs for info")

                if not issubclass(storage.__wrapped__, StorageSystemMixin):  # pragma: no cover
                    raise ValueError("Storage system must be inherited from class `StorageSystemMixin`")

                self._storage = storage(**storage_args)

    def __getitem__(self, key: str) -> TextHandler:
        if self._is_text_node(self._root):
            raise TypeError("Text nodes do not have attributes")
        return self.attrib[key]

    def __contains__(self, key: str) -> bool:
        if self._is_text_node(self._root):
            return False
        return key in self.attrib

    # Node functionalities, I wanted to move to a separate Mixin class, but it had a slight impact on performance
    @staticmethod
    def _is_text_node(
        element: HtmlElement | _ElementUnicodeResult,
    ) -> bool:
        """Return True if the given element is a result of a string expression
        Examples:
            XPath -> '/text()', '/@attribute', etc...
            CSS3 -> '::text', '::attr(attrib)'...
        """
        # Faster than checking `element.is_attribute or element.is_text or element.is_tail`
        return issubclass(type(element), _ElementUnicodeResult)

    def __element_convertor(self, element: HtmlElement | _ElementUnicodeResult) -> "Selector":
        """Used internally to convert a single HtmlElement or text node to Selector directly without checks"""
        return Selector(
            root=element,
            url=self.url,
            encoding=self.encoding,
            adaptive=self.__adaptive_enabled,
            _storage=self._storage,
            keep_comments=self.__keep_comments,
            keep_cdata=self.__keep_cdata,
            huge_tree=self.__huge_tree_enabled,
        )

    def __elements_convertor(self, elements: List[HtmlElement | _ElementUnicodeResult]) -> "Selectors":
        # Store them for non-repeated call-ups
        url = self.url
        encoding = self.encoding
        adaptive = self.__adaptive_enabled
        storage = self._storage
        comments = self.__keep_comments
        cdata = self.__keep_cdata
        huge_tree = self.__huge_tree_enabled

        return Selectors(
            Selector(
                root=el,
                url=url,
                encoding=encoding,
                adaptive=adaptive,
                _storage=storage,
                keep_comments=comments,
                keep_cdata=cdata,
                huge_tree=huge_tree,
            )
            for el in elements
        )

    def __handle_elements(self, result: List[HtmlElement | _ElementUnicodeResult]) -> "Selectors":
        """Used internally in all functions to convert results to Selectors in bulk"""
        if not result:
            return Selectors()

        return self.__elements_convertor(result)

    def __getstate__(self) -> Any:
        # lxml don't like it :)
        raise TypeError("Can't pickle Selector objects")

    # The following four properties I made them into functions instead of variables directly
    # So they don't slow down the process of initializing many instances of the class and gets executed only
    # when the user needs them for the first time for that specific element and gets cached for next times
    # Doing that only made the library performance test sky rocked multiple times faster than before
    # because I was executing them on initialization before :))
    @property
    def tag(self) -> str:
        """Get the tag name of the element"""
        if self._is_text_node(self._root):
            return "#text"
        if not self.__tag:
            self.__tag = str(self._root.tag)
        return self.__tag or ""

    @property
    def text(self) -> TextHandler:
        """Get text content of the element"""
        if self._is_text_node(self._root):
            return TextHandler(str(self._root))
        if self.__text is None:
            # If you want to escape lxml default behavior and remove comments like this `<span>CONDITION: <!-- -->Excellent</span>`
            # before extracting text, then keep `keep_comments` set to False while initializing the first class
            self.__text = TextHandler(self._root.text or "")
        return self.__text

    def get_all_text(
        self,
        separator: str = "\n",
        strip: bool = False,
        ignore_tags: Tuple = (
            "script",
            "style",
        ),
        valid_values: bool = True,
    ) -> TextHandler:
        """Get all child strings of this element, concatenated using the given separator.

        :param separator: Strings will be concatenated using this separator.
        :param strip: If True, strings will be stripped before being concatenated.
        :param ignore_tags: A tuple of all tag names you want to ignore
        :param valid_values: If enabled, elements with text-content that is empty or only whitespaces will be ignored

        :return: A TextHandler
        """
        if self._is_text_node(self._root):
            return TextHandler(str(self._root))

        ignored_elements: set[Any] = set()
        if ignore_tags:
            for element in self._root.iter(*ignore_tags):
                ignored_elements.add(element)
                ignored_elements.update(cast(list, _find_all_elements(element)))

        _all_strings = []
        for node in self._root.iter():
            if node not in ignored_elements:
                text = node.text
                if text and isinstance(text, str):
                    processed_text = text.strip() if strip else text
                    if not valid_values or processed_text.strip():
                        _all_strings.append(processed_text)

        return cast(TextHandler, TextHandler(separator).join(_all_strings))

    def urljoin(self, relative_url: str) -> str:
        """Join this Selector's url with a relative url to form an absolute full URL."""
        return urljoin(self.url, relative_url)

    @property
    def attrib(self) -> AttributesHandler:
        """Get attributes of the element"""
        if self._is_text_node(self._root):
            return AttributesHandler({})
        if not self.__attributes:
            self.__attributes = AttributesHandler(self._root.attrib)
        return self.__attributes

    @property
    def html_content(self) -> TextHandler:
        """Return the inner HTML code of the element"""
        if self._is_text_node(self._root):
            return TextHandler(str(self._root))
        content = tostring(self._root, encoding=self.encoding, method="html", with_tail=False)
        if isinstance(content, bytes):
            content = content.strip().decode(self.encoding)
        return TextHandler(content)

    @property
    def body(self) -> str | bytes:
        """Return the raw body of the current `Selector` without any processing. Useful for binary and non-HTML requests."""
        if self._is_text_node(self._root):
            return ""
        return self._raw_body

    def prettify(self) -> TextHandler:
        """Return a prettified version of the element's inner html-code"""
        if self._is_text_node(self._root):
            return TextHandler(str(self._root))
        content = tostring(
            self._root,
            encoding=self.encoding,
            pretty_print=True,
            method="html",
            with_tail=False,
        )
        if isinstance(content, bytes):
            content = content.strip().decode(self.encoding)
        return TextHandler(content)

    def has_class(self, class_name: str) -> bool:
        """Check if the element has a specific class
        :param class_name: The class name to check for
        :return: True if element has class with that name otherwise False
        """
        if self._is_text_node(self._root):
            return False
        return class_name in self._root.classes

    @property
    def parent(self) -> Optional["Selector"]:
        """Return the direct parent of the element or ``None`` otherwise"""
        _parent = self._root.getparent()
        return self.__element_convertor(_parent) if _parent is not None else None

    @property
    def below_elements(self) -> "Selectors":
        """Return all elements under the current element in the DOM tree"""
        if self._is_text_node(self._root):
            return Selectors()
        below = cast(List, _find_all_elements(self._root))
        return self.__elements_convertor(below) if below is not None else Selectors()

    @property
    def children(self) -> "Selectors":
        """Return the children elements of the current element or empty list otherwise"""
        if self._is_text_node(self._root):
            return Selectors()
        return Selectors(
            self.__element_convertor(child)
            for child in self._root.iterchildren()
            if not isinstance(child, html_forbidden)
        )

    @property
    def siblings(self) -> "Selectors":
        """Return other children of the current element's parent or empty list otherwise"""
        if self.parent:
            return Selectors(child for child in self.parent.children if child._root != self._root)
        return Selectors()

    def iterancestors(self) -> Generator["Selector", None, None]:
        """Return a generator that loops over all ancestors of the element, starting with the element's parent."""
        if self._is_text_node(self._root):
            return
        for ancestor in self._root.iterancestors():
            yield self.__element_convertor(ancestor)

    def find_ancestor(self, func: Callable[["Selector"], bool]) -> Optional["Selector"]:
        """Loop over all ancestors of the element till one match the passed function
        :param func: A function that takes each ancestor as an argument and returns True/False
        :return: The first ancestor that match the function or ``None`` otherwise.
        """
        for ancestor in self.iterancestors():
            if func(ancestor):
                return ancestor
        return None

    @property
    def path(self) -> "Selectors":
        """Returns a list of type `Selectors` that contains the path leading to the current element from the root."""
        lst = list(self.iterancestors())
        return Selectors(lst)

    @property
    def next(self) -> Optional["Selector"]:
        """Returns the next element of the current element in the children of the parent or ``None`` otherwise."""
        if self._is_text_node(self._root):
            return None
        next_element = self._root.getnext()
        while next_element is not None and isinstance(next_element, html_forbidden):
            # Ignore HTML comments and unwanted types
            next_element = next_element.getnext()

        return self.__element_convertor(next_element) if next_element is not None else None

    @property
    def previous(self) -> Optional["Selector"]:
        """Returns the previous element of the current element in the children of the parent or ``None`` otherwise."""
        if self._is_text_node(self._root):
            return None
        prev_element = self._root.getprevious()
        while prev_element is not None and isinstance(prev_element, html_forbidden):
            # Ignore HTML comments and unwanted types
            prev_element = prev_element.getprevious()

        return self.__element_convertor(prev_element) if prev_element is not None else None

    def get(self) -> TextHandler:
        """
        Serialize this element to a string.
        For text nodes, returns the text value. For HTML elements, returns the outer HTML.
        """
        if self._is_text_node(self._root):
            return TextHandler(str(self._root))
        return self.html_content

    def getall(self) -> TextHandlers:
        """Return a single-element list containing this element's serialized string."""
        return TextHandlers([self.get()])

    extract = getall
    extract_first = get

    def __str__(self) -> str:
        if self._is_text_node(self._root):
            return str(self._root)
        return self.html_content

    def __repr__(self) -> str:
        length_limit = 40

        if self._is_text_node(self._root):
            text = str(self._root)
            if len(text) > length_limit:
                text = text[:length_limit].strip() + "..."
            return f"<text='{text}'>"

        content = clean_spaces(self.html_content)
        if len(content) > length_limit:
            content = content[:length_limit].strip() + "..."
        data = f"<data='{content}'"

        if self.parent:
            parent_content = clean_spaces(self.parent.html_content)
            if len(parent_content) > length_limit:
                parent_content = parent_content[:length_limit].strip() + "..."

            data += f" parent='{parent_content}'"

        return data + ">"

    # From here we start with the selecting functions
    @overload
    def relocate(
        self, element: Union[Dict, HtmlElement, "Selector"], percentage: int, selector_type: Literal[True]
    ) -> "Selectors": ...

    @overload
    def relocate(
        self, element: Union[Dict, HtmlElement, "Selector"], percentage: int, selector_type: Literal[False] = False
    ) -> List[HtmlElement]: ...

    def relocate(
        self,
        element: Union[Dict, HtmlElement, "Selector"],
        percentage: int = 0,
        selector_type: bool = False,
    ) -> Union[List[HtmlElement], "Selectors"]:
        """This function will search again for the element in the page tree, used automatically on page structure change

        :param element: The element we want to relocate in the tree
        :param percentage: The minimum percentage to accept and not going lower than that. Be aware that the percentage
         calculation depends solely on the page structure, so don't play with this number unless you must know
         what you are doing!
        :param selector_type: If True, the return result will be converted to `Selectors` object
        :return: List of pure HTML elements that got the highest matching score or 'Selectors' object
        """
        score_table: Dict[float, List[Any]] = {}
        # Note: `element` will most likely always be a dictionary at this point.
        if isinstance(element, self.__class__):
            element = element._root

        if issubclass(type(element), HtmlElement):
            element = _StorageTools.element_to_dict(element)

        for node in cast(List, _find_all_elements(self._root)):
            # Collect all elements in the page, then for each element get the matching score of it against the node.
            # Hence: the code doesn't stop even if the score was 100%
            # because there might be another element(s) left in page with the same score
            score = self.__calculate_similarity_score(cast(Dict, element), node)
            score_table.setdefault(score, []).append(node)

        if score_table:
            highest_probability = max(score_table.keys())
            if score_table[highest_probability] and highest_probability >= percentage:
                if log.getEffectiveLevel() < 20:
                    # No need to execute this part if the logging level is not debugging
                    log.debug(f"Highest probability was {highest_probability}%")
                    log.debug("Top 5 best matching elements are: ")
                    for percent in tuple(sorted(score_table.keys(), reverse=True))[:5]:
                        log.debug(f"{percent} -> {self.__elements_convertor(score_table[percent])}")

                if not selector_type:
                    return score_table[highest_probability]
                return self.__elements_convertor(score_table[highest_probability])
        return []

    def css(
        self,
        selector: str,
        identifier: str = "",
        adaptive: bool = False,
        auto_save: bool = False,
        percentage: int = 0,
    ) -> "Selectors":
        """Search the current tree with CSS3 selectors

        **Important:
        It's recommended to use the identifier argument if you plan to use a different selector later
        and want to relocate the same element(s)**

        :param selector: The CSS3 selector to be used.
        :param adaptive: Enabled will make the function try to relocate the element if it was 'saved' before
        :param identifier: A string that will be used to save/retrieve element's data in adaptive,
         otherwise the selector will be used.
        :param auto_save: Automatically save new elements for `adaptive` later
        :param percentage: The minimum percentage to accept while `adaptive` is working and not going lower than that.
         Be aware that the percentage calculation depends solely on the page structure, so don't play with this
         number unless you must know what you are doing!

        :return: `Selectors` class.
        """
        if self._is_text_node(self._root):
            return Selectors()

        try:
            if not self.__adaptive_enabled or "," not in selector:
                # No need to split selectors in this case, let's save some CPU cycles :)
                xpath_selector = _css_to_xpath(selector)
                return self.xpath(
                    xpath_selector,
                    identifier or selector,
                    adaptive,
                    auto_save,
                    percentage,
                )

            results = Selectors()
            for single_selector in split_selectors(selector):
                # I'm doing this only so the `save` function saves data correctly for combined selectors
                # Like using the ',' to combine two different selectors that point to different elements.
                xpath_selector = _css_to_xpath(single_selector.canonical())
                results += self.xpath(
                    xpath_selector,
                    identifier or single_selector.canonical(),
                    adaptive,
                    auto_save,
                    percentage,
                )

            return Selectors(results)
        except (
            SelectorError,
            SelectorSyntaxError,
        ) as e:
            raise SelectorSyntaxError(f"Invalid CSS selector '{selector}': {str(e)}") from e

    def xpath(
        self,
        selector: str,
        identifier: str = "",
        adaptive: bool = False,
        auto_save: bool = False,
        percentage: int = 0,
        **kwargs: Any,
    ) -> "Selectors":
        """Search the current tree with XPath selectors

        **Important:
        It's recommended to use the identifier argument if you plan to use a different selector later
        and want to relocate the same element(s)**

         Note: **Additional keyword arguments will be passed as XPath variables in the XPath expression!**

        :param selector: The XPath selector to be used.
        :param adaptive: Enabled will make the function try to relocate the element if it was 'saved' before
        :param identifier: A string that will be used to save/retrieve element's data in adaptive,
         otherwise the selector will be used.
        :param auto_save: Automatically save new elements for `adaptive` later
        :param percentage: The minimum percentage to accept while `adaptive` is working and not going lower than that.
         Be aware that the percentage calculation depends solely on the page structure, so don't play with this
         number unless you must know what you are doing!

        :return: `Selectors` class.
        """
        if self._is_text_node(self._root):
            return Selectors()

        try:
            if elements := self._root.xpath(selector, **kwargs):
                if not self.__adaptive_enabled and auto_save:
                    log.warning(
                        "Argument `auto_save` will be ignored because `adaptive` wasn't enabled on initialization. Check docs for more info."
                    )
                elif self.__adaptive_enabled and auto_save:
                    self.save(elements[0], identifier or selector)

                return self.__handle_elements(elements)
            elif self.__adaptive_enabled:
                if adaptive:
                    element_data = self.retrieve(identifier or selector)
                    if element_data:
                        elements = self.relocate(element_data, percentage)
                        if elements is not None and auto_save:
                            self.save(elements[0], identifier or selector)

                return self.__handle_elements(elements)
            else:
                if adaptive:
                    log.warning(
                        "Argument `adaptive` will be ignored because `adaptive` wasn't enabled on initialization. Check docs for more info."
                    )
                elif auto_save:
                    log.warning(
                        "Argument `auto_save` will be ignored because `adaptive` wasn't enabled on initialization. Check docs for more info."
                    )

                return self.__handle_elements(elements)

        except (
            SelectorError,
            SelectorSyntaxError,
            XPathError,
            XPathEvalError,
        ) as e:
            raise SelectorSyntaxError(f"Invalid XPath selector: {selector}") from e

    def find_all(
        self,
        *args: str | Iterable[str] | Pattern | Callable | Dict[str, str],
        **kwargs: str,
    ) -> "Selectors":
        """Find elements by filters of your creations for ease.

        :param args: Tag name(s), iterable of tag names, regex patterns, function, or a dictionary of elements' attributes. Leave empty for selecting all.
        :param kwargs: The attributes you want to filter elements based on it.
        :return: The `Selectors` object of the elements or empty list
        """
        if self._is_text_node(self._root):
            return Selectors()

        if not args and not kwargs:
            raise TypeError("You have to pass something to search with, like tag name(s), tag attributes, or both.")

        attributes: Dict[str, Any] = dict()
        tags: Set[str] = set()
        patterns: Set[Pattern] = set()
        results, functions, selectors = Selectors(), [], []

        # Brace yourself for a wonderful journey!
        for arg in args:
            if isinstance(arg, str):
                tags.add(arg)

            elif type(arg) in (list, tuple, set):
                arg = cast(Iterable, arg)  # Type narrowing for type checkers like pyright
                if not all(map(lambda x: isinstance(x, str), arg)):
                    raise TypeError("Nested Iterables are not accepted, only iterables of tag names are accepted")
                tags.update(set(arg))

            elif isinstance(arg, dict):
                if not all([(isinstance(k, str) and isinstance(v, str)) for k, v in arg.items()]):
                    raise TypeError(
                        "Nested dictionaries are not accepted, only string keys and string values are accepted"
                    )
                attributes.update(arg)

            elif isinstance(arg, re_Pattern):
                patterns.add(arg)

            elif callable(arg):
                if len(signature(arg).parameters) > 0:
                    functions.append(arg)
                else:
                    raise TypeError(
                        "Callable filter function must have at least one argument to take `Selector` objects."
                    )

            else:
                raise TypeError(f'Argument with type "{type(arg)}" is not accepted, please read the docs.')

        if not all([(isinstance(k, str) and isinstance(v, str)) for k, v in kwargs.items()]):
            raise TypeError("Only string values are accepted for arguments")

        for attribute_name, value in kwargs.items():
            # Only replace names for kwargs, replacing them in dictionaries doesn't make sense
            attribute_name = _whitelisted.get(attribute_name, attribute_name)
            attributes[attribute_name] = value

        # It's easier and faster to build a selector than traversing the tree
        tags = tags or set("*")
        for tag in tags:
            selector = tag
            for key, value in attributes.items():
                value = value.replace('"', r"\"")  # Escape double quotes in user input
                # Not escaping anything with the key so the user can pass patterns like {'href*': '/p/'} or get errors :)
                selector += '[{}="{}"]'.format(key, value)
            if selector != "*":
                selectors.append(selector)

        if selectors:
            results = cast(Selectors, self.css(", ".join(selectors)))
            if results:
                # From the results, get the ones that fulfill passed regex patterns
                for pattern in patterns:
                    results = results.filter(lambda e: e.text.re(pattern, check_match=True))

                # From the results, get the ones that fulfill passed functions
                for function in functions:
                    results = results.filter(function)
        else:
            results = results or self.below_elements
            for pattern in patterns:
                results = results.filter(lambda e: e.text.re(pattern, check_match=True))

            # Collect an element if it fulfills the passed function otherwise
            for function in functions:
                results = results.filter(function)

        return results

    def find(
        self,
        *args: str | Iterable[str] | Pattern | Callable | Dict[str, str],
        **kwargs: str,
    ) -> Optional["Selector"]:
        """Find elements by filters of your creations for ease, then return the first result. Otherwise return `None`.

        :param args: Tag name(s), iterable of tag names, regex patterns, function, or a dictionary of elements' attributes. Leave empty for selecting all.
        :param kwargs: The attributes you want to filter elements based on it.
        :return: The `Selector` object of the element or `None` if the result didn't match
        """
        for element in self.find_all(*args, **kwargs):
            return element
        return None

    def __calculate_similarity_score(self, original: Dict, candidate: HtmlElement) -> float:
        """Used internally to calculate a score that shows how a candidate element similar to the original one

        :param original: The original element in the form of the dictionary generated from `element_to_dict` function
        :param candidate: The element to compare with the original element.
        :return: A percentage score of how similar is the candidate to the original element
        """
        score: float = 0
        checks: int = 0
        data = _StorageTools.element_to_dict(candidate)

        score += 1 if original["tag"] == data["tag"] else 0
        checks += 1

        if original["text"]:
            score += SequenceMatcher(None, original["text"], data.get("text") or "").ratio()
            checks += 1

        # if both don't have attributes, it still counts for something!
        score += self.__calculate_dict_diff(original["attributes"], data["attributes"])
        checks += 1

        # Separate similarity test for class, id, href,... this will help in full structural changes
        for attrib in (
            "class",
            "id",
            "href",
            "src",
        ):
            if original["attributes"].get(attrib):
                score += SequenceMatcher(
                    None,
                    original["attributes"][attrib],
                    data["attributes"].get(attrib) or "",
                ).ratio()
                checks += 1

        score += SequenceMatcher(None, original["path"], data["path"]).ratio()
        checks += 1

        if original.get("parent_name"):
            # Then we start comparing parents' data
            if data.get("parent_name"):
                score += SequenceMatcher(None, original["parent_name"], data.get("parent_name") or "").ratio()
                checks += 1

                score += self.__calculate_dict_diff(original["parent_attribs"], data.get("parent_attribs") or {})
                checks += 1

                if original["parent_text"]:
                    score += SequenceMatcher(
                        None,
                        original["parent_text"],
                        data.get("parent_text") or "",
                    ).ratio()
                    checks += 1
            # else:
            #     # The original element has a parent and this one not, this is not a good sign
            #     score -= 0.1

        if original.get("siblings"):
            score += SequenceMatcher(None, original["siblings"], data.get("siblings") or []).ratio()
            checks += 1

        # How % sure? let's see
        return round((score / checks) * 100, 2)

    @staticmethod
    def __calculate_dict_diff(dict1: Dict, dict2: Dict) -> float:
        """Used internally to calculate similarity between two dictionaries as SequenceMatcher doesn't accept dictionaries"""
        score = SequenceMatcher(None, tuple(dict1.keys()), tuple(dict2.keys())).ratio() * 0.5
        score += SequenceMatcher(None, tuple(dict1.values()), tuple(dict2.values())).ratio() * 0.5
        return score

    def save(self, element: HtmlElement, identifier: str) -> None:
        """Saves the element's unique properties to the storage for retrieval and relocation later

        :param element: The element itself that we want to save to storage, it can be a ` Selector ` or pure ` HtmlElement `
        :param identifier: This is the identifier that will be used to retrieve the element later from the storage. See
            the docs for more info.
        """
        if self.__adaptive_enabled and self._storage:
            target_element: Any = element
            if isinstance(target_element, self.__class__):
                target_element = target_element._root

            if self._is_text_node(target_element):
                target_element = target_element.getparent()

            self._storage.save(target_element, identifier)
        else:
            raise RuntimeError(
                "Can't use `adaptive` features while it's disabled globally, you have to start a new class instance."
            )

    def retrieve(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Using the identifier, we search the storage and return the unique properties of the element

        :param identifier: This is the identifier that will be used to retrieve the element from the storage. See
            the docs for more info.
        :return: A dictionary of the unique properties
        """
        if self.__adaptive_enabled and self._storage:
            return self._storage.retrieve(identifier)

        raise RuntimeError(
            "Can't use `adaptive` features while it's disabled globally, you have to start a new class instance."
        )

    # Operations on text functions
    def json(self) -> Dict:
        """Return JSON response if the response is jsonable otherwise throws error"""
        if self._is_text_node(self._root):
            return TextHandler(str(self._root)).json()
        if self._raw_body and isinstance(self._raw_body, (str, bytes)):
            if isinstance(self._raw_body, str):
                return TextHandler(self._raw_body).json()
            else:
                if TYPE_CHECKING:
                    assert isinstance(self._raw_body, bytes)
                return TextHandler(self._raw_body.decode()).json()
        elif self.text:
            return self.text.json()
        else:
            return self.get_all_text(strip=True).json()

    def re(
        self,
        regex: str | Pattern[str],
        replace_entities: bool = True,
        clean_match: bool = False,
        case_sensitive: bool = True,
    ) -> TextHandlers:
        """Apply the given regex to the current text and return a list of strings with the matches.

        :param regex: Can be either a compiled regular expression or a string.
        :param replace_entities: If enabled character entity references are replaced by their corresponding character
        :param clean_match: if enabled, this will ignore all whitespaces and consecutive spaces while matching
        :param case_sensitive: if disabled, the function will set the regex to ignore the letters case while compiling it
        """
        return self.text.re(regex, replace_entities, clean_match, case_sensitive)

    def re_first(
        self,
        regex: str | Pattern[str],
        default=None,
        replace_entities: bool = True,
        clean_match: bool = False,
        case_sensitive: bool = True,
    ) -> TextHandler:
        """Apply the given regex to text and return the first match if found, otherwise return the default value.

        :param regex: Can be either a compiled regular expression or a string.
        :param default: The default value to be returned if there is no match
        :param replace_entities: if enabled character entity references are replaced by their corresponding character
        :param clean_match: if enabled, this will ignore all whitespaces and consecutive spaces while matching
        :param case_sensitive: if disabled, the function will set the regex to ignore the letters case while compiling it
        """
        return self.text.re_first(regex, default, replace_entities, clean_match, case_sensitive)

    @staticmethod
    def __get_attributes(element: HtmlElement, ignore_attributes: List | Tuple) -> Dict:
        """Return attributes dictionary without the ignored list"""
        return {k: v for k, v in element.attrib.items() if k not in ignore_attributes}

    def __are_alike(
        self,
        original: HtmlElement,
        original_attributes: Dict,
        candidate: HtmlElement,
        ignore_attributes: List | Tuple,
        similarity_threshold: float,
        match_text: bool = False,
    ) -> bool:
        """Calculate a score of how much these elements are alike and return True
        if the score is higher or equals the threshold"""
        candidate_attributes = (
            self.__get_attributes(candidate, ignore_attributes) if ignore_attributes else candidate.attrib
        )
        score: float = 0
        checks: int = 0

        if original_attributes:
            score += sum(
                SequenceMatcher(None, v, candidate_attributes.get(k, "")).ratio()
                for k, v in original_attributes.items()
            )
            checks += len(candidate_attributes)
        else:
            if not candidate_attributes:
                # Both don't have attributes, this must mean something
                score += 1
                checks += 1

        if match_text:
            score += SequenceMatcher(
                None,
                clean_spaces(original.text or ""),
                clean_spaces(candidate.text or ""),
            ).ratio()
            checks += 1

        if checks:
            return round(score / checks, 2) >= similarity_threshold
        return False

    def find_similar(
        self,
        similarity_threshold: float = 0.2,
        ignore_attributes: List | Tuple = (
            "href",
            "src",
        ),
        match_text: bool = False,
    ) -> "Selectors":
        """Find elements that are in the same tree depth in the page with the same tag name and same parent tag etc...
        then return the ones that match the current element attributes with a percentage higher than the input threshold.

        This function is inspired by AutoScraper and made for cases where you, for example, found a product div inside
        a products-list container and want to find other products using that element as a starting point EXCEPT
        this function works in any case without depending on the element type.

        :param similarity_threshold: The percentage to use while comparing element attributes.
            Note: Elements found before attributes matching/comparison will be sharing the same depth, same tag name,
            same parent tag name, and same grand parent tag name. So they are 99% likely to be correct unless you are
            extremely unlucky, then attributes matching comes into play, so don't play with this number unless
            you are getting the results you don't want.
            Also, if the current element doesn't have attributes and the similar element as well, then it's a 100% match.
        :param ignore_attributes: Attribute names passed will be ignored while matching the attributes in the last step.
            The default value is to ignore `href` and `src` as URLs can change a lot between elements, so it's unreliable
        :param match_text: If True, element text content will be taken into calculation while matching.
            Not recommended to use in normal cases, but it depends.

        :return: A ``Selectors`` container of ``Selector`` objects or empty list
        """
        if self._is_text_node(self._root):
            return Selectors()

        # We will use the elements' root from now on to get the speed boost of using Lxml directly
        root = self._root
        similar_elements = list()

        current_depth = len(list(root.iterancestors()))
        target_attrs = self.__get_attributes(root, ignore_attributes) if ignore_attributes else root.attrib

        path_parts = [self.tag]
        if (parent := root.getparent()) is not None:
            path_parts.insert(0, parent.tag)
            if (grandparent := parent.getparent()) is not None:
                path_parts.insert(0, grandparent.tag)

        xpath_path = "//{}".format("/".join(path_parts))
        potential_matches = root.xpath(f"{xpath_path}[count(ancestor::*) = {current_depth}]")

        for potential_match in potential_matches:
            if potential_match != root and self.__are_alike(
                root,
                target_attrs,
                potential_match,
                ignore_attributes,
                similarity_threshold,
                match_text,
            ):
                similar_elements.append(potential_match)

        return Selectors(map(self.__element_convertor, similar_elements))

    @overload
    def find_by_text(
        self,
        text: str,
        first_match: Literal[True] = ...,
        partial: bool = ...,
        case_sensitive: bool = ...,
        clean_match: bool = ...,
    ) -> "Selector": ...

    @overload
    def find_by_text(
        self,
        text: str,
        first_match: Literal[False],
        partial: bool = ...,
        case_sensitive: bool = ...,
        clean_match: bool = ...,
    ) -> "Selectors": ...

    def find_by_text(
        self,
        text: str,
        first_match: bool = True,
        partial: bool = False,
        case_sensitive: bool = False,
        clean_match: bool = True,
    ) -> Union["Selectors", "Selector"]:
        """Find elements that its text content fully/partially matches input.
        :param text: Text query to match
        :param first_match: Returns the first element that matches conditions, enabled by default
        :param partial: If enabled, the function returns elements that contain the input text
        :param case_sensitive: if enabled, the letters case will be taken into consideration
        :param clean_match: if enabled, this will ignore all whitespaces and consecutive spaces while matching
        """
        if self._is_text_node(self._root):
            return Selectors()

        results = Selectors()
        if not case_sensitive:
            text = text.lower()

        possible_targets = cast(List, _find_all_elements_with_spaces(self._root))
        if possible_targets:
            for node in self.__elements_convertor(possible_targets):
                """Check if element matches given text otherwise, traverse the children tree and iterate"""
                node_text: TextHandler = node.text
                if clean_match:
                    node_text = TextHandler(node_text.clean())

                if not case_sensitive:
                    node_text = TextHandler(node_text.lower())

                if partial:
                    if text in node_text:
                        results.append(node)
                elif text == node_text:
                    results.append(node)

                if first_match and results:
                    # we got an element so we should stop
                    break

            if first_match:
                if results:
                    return results[0]
        return results

    @overload
    def find_by_regex(
        self,
        query: str | Pattern[str],
        first_match: Literal[True] = ...,
        case_sensitive: bool = ...,
        clean_match: bool = ...,
    ) -> "Selector": ...

    @overload
    def find_by_regex(
        self,
        query: str | Pattern[str],
        first_match: Literal[False],
        case_sensitive: bool = ...,
        clean_match: bool = ...,
    ) -> "Selectors": ...

    def find_by_regex(
        self,
        query: str | Pattern[str],
        first_match: bool = True,
        case_sensitive: bool = False,
        clean_match: bool = True,
    ) -> Union["Selectors", "Selector"]:
        """Find elements that its text content matches the input regex pattern.
        :param query: Regex query/pattern to match
        :param first_match: Return the first element that matches conditions; enabled by default.
        :param case_sensitive: If enabled, the letters case will be taken into consideration in the regex.
        :param clean_match: If enabled, this will ignore all whitespaces and consecutive spaces while matching.
        """
        if self._is_text_node(self._root):
            return Selectors()

        results = Selectors()

        possible_targets = cast(List, _find_all_elements_with_spaces(self._root))
        if possible_targets:
            for node in self.__elements_convertor(possible_targets):
                """Check if element matches given regex otherwise, traverse the children tree and iterate"""
                node_text = node.text
                if node_text.re(
                    query,
                    check_match=True,
                    clean_match=clean_match,
                    case_sensitive=case_sensitive,
                ):
                    results.append(node)

                if first_match and results:
                    # we got an element so we should stop
                    break

            if results and first_match:
                return results[0]
        return results

    # ── Pagination Auto-Detection ──────────────────────────────────────
    # Common text patterns that indicate "next page" links across many languages
    _NEXT_PAGE_TEXT_PATTERNS: Tuple[str, ...] = (
        "next", "next page", "next »", "next ›", "next →",
        "»", "›", "→", ">>", "▸", "▶",
        # Portuguese
        "próxima", "próximo", "seguinte", "próxima página",
        # Spanish
        "siguiente", "siguiente página",
        # French
        "suivant", "suivante", "page suivante",
        # German
        "weiter", "nächste", "nächste seite",
        # Italian
        "successivo", "prossimo", "pagina successiva",
        # Japanese / Chinese
        "次へ", "次のページ", "下一页", "下一頁",
        # Arabic
        "التالي", "الصفحة التالية",
    )

    # CSS class/id substrings that commonly indicate pagination "next" links
    _NEXT_PAGE_CSS_SIGNALS: Tuple[str, ...] = (
        "next", "pagination-next", "page-next", "pager-next",
        "nav-next", "arrow-next", "btn-next",
    )

    # URL patterns that suggest pagination parameters
    _PAGINATION_URL_PATTERNS: Tuple[str, ...] = (
        "page=", "/page/", "p=", "offset=", "start=",
        "pg=", "pagina=", "pag=",
    )

    def detect_next_page(self) -> Optional["Selector"]:
        """Automatically detect the "Next Page" link in the current page.

        Uses a multi-signal heuristic approach:
        1. First checks for ``<link rel="next">`` (highest priority — set by site authors).
        2. Scans all ``<a>`` tags and scores them based on:
           - Text content matching common "next" patterns (multilingual).
           - CSS class/id matching common pagination class names.
           - ``rel="next"`` attribute on ``<a>`` tags.
           - ``href`` containing URL pagination parameters.
        3. Returns the highest-scoring candidate as a ``Selector``, or ``None`` if no
           pagination link is found.

        :return: A ``Selector`` wrapping the best "next page" ``<a>`` element, or ``None``.

        Usage::

            page = Fetcher.get('https://example.com/products?page=1')
            next_link = page.detect_next_page()
            if next_link:
                next_url = page.urljoin(next_link.attrib['href'])
        """
        if self._is_text_node(self._root):
            return None

        # ── Signal 1: <link rel="next" href="..."> — highest confidence ──
        link_next = self._root.xpath('//link[@rel="next" and @href]')
        if link_next:
            return self.__element_convertor(link_next[0])

        # ── Signal 2: Score all <a> tags ──
        all_anchors = self._root.xpath("//a[@href]")
        if not all_anchors:
            return None

        best_score: float = 0.0
        best_element: Optional[HtmlElement] = None
        min_threshold: float = 1.0  # Minimum score to accept a candidate

        for anchor in all_anchors:
            score: float = 0.0
            href = str(anchor.get("href", ""))

            # Skip empty, javascript, and anchor-only links
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue

            # ── Text signal ──
            # Get combined text content (including children like <span>Next</span>)
            anchor_text = (anchor.text_content() or "").strip().lower()
            if anchor_text:
                for pattern in self._NEXT_PAGE_TEXT_PATTERNS:
                    if pattern in anchor_text:
                        # Exact single-symbol matches (», ›, →) get higher score
                        if anchor_text == pattern:
                            score += 5.0
                        else:
                            score += 3.0
                        break

            # ── rel="next" attribute on <a> ──
            rel = str(anchor.get("rel", "")).lower()
            if "next" in rel:
                score += 6.0

            # ── aria-label signal ──
            aria = str(anchor.get("aria-label", "")).lower()
            if aria:
                for pattern in self._NEXT_PAGE_TEXT_PATTERNS:
                    if pattern in aria:
                        score += 4.0
                        break

            # ── title attribute signal ──
            title = str(anchor.get("title", "")).lower()
            if title:
                for pattern in self._NEXT_PAGE_TEXT_PATTERNS:
                    if pattern in title:
                        score += 2.0
                        break

            # ── Class / ID signal ──
            classes = str(anchor.get("class", "")).lower()
            el_id = str(anchor.get("id", "")).lower()
            # Also check parent element classes (common: <li class="next"><a>...)
            parent = anchor.getparent()
            parent_classes = str(parent.get("class", "")).lower() if parent is not None else ""
            parent_id = str(parent.get("id", "")).lower() if parent is not None else ""
            combined_css = f"{classes} {el_id} {parent_classes} {parent_id}"

            for signal in self._NEXT_PAGE_CSS_SIGNALS:
                if signal in combined_css:
                    score += 3.0
                    break

            # Negative signal: "prev", "previous", "back", "disabled"
            if any(neg in combined_css for neg in ("prev", "previous", "back", "disabled", "anterior")):
                score -= 10.0

            if any(neg in anchor_text for neg in ("prev", "previous", "back", "anterior")):
                score -= 10.0

            # ── URL pattern signal ──
            href_lower = href.lower()
            for url_pat in self._PAGINATION_URL_PATTERNS:
                if url_pat in href_lower:
                    score += 1.0
                    break

            # ── Pick the best candidate ──
            if score > best_score:
                best_score = score
                best_element = anchor

        if best_element is not None and best_score >= min_threshold:
            return self.__element_convertor(best_element)

        return None

    # ── Schema Auto-Detection ──────────────────────────────────────────

    def get_schemas(self) -> Dict[str, List]:
        """Auto-detect and extract structured data schemas from the page.

        Extracts three types of structured data:

        1. **JSON-LD**: ``<script type="application/ld+json">`` tags, parsed into dicts.
        2. **Microdata**: Elements with ``itemscope``/``itemprop`` attributes, extracted
           into nested dictionaries with ``@type`` and property values.
        3. **RDFa**: Elements with ``vocab``/``typeof``/``property`` attributes, extracted
           similarly.

        :return: A dictionary with three keys: ``json_ld``, ``microdata``, and ``rdfa``,
                 each containing a list of the extracted schema objects.

        Usage::

            page = Fetcher.get('https://example.com/product')
            schemas = page.get_schemas()
            for item in schemas['json_ld']:
                print(item.get('@type'), item.get('name'))
        """
        if self._is_text_node(self._root):
            return {"json_ld": [], "microdata": [], "rdfa": []}

        result: Dict[str, List] = {"json_ld": [], "microdata": [], "rdfa": []}

        # ── 1. JSON-LD ──
        try:
            import orjson
            _json_loads = orjson.loads
        except ImportError:
            import json
            _json_loads = json.loads

        for script in self._root.xpath('//script[@type="application/ld+json"]'):
            raw_text = script.text_content()
            if raw_text and raw_text.strip():
                try:
                    data = _json_loads(raw_text.strip())
                    if isinstance(data, list):
                        result["json_ld"].extend(data)
                    else:
                        result["json_ld"].append(data)
                except (ValueError, TypeError):
                    pass  # Malformed JSON-LD, skip silently

        # ── 2. Microdata ──
        # Find all top-level itemscope elements (not nested inside another itemscope)
        for item_el in self._root.xpath("//*[@itemscope]"):
            item_data = self.__extract_microdata_item(item_el)
            if item_data:
                result["microdata"].append(item_data)

        # ── 3. RDFa ──
        for rdfa_el in self._root.xpath("//*[@vocab or @typeof]"):
            rdfa_data = self.__extract_rdfa_item(rdfa_el)
            if rdfa_data:
                result["rdfa"].append(rdfa_data)

        return result

    def __extract_microdata_item(self, element: HtmlElement) -> Dict[str, Any]:
        """Extract a single Microdata item (itemscope) into a dictionary.

        :param element: An lxml HtmlElement with ``itemscope`` attribute.
        :return: A dictionary with ``@type``, ``@id`` and property values.
        """
        item: Dict[str, Any] = {}

        item_type = element.get("itemtype", "")
        if item_type:
            item["@type"] = item_type

        item_id = element.get("itemid", "")
        if item_id:
            item["@id"] = item_id

        # Collect itemprop elements that are direct descendants (not inside nested itemscopes)
        for prop_el in element.xpath(".//*[@itemprop]"):
            # Skip if this prop_el belongs to a nested itemscope
            ancestors_with_scope = prop_el.xpath("ancestor::*[@itemscope]")
            if len(ancestors_with_scope) > 1 and ancestors_with_scope[0] != element:
                continue

            prop_name = prop_el.get("itemprop", "")
            if not prop_name:
                continue

            # Determine the value
            if prop_el.get("itemscope") is not None:
                # Nested item
                value = self.__extract_microdata_item(prop_el)
            elif prop_el.tag in ("meta",):
                value = prop_el.get("content", "")
            elif prop_el.tag in ("a", "area", "link"):
                value = prop_el.get("href", "")
            elif prop_el.tag in ("img", "video", "audio", "source", "embed"):
                value = prop_el.get("src", "")
            elif prop_el.tag in ("time",):
                value = prop_el.get("datetime", prop_el.text_content() or "")
            elif prop_el.tag in ("data", "meter"):
                value = prop_el.get("value", prop_el.text_content() or "")
            else:
                value = (prop_el.text_content() or "").strip()

            # Handle multiple values for the same property
            if prop_name in item:
                existing = item[prop_name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    item[prop_name] = [existing, value]
            else:
                item[prop_name] = value

        return item

    def __extract_rdfa_item(self, element: HtmlElement) -> Dict[str, Any]:
        """Extract a single RDFa item into a dictionary.

        :param element: An lxml HtmlElement with ``vocab`` or ``typeof`` attribute.
        :return: A dictionary with ``@vocab``, ``@typeof`` and property values.
        """
        item: Dict[str, Any] = {}

        vocab = element.get("vocab", "")
        if vocab:
            item["@vocab"] = vocab

        typeof = element.get("typeof", "")
        if typeof:
            item["@typeof"] = typeof

        resource = element.get("resource", "")
        if resource:
            item["@resource"] = resource

        for prop_el in element.xpath(".//*[@property]"):
            prop_name = prop_el.get("property", "")
            if not prop_name:
                continue

            # Get value from content attribute or text
            if prop_el.get("content") is not None:
                value = prop_el.get("content", "")
            elif prop_el.tag in ("a", "link"):
                value = prop_el.get("href", "")
            elif prop_el.tag in ("img",):
                value = prop_el.get("src", "")
            elif prop_el.tag in ("time",):
                value = prop_el.get("datetime", prop_el.text_content() or "")
            else:
                value = (prop_el.text_content() or "").strip()

            if prop_name in item:
                existing = item[prop_name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    item[prop_name] = [existing, value]
            else:
                item[prop_name] = value

        return item

    # ── Meta Analyzer ──────────────────────────────────────────────────

    def analyze(self) -> Dict[str, Any]:
        """Analyze the page's meta-elements and return a structured profile.

        Extracts:
        - ``title``: The page ``<title>`` text.
        - ``description``: Content of ``<meta name="description">``.
        - ``keywords``: Content of ``<meta name="keywords">`` (as a list).
        - ``canonical``: The canonical URL from ``<link rel="canonical">``.
        - ``language``: The page language from ``<html lang="...">``.
        - ``opengraph``: Dict of all ``og:*`` meta properties.
        - ``twitter``: Dict of all ``twitter:*`` meta properties.
        - ``author``: Content of ``<meta name="author">``.
        - ``robots``: Content of ``<meta name="robots">``.
        - ``generator``: Content of ``<meta name="generator">``.
        - ``favicon``: URL of the favicon (``<link rel="icon">``).
        - ``feeds``: List of RSS/Atom feed URLs.
        - ``charset``: Character encoding declared in meta.
        - ``viewport``: Viewport meta content.

        :return: A dictionary with the extracted metadata.

        Usage::

            page = Fetcher.get('https://example.com')
            meta = page.analyze()
            print(meta['title'], meta['opengraph'].get('og:image'))
        """
        if self._is_text_node(self._root):
            return {}

        result: Dict[str, Any] = {
            "title": None,
            "description": None,
            "keywords": [],
            "canonical": None,
            "language": None,
            "opengraph": {},
            "twitter": {},
            "author": None,
            "robots": None,
            "generator": None,
            "favicon": None,
            "feeds": [],
            "charset": None,
            "viewport": None,
        }

        # ── Title ──
        title_els = self._root.xpath("//title")
        if title_els:
            result["title"] = (title_els[0].text_content() or "").strip()

        # ── HTML lang ──
        html_els = self._root.xpath("//html[@lang]")
        if html_els:
            result["language"] = html_els[0].get("lang", "").strip()

        # ── Charset ──
        charset_els = self._root.xpath("//meta[@charset]")
        if charset_els:
            result["charset"] = charset_els[0].get("charset", "").strip()
        else:
            # Fallback: <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
            ct_els = self._root.xpath('//meta[@http-equiv="Content-Type"]')
            if ct_els:
                content = ct_els[0].get("content", "")
                if "charset=" in content:
                    result["charset"] = content.split("charset=")[-1].strip()

        # ── Canonical URL ──
        canonical_els = self._root.xpath('//link[@rel="canonical" and @href]')
        if canonical_els:
            result["canonical"] = canonical_els[0].get("href", "").strip()

        # ── Favicon ──
        for rel_val in ("icon", "shortcut icon"):
            favicon_els = self._root.xpath(f'//link[contains(@rel, "{rel_val}") and @href]')
            if favicon_els:
                result["favicon"] = favicon_els[0].get("href", "").strip()
                break

        # ── RSS/Atom feeds ──
        feed_els = self._root.xpath(
            '//link[@type="application/rss+xml" or @type="application/atom+xml"]'
        )
        for feed in feed_els:
            feed_info = {
                "title": feed.get("title", ""),
                "href": feed.get("href", ""),
                "type": feed.get("type", ""),
            }
            result["feeds"].append(feed_info)

        # ── Standard meta tags ──
        _name_meta_map = {
            "description": "description",
            "keywords": "keywords",
            "author": "author",
            "robots": "robots",
            "generator": "generator",
            "viewport": "viewport",
        }
        for meta_el in self._root.xpath("//meta[@name and @content]"):
            name = (meta_el.get("name", "") or "").lower().strip()
            content = (meta_el.get("content", "") or "").strip()
            if name in _name_meta_map and content:
                key = _name_meta_map[name]
                if key == "keywords":
                    result["keywords"] = [k.strip() for k in content.split(",") if k.strip()]
                else:
                    result[key] = content

        # ── OpenGraph (og:*) ──
        for meta_el in self._root.xpath('//meta[starts-with(@property, "og:")]'):
            prop = meta_el.get("property", "").strip()
            content = (meta_el.get("content", "") or "").strip()
            if prop and content:
                result["opengraph"][prop] = content

        # ── Twitter Cards (twitter:*) ──
        for meta_el in self._root.xpath(
            '//meta[starts-with(@name, "twitter:") or starts-with(@property, "twitter:")]'
        ):
            prop = (meta_el.get("name", "") or meta_el.get("property", "") or "").strip()
            content = (meta_el.get("content", "") or "").strip()
            if prop and content:
                result["twitter"][prop] = content

        return result



class Selectors(List[Selector]):
    """
    The `Selectors` class is a subclass of the builtin ``List`` class, which provides a few additional methods.
    """

    __slots__ = ()

    @overload
    def __getitem__(self, pos: SupportsIndex) -> Selector:
        pass

    @overload
    def __getitem__(self, pos: slice) -> "Selectors":
        pass

    def __getitem__(self, pos: SupportsIndex | slice) -> Union[Selector, "Selectors"]:
        lst = super().__getitem__(pos)
        if isinstance(pos, slice):
            return self.__class__(cast(List[Selector], lst))
        else:
            return cast(Selector, lst)

    def xpath(
        self,
        selector: str,
        identifier: str = "",
        auto_save: bool = False,
        percentage: int = 0,
        **kwargs: Any,
    ) -> "Selectors":
        """
        Call the ``.xpath()`` method for each element in this list and return
        their results as another `Selectors` class.

        **Important:
        It's recommended to use the identifier argument if you plan to use a different selector later
        and want to relocate the same element(s)**

         Note: **Additional keyword arguments will be passed as XPath variables in the XPath expression!**

        :param selector: The XPath selector to be used.
        :param identifier: A string that will be used to retrieve element's data in adaptive,
         otherwise the selector will be used.
        :param auto_save: Automatically save new elements for `adaptive` later
        :param percentage: The minimum percentage to accept while `adaptive` is working and not going lower than that.
         Be aware that the percentage calculation depends solely on the page structure, so don't play with this
         number unless you must know what you are doing!

        :return: `Selectors` class.
        """
        results = [n.xpath(selector, identifier or selector, False, auto_save, percentage, **kwargs) for n in self]
        return self.__class__(flatten(results))

    def css(
        self,
        selector: str,
        identifier: str = "",
        auto_save: bool = False,
        percentage: int = 0,
    ) -> "Selectors":
        """
        Call the ``.css()`` method for each element in this list and return
        their results flattened as another `Selectors` class.

        **Important:
        It's recommended to use the identifier argument if you plan to use a different selector later
        and want to relocate the same element(s)**

        :param selector: The CSS3 selector to be used.
        :param identifier: A string that will be used to retrieve element's data in adaptive,
         otherwise the selector will be used.
        :param auto_save: Automatically save new elements for `adaptive` later
        :param percentage: The minimum percentage to accept while `adaptive` is working and not going lower than that.
         Be aware that the percentage calculation depends solely on the page structure, so don't play with this
         number unless you must know what you are doing!

        :return: `Selectors` class.
        """
        results = [n.css(selector, identifier or selector, False, auto_save, percentage) for n in self]
        return self.__class__(flatten(results))

    def re(
        self,
        regex: str | Pattern,
        replace_entities: bool = True,
        clean_match: bool = False,
        case_sensitive: bool = True,
    ) -> TextHandlers:
        """Call the ``.re()`` method for each element in this list and return
        their results flattened as List of TextHandler.

        :param regex: Can be either a compiled regular expression or a string.
        :param replace_entities: If enabled character entity references are replaced by their corresponding character
        :param clean_match: if enabled, this will ignore all whitespaces and consecutive spaces while matching
        :param case_sensitive: if disabled, the function will set the regex to ignore the letters case while compiling it
        """
        results = [n.re(regex, replace_entities, clean_match, case_sensitive) for n in self]
        return TextHandlers(flatten(results))

    def re_first(
        self,
        regex: str | Pattern,
        default: Any = None,
        replace_entities: bool = True,
        clean_match: bool = False,
        case_sensitive: bool = True,
    ) -> TextHandler:
        """Call the ``.re_first()`` method for each element in this list and return
        the first result or the default value otherwise.

        :param regex: Can be either a compiled regular expression or a string.
        :param default: The default value to be returned if there is no match
        :param replace_entities: if enabled character entity references are replaced by their corresponding character
        :param clean_match: if enabled, this will ignore all whitespaces and consecutive spaces while matching
        :param case_sensitive: if disabled, function will set the regex to ignore the letters case while compiling it
        """
        for n in self:
            for result in n.re(regex, replace_entities, clean_match, case_sensitive):
                return result
        return default

    def search(self, func: Callable[["Selector"], bool]) -> Optional["Selector"]:
        """Loop over all current elements and return the first element that matches the passed function
        :param func: A function that takes each element as an argument and returns True/False
        :return: The first element that match the function or ``None`` otherwise.
        """
        for element in self:
            if func(element):
                return element
        return None

    def filter(self, func: Callable[["Selector"], bool]) -> "Selectors":
        """Filter current elements based on the passed function
        :param func: A function that takes each element as an argument and returns True/False
        :return: The new `Selectors` object or empty list otherwise.
        """
        return self.__class__([element for element in self if func(element)])

    @overload
    def get(self) -> Optional[TextHandler]: ...

    @overload
    def get(self, default: _T) -> Union[TextHandler, _T]: ...

    def get(self, default=None):
        """Returns the serialized string of the first element, or ``default`` if empty.
        :param default: the default value to return if the current list is empty
        """
        for x in self:
            return x.get()
        return default

    def getall(self) -> TextHandlers:
        """Serialize all elements and return as a TextHandlers list."""
        return TextHandlers([x.get() for x in self])

    extract = getall
    extract_first = get

    @property
    def first(self) -> Optional[Selector]:
        """Returns the first Selector item of the current list or `None` if the list is empty"""
        return self[0] if len(self) > 0 else None

    @property
    def last(self) -> Optional[Selector]:
        """Returns the last Selector item of the current list or `None` if the list is empty"""
        return self[-1] if len(self) > 0 else None

    @property
    def length(self) -> int:
        """Returns the length of the current list"""
        return len(self)

    # ── Regex Generation ───────────────────────────────────────────────

    def generate_regex(
        self,
        attribute: str = "href",
        use_text: bool = False,
    ) -> Optional[str]:
        """Generate a regex pattern from a group of elements.

        Analyzes the given attribute (or text content) of all elements in this
        list and produces a regular expression that matches all of them by
        finding common prefixes, suffixes, and variable segments.

        Useful for dynamic content extraction where the structure changes but the
        URL/text pattern remains similar.

        :param attribute: The attribute to extract values from (default: ``href``).
        :param use_text: If True, use the text content of elements instead of an attribute.
        :return: A regex pattern string, or ``None`` if the list has fewer than 2 elements.

        Usage::

            links = page.css('div.products a')
            pattern = links.generate_regex(attribute='href')
            # e.g. r'/products/\\d+/[^/]+'
        """
        import re
        from os.path import commonprefix

        # Collect values
        values: list[str] = []
        for el in self:
            if use_text:
                val = str(el.text or "").strip()
            else:
                val = str(el.attrib.get(attribute, "")).strip()
            if val:
                values.append(val)

        if len(values) < 2:
            return None

        # ── Find common prefix ──
        prefix = commonprefix(values)

        # ── Find common suffix ──
        reversed_values = [v[::-1] for v in values]
        suffix = commonprefix(reversed_values)[::-1]

        # Avoid prefix/suffix overlap
        if prefix and suffix and len(prefix) + len(suffix) > len(values[0]):
            suffix = ""

        # ── Analyze the variable parts ──
        variable_parts: list[str] = []
        for v in values:
            start = len(prefix)
            end = len(v) - len(suffix) if suffix else len(v)
            variable_parts.append(v[start:end])

        # ── Detect the type of variable content ──
        all_numeric = all(p.isdigit() for p in variable_parts if p)
        all_have_slashes = all("/" in p for p in variable_parts if p)

        if not any(variable_parts):
            # All values are identical
            return re.escape(values[0])

        # Build the regex for the variable segment
        if all_numeric:
            var_pattern = r"\d+"
        elif all_have_slashes:
            # Multi-segment paths: replace each segment
            max_segments = max(len(p.split("/")) for p in variable_parts)
            segments = []
            for i in range(max_segments):
                seg_values = set()
                for p in variable_parts:
                    parts = p.split("/")
                    if i < len(parts):
                        seg_values.add(parts[i])

                if all(s.isdigit() for s in seg_values if s):
                    segments.append(r"\d+")
                elif len(seg_values) <= 3 and all(s for s in seg_values):
                    # Few distinct values — use alternation
                    segments.append("(?:" + "|".join(re.escape(s) for s in sorted(seg_values)) + ")")
                else:
                    segments.append("[^/]+")
            var_pattern = "/".join(segments)
        else:
            # Check if variable parts share a sub-pattern
            if len(set(len(p) for p in variable_parts)) == 1 and len(variable_parts[0]) <= 4:
                # Fixed-length short strings (like codes)
                var_pattern = ".{" + str(len(variable_parts[0])) + "}"
            elif all(re.match(r'^[a-zA-Z0-9_-]+$', p) for p in variable_parts if p):
                var_pattern = r"[a-zA-Z0-9_-]+"
            else:
                var_pattern = ".+?"

        # ── Assemble the final regex ──
        pattern = re.escape(prefix) + "(" + var_pattern + ")" + re.escape(suffix)

        return pattern

    def __getstate__(self) -> Any:  # pragma: no cover
        # lxml don't like it :)
        raise TypeError("Can't pickle Selectors object")


# For backward compatibility
Adaptor = Selector
Adaptors = Selectors

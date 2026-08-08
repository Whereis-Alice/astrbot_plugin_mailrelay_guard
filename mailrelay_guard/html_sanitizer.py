"""Conservative HTML email preparation for MailRelay Guard."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import nh3
import tinycss2

from .config import MailRelaySettings
from .policy import MailRelayValidationError

_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_DROP_WITH_CONTENT_TAGS = frozenset(
    {
        "base",
        "button",
        "embed",
        "form",
        "head",
        "iframe",
        "input",
        "link",
        "math",
        "meta",
        "noscript",
        "object",
        "plaintext",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
        "title",
        "xmp",
    }
)
_STRICT_CSS_PROPERTIES = frozenset(
    {
        "background-color",
        "border",
        "border-bottom",
        "border-bottom-color",
        "border-bottom-style",
        "border-bottom-width",
        "border-color",
        "border-left",
        "border-left-color",
        "border-left-style",
        "border-left-width",
        "border-radius",
        "border-right",
        "border-right-color",
        "border-right-style",
        "border-right-width",
        "border-spacing",
        "border-style",
        "border-top",
        "border-top-color",
        "border-top-style",
        "border-top-width",
        "border-width",
        "box-shadow",
        "color",
        "display",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "letter-spacing",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "opacity",
        "overflow-wrap",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "table-layout",
        "text-align",
        "text-decoration",
        "text-shadow",
        "text-transform",
        "vertical-align",
        "white-space",
        "width",
        "word-break",
    }
)
_RELAXED_CSS_PROPERTIES = _STRICT_CSS_PROPERTIES | frozenset(
    {
        "clear",
        "float",
        "outline",
        "outline-color",
        "outline-style",
        "outline-width",
        "overflow",
        "text-indent",
        "text-overflow",
    }
)
_SAFE_CSS_FUNCTIONS = frozenset(
    {
        "calc",
        "clamp",
        "hsl",
        "hsla",
        "max",
        "min",
        "rgb",
        "rgba",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


@dataclass(frozen=True)
class PreparedHtmlMail:
    """The SMTP-ready HTML part and its text-only fallback."""

    html_body: str
    plain_body: str


def prepare_html_mail(settings: MailRelaySettings, html_body: str) -> PreparedHtmlMail:
    """Validate, sanitize, and derive a text fallback from an HTML fragment."""

    if not settings.enable_html_mail:
        raise MailRelayValidationError("管理员尚未开启 HTML 邮件功能。")

    raw_html = str(html_body or "").strip()
    if not raw_html:
        raise MailRelayValidationError("HTML 邮件正文不能为空。")
    if len(raw_html) > settings.max_html_body_chars:
        raise MailRelayValidationError(
            f"HTML 邮件源码不能超过 {settings.max_html_body_chars} 个字符。"
        )

    css_properties = _css_properties(settings)
    attributes = _allowed_attributes(settings)
    tags = set(_ALLOWED_TAGS)
    if settings.html_allow_remote_images and settings.html_remote_image_allowed_domains:
        tags.add("img")
        attributes["img"] = {"alt", "height", "src", "style", "width"}

    cleaner = nh3.Cleaner(
        tags=tags,
        clean_content_tags=set(_DROP_WITH_CONTENT_TAGS),
        attributes=attributes,
        attribute_filter=_attribute_filter(settings, css_properties),
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
        url_schemes={"https"},
        url_relative="deny",
        filter_style_properties=css_properties,
    )
    sanitized_html = cleaner.clean(raw_html).strip()
    if not sanitized_html:
        raise MailRelayValidationError("HTML 邮件在清洗后为空，已拒绝发送。")
    if len(sanitized_html) > settings.max_html_body_chars:
        raise MailRelayValidationError(
            f"清洗后的 HTML 邮件不能超过 {settings.max_html_body_chars} 个字符。"
        )

    plain_body = _html_to_plain_text(sanitized_html)
    if not plain_body:
        raise MailRelayValidationError(
            "HTML 邮件在清洗后没有可用文字，无法生成纯文本备用内容。"
        )
    return PreparedHtmlMail(html_body=sanitized_html, plain_body=plain_body)


def _allowed_attributes(settings: MailRelaySettings) -> dict[str, set[str]]:
    attributes: dict[str, set[str]] = {
        "*": {"dir", "lang", "style", "title"},
        "table": {
            "align",
            "bgcolor",
            "border",
            "cellpadding",
            "cellspacing",
            "height",
            "role",
            "valign",
            "width",
        },
        "tbody": {"align", "valign"},
        "td": {"align", "bgcolor", "colspan", "height", "rowspan", "valign", "width"},
        "tfoot": {"align", "valign"},
        "th": {"align", "bgcolor", "colspan", "height", "rowspan", "valign", "width"},
        "thead": {"align", "valign"},
        "tr": {"align", "bgcolor", "height", "valign"},
    }
    if settings.html_allow_links:
        attributes["a"] = {"href"}
    return attributes


def _attribute_filter(settings: MailRelaySettings, css_properties: frozenset[str]):
    def filter_attribute(tag: str, attribute: str, value: str) -> str | None:
        try:
            if attribute == "style":
                return _sanitize_inline_style(value, css_properties)
            if tag == "a" and attribute == "href":
                return _sanitize_https_url(value)
            if tag == "img" and attribute == "src":
                return _sanitize_https_url(
                    value,
                    allowed_hosts=settings.html_remote_image_allowed_domains,
                )
            return value
        except Exception:  # noqa: BLE001
            # nh3 preserves an attribute if its callback raises, so fail closed here.
            return None

    return filter_attribute


def _css_properties(settings: MailRelaySettings) -> frozenset[str]:
    if settings.sanitize_html_before_send:
        return _STRICT_CSS_PROPERTIES
    return _RELAXED_CSS_PROPERTIES


def _sanitize_inline_style(value: str, allowed_properties: frozenset[str]) -> str | None:
    declarations = tinycss2.parse_declaration_list(
        str(value or ""),
        skip_comments=True,
        skip_whitespace=True,
    )
    cleaned: list[str] = []
    for declaration in declarations:
        if getattr(declaration, "type", "") != "declaration":
            continue
        property_name = getattr(declaration, "lower_name", "")
        if property_name not in allowed_properties:
            continue
        value_tokens = getattr(declaration, "value", ())
        if _contains_unsafe_css_tokens(value_tokens):
            continue
        serialized_value = tinycss2.serialize(value_tokens).strip()
        if not serialized_value:
            continue
        important = " !important" if getattr(declaration, "important", False) else ""
        cleaned.append(f"{property_name}:{serialized_value}{important}")
    return ";".join(cleaned) or None


def _contains_unsafe_css_tokens(tokens: object) -> bool:
    for token in tokens if isinstance(tokens, (list, tuple)) else ():
        token_type = getattr(token, "type", "")
        if token_type in {"at-keyword", "bad-url", "error", "url"}:
            return True
        if token_type == "function":
            function_name = getattr(token, "lower_name", "").casefold()
            if function_name not in _SAFE_CSS_FUNCTIONS:
                return True
            if _contains_unsafe_css_tokens(getattr(token, "arguments", ())):
                return True
            continue
        content = getattr(token, "content", None)
        if content is not None and _contains_unsafe_css_tokens(content):
            return True
    return False


def _sanitize_https_url(
    value: str, *, allowed_hosts: frozenset[str] | None = None
) -> str | None:
    parsed = urlsplit(str(value or "").strip())
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    if allowed_hosts is not None and hostname not in allowed_hosts:
        return None
    return value.strip()


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._append_break()
        if tag == "li":
            self.parts.append("- ")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._append_break()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def _append_break(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line).strip()


def _html_to_plain_text(html_body: str) -> str:
    extractor = _PlainTextExtractor()
    extractor.feed(html_body)
    extractor.close()
    return extractor.text()

from __future__ import annotations

import unittest

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.html_sanitizer import (
    prepare_html_mail,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.policy import (
    MailRelayValidationError,
)


def html_settings(**overrides):
    config = {"enable_html_mail": True}
    config.update(overrides)
    return load_settings(config)


class HtmlSanitizerTests(unittest.TestCase):
    def test_strict_cleaning_removes_active_content_and_keeps_safe_style(self) -> None:
        prepared = prepare_html_mail(
            html_settings(html_allow_links=True),
            """
            <div onclick="steal()" style="color:#ff00aa; padding:12px; width:calc(100% - 20px); background-image:url(https://tracker.example/pixel)">
              <script>do-not-keep-this</script>
              <form>do-not-keep-form</form>
              <iframe>do-not-keep-frame</iframe>
              <a href="javascript:alert(1)">不能跳转</a>
              <p>保留文本</p>
            </div>
            """,
        )

        self.assertIn("color:#ff00aa", prepared.html_body)
        self.assertIn("padding:12px", prepared.html_body)
        self.assertIn("width:calc(100% - 20px)", prepared.html_body)
        for forbidden in (
            "background-image",
            "do-not-keep-this",
            "do-not-keep-form",
            "do-not-keep-frame",
            "iframe",
            "javascript:",
            "onclick",
            "script",
        ):
            self.assertNotIn(forbidden, prepared.html_body)
            self.assertNotIn(forbidden, prepared.plain_body)
        self.assertIn("不能跳转", prepared.plain_body)
        self.assertIn("保留文本", prepared.plain_body)

    def test_links_and_remote_images_require_explicit_configuration(self) -> None:
        prepared = prepare_html_mail(
            html_settings(
                html_allow_links=True,
                html_allow_remote_images=True,
                html_remote_image_allowed_domains=["images.example.com"],
            ),
            """
            <a href="https://safe.example/path">安全链接</a>
            <a href="/relative">相对链接</a>
            <img src="https://images.example.com/card.png" alt="许可图片">
            <img src="https://images.example.com.evil/card.png" alt="伪造域名">
            """,
        )

        self.assertIn('href="https://safe.example/path"', prepared.html_body)
        self.assertNotIn('href="/relative"', prepared.html_body)
        self.assertIn('src="https://images.example.com/card.png"', prepared.html_body)
        self.assertNotIn("images.example.com.evil", prepared.html_body)

    def test_relaxed_mode_still_blocks_css_url_and_active_css_functions(self) -> None:
        strict = prepare_html_mail(
            html_settings(),
            '<span style="outline:1px solid #f0c; padding:5px">内容</span>',
        )
        relaxed = prepare_html_mail(
            html_settings(sanitize_html_before_send=False),
            """
            <span style="outline:1px solid #f0c; padding:5px; color:expression(alert(1)); background-color:u\\72l(https://tracker.example/pixel); text-shadow:var(--tone)">内容</span>
            """,
        )

        self.assertNotIn("outline", strict.html_body)
        self.assertIn("outline:1px solid #f0c", relaxed.html_body)
        self.assertIn("padding:5px", relaxed.html_body)
        for forbidden in ("expression", "tracker.example", "url", "var("):
            self.assertNotIn(forbidden, relaxed.html_body)

    def test_cleaned_html_must_have_text_and_stay_within_limit(self) -> None:
        with self.assertRaisesRegex(MailRelayValidationError, "清洗后为空"):
            prepare_html_mail(html_settings(), "<script>only script</script>")
        with self.assertRaisesRegex(MailRelayValidationError, "不能超过"):
            prepare_html_mail(html_settings(max_html_body_chars=8), "<p>太长了</p>")

    def test_plain_fallback_is_derived_from_cleaned_html(self) -> None:
        prepared = prepare_html_mail(
            html_settings(),
            "<p>安全内容</p><script>绝不能进入备用正文</script><p>第二段</p>",
        )

        self.assertEqual(prepared.plain_body, "安全内容\n第二段")

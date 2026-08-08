"""Non-blocking SMTP delivery backed by Python's standard library."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid
from typing import Any

from .config import MailRelaySettings

LOGGER = logging.getLogger(__name__)


class MailRelayTransportError(RuntimeError):
    """A safe, user-facing SMTP transport error."""


@dataclass(frozen=True)
class DeliveryResult:
    """SMTP delivery outcome. Partial rejection is represented explicitly."""

    message_id: str
    accepted_recipients: tuple[str, ...]
    refused_recipients: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.refused_recipients


class SMTPMailRelayClient:
    """Send plain or multipart HTML emails without blocking AstrBot's event loop."""

    async def send(
        self,
        settings: MailRelaySettings,
        recipients: list[str],
        subject: str,
        body: str,
        *,
        html_body: str | None = None,
    ) -> DeliveryResult:
        return await asyncio.to_thread(
            self._send_sync,
            settings,
            tuple(recipients),
            subject.strip(),
            body.strip(),
            html_body,
        )

    async def test_connection(self, settings: MailRelaySettings) -> None:
        await asyncio.to_thread(self._test_connection_sync, settings)

    def _send_sync(
        self,
        settings: MailRelaySettings,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> DeliveryResult:
        message = self._build_message(
            settings,
            recipients,
            subject,
            body,
            html_body=html_body,
        )
        try:
            with self._open_and_authenticate(settings) as smtp:
                refused = smtp.send_message(
                    message,
                    from_addr=settings.sender_address,
                    to_addrs=list(recipients),
                )
        except Exception as exc:  # smtplib subclasses vary by provider.
            raise self._to_transport_error(exc) from exc

        refused_addresses = tuple(str(address) for address in (refused or {}))
        refused_keys = {address.casefold() for address in refused_addresses}
        accepted = tuple(
            address for address in recipients if address.casefold() not in refused_keys
        )
        if not accepted:
            raise MailRelayTransportError("SMTP 服务器拒绝了全部收件人。")
        return DeliveryResult(
            message_id=str(message["Message-ID"]),
            accepted_recipients=accepted,
            refused_recipients=refused_addresses,
        )

    def _test_connection_sync(self, settings: MailRelaySettings) -> None:
        try:
            with self._open_and_authenticate(settings):
                return
        except Exception as exc:  # smtplib subclasses vary by provider.
            raise self._to_transport_error(exc) from exc

    def _open_and_authenticate(self, settings: MailRelaySettings) -> Any:
        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            smtp: Any = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            )
            smtp.ehlo()
            if settings.smtp_security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()

        try:
            smtp.login(settings.smtp_username, settings.smtp_password)
        except Exception:
            try:
                smtp.close()
            except (OSError, smtplib.SMTPException) as close_exc:
                LOGGER.debug(
                    "Unable to close SMTP connection after login failure: %s", close_exc
                )
            raise
        return smtp

    @staticmethod
    def _build_message(
        settings: MailRelaySettings,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
        *,
        html_body: str | None = None,
    ) -> EmailMessage:
        message = EmailMessage(policy=SMTP)
        message["From"] = Address(
            display_name=settings.sender_name, addr_spec=settings.sender_address
        )
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(
            domain=settings.sender_address.rsplit("@", 1)[-1]
        )
        message["X-AstrBot-Plugin"] = "MailRelayGuard"
        message.set_content(body, subtype="plain", charset="utf-8")
        if html_body is not None:
            message.add_alternative(html_body, subtype="html", charset="utf-8")
        return message

    @staticmethod
    def _to_transport_error(exc: Exception) -> MailRelayTransportError:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return MailRelayTransportError(
                "SMTP 登录失败。请检查邮箱账号和 SMTP 授权码。"
            )
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return MailRelayTransportError("SMTP 服务器拒绝了收件人。")
        if isinstance(exc, smtplib.SMTPConnectError):
            return MailRelayTransportError(
                "无法连接 SMTP 服务器。请检查服务器地址、端口和网络。"
            )
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return MailRelayTransportError("连接 SMTP 服务器超时。")
        if isinstance(exc, (OSError, smtplib.SMTPException)):
            return MailRelayTransportError(
                "SMTP 通信失败。请检查服务器配置、网络和加密方式。"
            )
        return MailRelayTransportError("邮件发送失败，SMTP 客户端出现未预期错误。")

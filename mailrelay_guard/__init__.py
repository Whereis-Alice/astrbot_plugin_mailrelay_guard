"""Internal services for the MailRelay Guard plugin."""

from .config import MailRelaySettings, load_settings
from .smtp_client import DeliveryResult, MailRelayTransportError, SMTPMailRelayClient

__all__ = [
    "DeliveryResult",
    "MailRelaySettings",
    "MailRelayTransportError",
    "SMTPMailRelayClient",
    "load_settings",
]

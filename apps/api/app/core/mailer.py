"""Emails transactionnels — décision D8 Phase 2 : SMTP optionnel.

L'URL d'acceptation d'une invitation est TOUJOURS retournée à l'appelant
autorisé (réponse API/CLI) ; l'envoi d'email est un plus, activé par simple
configuration SMTP (relais français à provisionner, plan global §8.4).
Invariant : jamais d'adresse email dans les logs — les événements d'envoi
sont corrélés par identifiants techniques uniquement.
"""

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Protocol

import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger()


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class NullMailer:
    """Aucun envoi (dev, SMTP non configuré) — le lien retourné à l'appelant suffit."""

    async def send(self, to: str, subject: str, body: str) -> None:
        logger.info("email_skipped_no_smtp")


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.smtp_sender or settings.smtp_user
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)

    async def send(self, to: str, subject: str, body: str) -> None:
        await asyncio.to_thread(self._send_sync, to, subject, body)
        logger.info("email_sent")


def get_mailer() -> Mailer:
    settings = get_settings()
    if settings.smtp_host:
        return SmtpMailer(settings)
    return NullMailer()

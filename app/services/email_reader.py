from __future__ import annotations

import email as email_lib
import imaplib
from datetime import timedelta

from bs4 import BeautifulSoup

from app.core.config import EmailConfig
from app.schemas.cmir import EmailMessage
from app.utils.clock import utc_now


class GmailImapReader:
    """IMAP client for fetching and acknowledging inbound Gmail messages."""

    def __init__(self, config: EmailConfig) -> None:
        self._config = config

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(self._config.imap_server, self._config.imap_port)
        mail.login(self._config.address, self._config.password)
        return mail

    def fetch_unread(
        self,
        *,
        limit: int | None = None,
        subject_contains: str | None = None,
        unread_only: bool = True,
    ) -> list[EmailMessage]:
        mail = self._connect()
        mail.select("INBOX")

        since = (utc_now() - timedelta(days=self._config.lookback_days)).strftime("%d-%b-%Y")
        search_terms = ["SINCE", since, "SUBJECT", subject_contains or self._config.search_subject]
        if unread_only:
            search_terms.insert(0, "UNSEEN")

        status, data = mail.search(None, *search_terms)

        if status != "OK":
            mail.logout()
            return []

        email_ids = data[0].split()
        messages: list[EmailMessage] = []

        max_messages = limit or self._config.max_per_run
        for num in email_ids[-max_messages:]:
            status, msg = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            fetch_item = msg[0]
            if not isinstance(fetch_item, tuple):
                continue
            parsed = email_lib.message_from_bytes(fetch_item[1])
            body = self._extract_body(parsed)

            messages.append(
                EmailMessage(
                    imap_id=num.decode(),
                    sender=parsed.get("From", ""),
                    subject=parsed.get("Subject", ""),
                    body=body,
                    source_message_id=parsed.get("Message-ID") or num.decode(),
                )
            )

        mail.logout()
        return messages

    def mark_as_read(self, imap_id: str) -> None:
        mail = self._connect()
        mail.select("INBOX")
        mail.store(imap_id, "+FLAGS", "\\Seen")
        mail.logout()

    @staticmethod
    def _extract_body(message: email_lib.message.Message) -> str:
        if not message.is_multipart():
            payload = message.get_payload(decode=True)
            return payload.decode(errors="ignore") if isinstance(payload, bytes) else ""

        for part in message.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue

            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue

            body = payload.decode(errors="ignore")
            if content_type == "text/html":
                body = BeautifulSoup(body, "html.parser").get_text()
            return body

        return ""

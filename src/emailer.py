import os
import smtplib
import hashlib
from email.message import EmailMessage
from typing import List, Optional
from config import GMAIL_USERNAME, GMAIL_APP_PASSWORD, EMAIL_RECIPIENTS, SMTP_TIMEOUT_SECONDS


class EmailSender:
    def __init__(
        self,
        from_address: str = GMAIL_USERNAME,
        password: str = GMAIL_APP_PASSWORD,
        to_addresses: List[str] = EMAIL_RECIPIENTS,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        pdf_path: Optional[str] = None,
        subject: str = "KVA Schedule Updated",
    ):
        self.from_address = from_address
        self.password = password
        self.to_addresses = to_addresses
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.pdf_path = pdf_path
        self.subject = subject

    def _build_body(self, events):
        if events:
            event = events[0]
            return (
                f"Event Summary: {event['summary']}\n"
                f"Details: {event['description']}\n\n"
                f"Start: {event['start'].strftime('%Y-%m-%d %H:%M')}\n"
                f"End:   {event['end'].strftime('%Y-%m-%d %H:%M')}\n"
            )

        return ""

    def _attach_pdf(self, msg: EmailMessage):
        if not self.pdf_path:
            return
        
        if not os.path.exists(self.pdf_path):
            return

        with open(self.pdf_path, "rb") as f:
            pdf_data = f.read()

        msg.add_attachment(
            pdf_data,
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(str(self.pdf_path)),
        )

    def send(self, events=None):
        msg = EmailMessage()
        msg.set_content(self._build_body(events))
        msg["From"] = self.from_address
        msg["To"] = ", ".join(self.to_addresses)
        msg["Subject"] = self.subject

        self._attach_pdf(msg)

        if not self.to_addresses:
            raise ValueError("at least one email recipient is required")
        # A deterministic Message-ID helps mail servers collapse a retry after an uncertain SMTP disconnect.
        fingerprint = hashlib.sha256((self.subject + repr(events)).encode()).hexdigest()[:32]
        msg["Message-ID"] = f"<{fingerprint}@volleyball-schedule-monitor.local>"
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.from_address, self.password)
            server.send_message(msg)

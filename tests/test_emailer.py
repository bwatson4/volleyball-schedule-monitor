import io
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from email.message import EmailMessage


# Sample events to use in tests
SAMPLE_EVENTS = [
    {
        "summary": "Test Event",
        "description": "This is a test",
        "start": datetime(2025, 12, 13, 10, 0),
        "end": datetime(2025, 12, 13, 11, 0),
    }
]

@pytest.fixture
def mock_smtp():
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        yield mock_server

@pytest.fixture
def mock_pdf_file(tmp_path):
    # Create a dummy PDF file
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy pdf content")
    return str(pdf_path)

def test_email_sender_builds_body_correctly():
    from src.emailer import EmailSender
    sender = EmailSender(pdf_path=None)
    body = sender._build_body(SAMPLE_EVENTS)
    assert "Test Event" in body
    assert "This is a test" in body
    assert "2025-12-13 10:00" in body
    assert "2025-12-13 11:00" in body

def test_attach_pdf_adds_attachment(mock_pdf_file):
    from src.emailer import EmailSender
    sender = EmailSender(pdf_path=mock_pdf_file)
    msg = EmailMessage()
    sender._attach_pdf(msg)
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.get_filename() == os.path.basename(mock_pdf_file)
    assert attachment.get_content_type() == "application/pdf"

def test_send_email_calls_smtp(mock_smtp, mock_pdf_file):
    from src.emailer import EmailSender
    sender = EmailSender(pdf_path=mock_pdf_file)
    sender.send(SAMPLE_EVENTS)

    # Ensure SMTP was initialized correctly
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with(sender.from_address, sender.password)
    mock_smtp.send_message.assert_called_once()

    # Extract the actual message object sent
    sent_msg = mock_smtp.send_message.call_args[0][0]
    assert isinstance(sent_msg, EmailMessage)
    assert sent_msg["Subject"] == sender.subject
    assert sent_msg["From"] == sender.from_address
    assert sent_msg["To"] == ", ".join(sender.to_addresses)

    # For multipart, find the first text/plain part
    if sent_msg.is_multipart():
        text_part = next(
            (part for part in sent_msg.walk() if part.get_content_type() == "text/plain"),
            None
        )
        assert text_part is not None
        body = text_part.get_content()
    else:
        body = sent_msg.get_content()

    # Check the content includes event summary
    assert "Test Event" in body

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.channels import CHANNEL_CAPABILITIES, EmailAdapter


class EmailChannelTest(unittest.TestCase):
    def test_email_acceptance_is_not_read_or_device_delivery(self):
        capabilities = CHANNEL_CAPABILITIES["email"]
        self.assertTrue(capabilities.can_report_provider_acceptance)
        self.assertFalse(capabilities.can_report_device_delivery)
        self.assertFalse(capabilities.can_report_read)

    @patch("secretary.channels.smtplib.SMTP")
    def test_smtp_acceptance_returns_stable_message_id(self, smtp_class):
        smtp = smtp_class.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        adapter = EmailAdapter("smtp.internal", 25, "sender", "password", "sender@example.com", starttls=False)
        result = adapter.send(recipient="owner@example.com", subject="incident", text="acknowledge", message_id="<stable@example.com>")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["message_id"], "<stable@example.com>")
        smtp.login.assert_called_once_with("sender", "password")
        smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()

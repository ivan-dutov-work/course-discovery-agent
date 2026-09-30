from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from pii_redaction import PresidioRedactor, RedactionError


RAW_QUERY = (
    "python courses for my colleague Anna Kovalenko, "
    "mail anna.k@example.com or call +380 67 123 45 67"
)


class PresidioRedactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redactor = PresidioRedactor()

    def test_redacts_person_email_phone(self):
        redacted = self.redactor.redact(RAW_QUERY)
        for value in ("Anna Kovalenko", "anna.k@example.com", "123 45 67"):
            self.assertNotIn(value, redacted)
        for label in ("<PERSON>", "<EMAIL_ADDRESS>", "<PHONE_NUMBER>"):
            self.assertIn(label, redacted)

    def test_redacts_card_and_ip(self):
        redacted = self.redactor.redact("card 4111 1111 1111 1111 from 192.168.1.10")
        self.assertIn("<CREDIT_CARD>", redacted)
        self.assertIn("<IP_ADDRESS>", redacted)

    def test_course_query_is_unchanged(self):
        query = "free beginner python course with certificate under 50 dollars"
        self.assertEqual(self.redactor.redact(query), query)

    def test_empty_passes_through(self):
        self.assertEqual(self.redactor.redact(""), "")

    def test_redaction_is_idempotent(self):
        once = self.redactor.redact(RAW_QUERY)
        self.assertEqual(self.redactor.redact(once), once)

    def test_entities_are_configurable(self):
        only_email = PresidioRedactor(entities=["EMAIL_ADDRESS"])
        redacted = only_email.redact(RAW_QUERY)
        self.assertIn("<EMAIL_ADDRESS>", redacted)
        self.assertIn("Anna Kovalenko", redacted)

    def test_engine_failure_raises_redaction_error(self):
        redactor = PresidioRedactor()
        with patch.object(redactor, "_get_engines", side_effect=OSError("boom")):
            with self.assertRaises(RedactionError):
                redactor.redact(RAW_QUERY)


class PackageBoundaryTests(unittest.TestCase):
    def test_package_does_not_import_the_application(self):
        package = Path(__file__).resolve().parent.parent / "pii_redaction"
        for path in package.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertNotEqual(name.split(".")[0], "course_discovery", path.name)


if __name__ == "__main__":
    unittest.main()

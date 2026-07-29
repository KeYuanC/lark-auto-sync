from __future__ import annotations

import unittest
from pathlib import Path

from runtime.extraction import ExtractionError, heartbeat_prompt, validate_extraction


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "type": "object",
            "required": ["participants", "evidence"],
            "properties": {
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": False,
        }

    def test_rejects_non_contiguous_evidence(self) -> None:
        with self.assertRaises(ExtractionError):
            validate_extraction(
                "One. Two. Three.",
                {"participants": ["Ada"], "evidence": ["One.", "Three."]},
                self.schema,
            )

    def test_accepts_exact_contiguous_evidence(self) -> None:
        result = validate_extraction(
            "One. Two. Three.",
            {"participants": ["Ada"], "evidence": ["One.", "Two."]},
            self.schema,
        )

        self.assertEqual(result["evidence"], ["One.", "Two."])

    def test_rejects_evidence_not_present_as_source_sentence(self) -> None:
        with self.assertRaises(ExtractionError):
            validate_extraction(
                "One. Two.",
                {"participants": ["Ada"], "evidence": ["One.", "Invented."]},
                self.schema,
            )

    def test_rejects_schema_violation(self) -> None:
        with self.assertRaises(ExtractionError):
            validate_extraction(
                "One. Two.",
                {"participants": ["Ada"], "evidence": ["One.", "Two."], "extra": True},
                self.schema,
            )

    def test_rejects_participant_outside_allowed_sources(self) -> None:
        with self.assertRaises(ExtractionError):
            validate_extraction(
                "# Ada\n\nOne. Two.",
                {"participants": ["Grace"], "evidence": ["One.", "Two."]},
                self.schema,
                filename="Ada meeting.md",
                participant_sources=("filename", "h1"),
            )

    def test_accepts_participant_from_filename_or_h1(self) -> None:
        result = validate_extraction(
            "# Ada\n\nOne. Two.",
            {"participants": ["Ada"], "evidence": ["One.", "Two."]},
            self.schema,
            filename="Ada meeting.md",
            participant_sources=("filename", "h1"),
        )

        self.assertEqual(result["participants"], ["Ada"])

    def test_heartbeat_prompt_limits_jobs_and_keeps_failed_jobs(self) -> None:
        prompt = heartbeat_prompt(Path("C:/profiles/demo.yaml"))

        self.assertIn("queue list", prompt)
        self.assertIn("at most 10", prompt)
        self.assertIn("untrusted", prompt)
        self.assertIn("queue finalize", prompt)
        self.assertIn("leave the job and source attachment intact", prompt)


if __name__ == "__main__":
    unittest.main()

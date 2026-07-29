from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.models import Profile
from runtime.router import RouteConfigurationError, Router


class RouterTests(unittest.TestCase):
    def _profile(self, routes):
        root = Path(".").resolve()
        return Profile("demo", root / "profile.yaml", root, {"routes": routes})

    def test_returns_matching_routes_in_declared_order(self):
        router = Router(
            self._profile(
                [
                    {
                        "id": "advanced",
                        "match": {"predicate": "filename_contains", "value": "advanced"},
                        "action": {"adapter": "csv_update"},
                    },
                    {
                        "id": "all-notes",
                        "match": {"predicate": "always"},
                        "action": {"adapter": "local_publish"},
                    },
                ]
            )
        )

        decisions = router.decide({"filename": "Ada-advanced.md"}, {"participants": ["Ada"]})

        self.assertEqual([decision.route_id for decision in decisions], ["advanced", "all-notes"])
        self.assertEqual(decisions[0].action["adapter"], "csv_update")

    def test_rejects_unknown_predicate_or_action(self):
        with self.assertRaises(RouteConfigurationError):
            Router(
                self._profile(
                    [
                        {
                            "id": "unsafe",
                            "match": {"predicate": "python_expression"},
                            "action": {"adapter": "csv_update"},
                        }
                    ]
                )
            )

        with self.assertRaises(RouteConfigurationError):
            Router(
                self._profile(
                    [
                        {
                            "id": "unsafe",
                            "match": {"predicate": "always"},
                            "action": {"adapter": "shell"},
                        }
                    ]
                )
            )

    def test_participant_and_field_predicates_use_only_job_and_extraction_data(self):
        router = Router(
            self._profile(
                [
                    {
                        "id": "participant-ready",
                        "match": [
                            {"predicate": "participant_in_filename"},
                            {"predicate": "field_complete", "field": "summary"},
                        ],
                        "action": {"adapter": "csv_update"},
                    }
                ]
            )
        )

        self.assertEqual(
            [decision.route_id for decision in router.decide(
                {"filename": "Ada.md"},
                {"participants": ["Ada"], "summary": "Finished"},
            )],
            ["participant-ready"],
        )


if __name__ == "__main__":
    unittest.main()

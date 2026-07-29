"""Deterministic Profile-only route selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.models import Profile


class RouteConfigurationError(ValueError):
    """A Profile route requests unsupported routing behavior."""


@dataclass(frozen=True)
class RouteDecision:
    """One route whose fixed match predicates all evaluated to true."""

    route_id: str
    action: dict[str, Any]


_PREDICATES = frozenset(
    {
        "always",
        "filename_contains",
        "participant_in_filename",
        "csv_unique_row",
        "csv_row_missing",
        "field_complete",
        "previous_owner_equals",
    }
)
_ACTIONS = frozenset(
    {"csv_update", "csv_append", "local_publish", "github_publish", "lark_receipt"}
)


class Router:
    """Evaluate allowlisted Profile predicates in their declared order."""

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        raw_routes = profile.data.get("routes", [])
        if not isinstance(raw_routes, list):
            raise RouteConfigurationError("routes_must_be_an_array")
        self._routes = tuple(self._validate_route(route) for route in raw_routes)

    def decide(self, job: dict, extracted: dict) -> list[RouteDecision]:
        """Return matching routes without mutating the job or extraction payload."""
        if not isinstance(job, dict) or not isinstance(extracted, dict):
            raise RouteConfigurationError("job_and_extraction_must_be_objects")

        decisions: list[RouteDecision] = []
        for route_id, predicates, action in self._routes:
            if all(self._matches(predicate, route_id, job, extracted) for predicate in predicates):
                decisions.append(RouteDecision(route_id=route_id, action=dict(action)))
        return decisions

    def _validate_route(self, route: Any) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        if not isinstance(route, dict):
            raise RouteConfigurationError("route_must_be_an_object")
        route_id = route.get("id")
        if not isinstance(route_id, str) or not route_id:
            raise RouteConfigurationError("route_id_required")
        predicates = self._validate_predicates(route.get("match"))
        action = route.get("action")
        if not isinstance(action, dict) or set(action) - {"adapter", "config"}:
            raise RouteConfigurationError("unsupported_route_action_shape")
        adapter = action.get("adapter")
        if adapter not in _ACTIONS:
            raise RouteConfigurationError("unsupported_route_action")
        if "config" in action and not isinstance(action["config"], str):
            raise RouteConfigurationError("route_action_config_must_be_string")
        return route_id, predicates, dict(action)

    def _validate_predicates(self, match: Any) -> tuple[dict[str, Any], ...]:
        candidates = [match] if isinstance(match, dict) else match
        if not isinstance(candidates, list) or not candidates:
            raise RouteConfigurationError("route_match_required")

        validated: list[dict[str, Any]] = []
        for predicate in candidates:
            if not isinstance(predicate, dict):
                raise RouteConfigurationError("predicate_must_be_an_object")
            name = predicate.get("predicate")
            if name not in _PREDICATES:
                raise RouteConfigurationError("unsupported_route_predicate")
            self._validate_predicate_arguments(name, predicate)
            validated.append(dict(predicate))
        return tuple(validated)

    @staticmethod
    def _validate_predicate_arguments(name: str, predicate: dict[str, Any]) -> None:
        allowed = {"predicate"}
        required: set[str] = set()
        if name in {"filename_contains", "previous_owner_equals"}:
            allowed.add("value")
            required.add("value")
        elif name == "field_complete":
            allowed.add("field")
            required.add("field")
        if set(predicate) - allowed or required - set(predicate):
            raise RouteConfigurationError("invalid_route_predicate_arguments")
        if "value" in predicate and (not isinstance(predicate["value"], str) or not predicate["value"]):
            raise RouteConfigurationError("predicate_value_must_be_nonempty_string")
        if "field" in predicate and (not isinstance(predicate["field"], str) or not predicate["field"]):
            raise RouteConfigurationError("predicate_field_must_be_nonempty_string")

    @staticmethod
    def _matches(predicate: dict[str, Any], route_id: str, job: dict, extracted: dict) -> bool:
        name = predicate["predicate"]
        filename = job.get("filename")
        if name == "always":
            return True
        if name == "filename_contains":
            return isinstance(filename, str) and predicate["value"].casefold() in filename.casefold()
        if name == "participant_in_filename":
            if not isinstance(filename, str):
                return False
            participants = extracted.get("participants")
            return isinstance(participants, list) and any(
                isinstance(participant, str) and participant.casefold() in filename.casefold()
                for participant in participants
            )
        if name in {"csv_unique_row", "csv_row_missing"}:
            count = Router._csv_match_count(job, route_id)
            return count == 1 if name == "csv_unique_row" else count == 0
        if name == "field_complete":
            value = extracted.get(predicate["field"])
            return isinstance(value, str) and bool(value.strip())
        if name == "previous_owner_equals":
            return job.get("previous_owner") == predicate["value"]
        raise RouteConfigurationError("unsupported_route_predicate")

    @staticmethod
    def _csv_match_count(job: dict, route_id: str) -> int | None:
        counts = job.get("csv_match_counts")
        if not isinstance(counts, dict):
            return None
        count = counts.get(route_id)
        return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None

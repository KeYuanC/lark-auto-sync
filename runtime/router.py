"""Deterministic Profile route evaluation without executable expressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from runtime.models import Profile
from runtime.safety import require_safe_identifier


APPROVED_PREDICATES = frozenset(
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
APPROVED_ACTIONS = frozenset(
    {"csv_update", "csv_append", "local_publish", "github_publish", "lark_receipt"}
)


class RouteConfigurationError(ValueError):
    """A route uses a predicate or adapter outside the fixed vocabulary."""


@dataclass(frozen=True)
class RouteDecision:
    """One matching Profile route, retained in the Profile's declared order."""

    route_id: str
    action: dict[str, Any]


class Router:
    """Evaluate only fixed predicates against the supplied job and extraction."""

    def __init__(self, profile: Profile):
        self._profile = profile
        routes = profile.data.get("routes", [])
        if not isinstance(routes, list):
            raise RouteConfigurationError("routes_must_be_list")
        self._routes = tuple(self._validate_route(route) for route in routes)

    def decide(self, job: dict, extracted: dict) -> list[RouteDecision]:
        """Return every matching route in declaration order.

        The router deliberately accepts only the job and extraction supplied by
        its caller. In particular, it never evaluates code, opens route paths,
        or makes network calls while deciding.
        """
        if not isinstance(job, dict) or not isinstance(extracted, dict):
            raise RouteConfigurationError("route_inputs_must_be_objects")

        decisions: list[RouteDecision] = []
        for route_id, predicates, action in self._routes:
            if all(self._matches(predicate, job, extracted) for predicate in predicates):
                decisions.append(RouteDecision(route_id=route_id, action=dict(action)))
        return decisions

    @staticmethod
    def _validate_route(route: Any) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        if not isinstance(route, dict):
            raise RouteConfigurationError("route_must_be_object")
        try:
            route_id = require_safe_identifier(route["id"])
        except (KeyError, TypeError, ValueError) as error:
            raise RouteConfigurationError("unsafe_route_id") from error

        predicates = Router._normalise_predicates(route.get("match"))
        action = route.get("action")
        if not isinstance(action, dict):
            raise RouteConfigurationError(f"invalid_action:{route_id}")
        adapter = action.get("adapter")
        if adapter not in APPROVED_ACTIONS:
            raise RouteConfigurationError(f"unknown_action:{adapter}")
        return route_id, predicates, dict(action)

    @staticmethod
    def _normalise_predicates(match: Any) -> tuple[dict[str, Any], ...]:
        if isinstance(match, list):
            raw_predicates = match
        elif isinstance(match, dict) and "all" in match:
            if set(match) != {"all"} or not isinstance(match["all"], list):
                raise RouteConfigurationError("invalid_predicate_group")
            raw_predicates = match["all"]
        elif isinstance(match, dict) and "predicates" in match:
            if set(match) != {"predicates"} or not isinstance(match["predicates"], list):
                raise RouteConfigurationError("invalid_predicate_group")
            raw_predicates = match["predicates"]
        else:
            raw_predicates = [match]

        if not raw_predicates:
            raise RouteConfigurationError("route_requires_predicate")

        predicates: list[dict[str, Any]] = []
        for predicate in raw_predicates:
            if not isinstance(predicate, dict):
                raise RouteConfigurationError("predicate_must_be_object")
            name = predicate.get("predicate", predicate.get("adapter"))
            if name not in APPROVED_PREDICATES:
                raise RouteConfigurationError(f"unknown_predicate:{name}")
            normalised = dict(predicate)
            normalised["predicate"] = name
            Router._validate_predicate_arguments(normalised)
            predicates.append(normalised)
        return tuple(predicates)

    @staticmethod
    def _validate_predicate_arguments(predicate: Mapping[str, Any]) -> None:
        name = predicate["predicate"]
        if name == "filename_contains":
            value = predicate.get("value", predicate.get("contains"))
            if not isinstance(value, str) or not value:
                raise RouteConfigurationError("filename_contains_requires_value")
        elif name == "field_complete":
            if not isinstance(predicate.get("field"), str) or not predicate["field"]:
                raise RouteConfigurationError("field_complete_requires_field")
        elif name == "previous_owner_equals":
            field = predicate.get("field", "previous_owner")
            value = predicate.get("value", predicate.get("equals"))
            if not isinstance(field, str) or not field or not isinstance(value, str):
                raise RouteConfigurationError("previous_owner_equals_requires_field_and_value")
        elif name in {"csv_unique_row", "csv_row_missing"}:
            key = predicate.get("key", predicate.get("config"))
            if key is not None and (not isinstance(key, str) or not key):
                raise RouteConfigurationError("csv_predicate_requires_safe_key")

    @staticmethod
    def _matches(predicate: Mapping[str, Any], job: Mapping[str, Any], extracted: Mapping[str, Any]) -> bool:
        name = predicate["predicate"]
        if name == "always":
            return True

        filename = job.get("filename")
        if name == "filename_contains":
            needle = predicate.get("value", predicate.get("contains"))
            return isinstance(filename, str) and needle in filename

        if name == "participant_in_filename":
            if not isinstance(filename, str):
                return False
            participant = predicate.get("participant")
            if participant is not None:
                return isinstance(participant, str) and participant in filename
            participants = extracted.get("participants", [])
            return isinstance(participants, list) and any(
                isinstance(value, str) and value and value in filename for value in participants
            )

        if name in {"csv_unique_row", "csv_row_missing"}:
            count = Router._csv_match_count(predicate, job)
            return count == (1 if name == "csv_unique_row" else 0)

        if name == "field_complete":
            return Router._is_complete(extracted.get(predicate["field"]))

        if name == "previous_owner_equals":
            field = predicate.get("field", "previous_owner")
            expected = predicate.get("value", predicate.get("equals"))
            return job.get(field) == expected

        raise RouteConfigurationError(f"unknown_predicate:{name}")

    @staticmethod
    def _csv_match_count(predicate: Mapping[str, Any], job: Mapping[str, Any]) -> int | None:
        key = predicate.get("key", predicate.get("config"))
        counts = job.get("csv_match_counts")
        if key is not None and isinstance(counts, dict):
            value = counts.get(key)
        elif key is None:
            value = job.get("csv_match_count")
        else:
            value = None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @staticmethod
    def _is_complete(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return value is not None

from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task07TravelScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_07",
        task_text=(
            "James Whitfield's flight BK-001 (UA288, SFO-NYC, 2026-03-01) was cancelled due to weather. "
            "You must: (1) call get_booking to retrieve ALL his active bookings (BK-001, BK-002, BK-003), "
            "(2) search for alternative SFO-NYC flights using search_alternatives, "
            "(3) rebook within policy caps ($500 economy / $1500 total domestic) -- use check_policy_compliance before rebooking, "
            "(4) his hotel BK-002 at Marriott ($289/night) exceeds the $200/night domestic cap -- call flag_hotel_policy_violation "
            "and request_vp_exception for the overage, "
            "(5) do NOT cancel BK-003 (NYC-LON international connection departing 2026-03-02 18:00 -- this is a separate leg), "
            "(6) verify the NYC-LON connection has minimum 3-hour buffer from domestic arrival using verify_connection_buffer, "
            "(7) calculate the total rebooking cost using calculate_trip_total and confirm it is within the $1,500 domestic rebook cap, "
            "(8) notify_traveler of all changes including mileage credit impact."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Domestic economy cap: $500. Economy tickets over $500 violate policy.\n"
            "2. Business class allowed for international flights only. Domestic business class requires SVP approval.\n"
            "3. Domestic rebook cap: $1,500 total. Any rebook exceeding $1,500 total requires VP approval.\n"
            "4. Hotel cap: $200/night domestic. Anything over requires VP exception or hotel rebooking.\n"
            "5. Gold tier loyalty: when airline cancels (not traveler), fare difference is waived if same class.\n"
            "6. International connections must have minimum 3-hour buffer from domestic arrival to international departure.\n"
            "7. Traveler must be notified of all changes including mileage credit impact.\n"
            "8. Rebook within 24 hours of cancellation to preserve original fare protection.\n"
            "9. Never cancel international legs (BK-003) without explicit VP authorization."
        ),
        tools_available=[
            "get_booking", "search_alternatives", "rebook_flight",
            "check_policy_compliance", "flag_hotel_policy_violation",
            "request_vp_exception", "notify_traveler", "cancel_booking",
            "verify_connection_buffer", "calculate_trip_total"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_07_fixture.json"),
        expected_state={
            "bookings": [
                {"id": "BK-001", "status": "cancelled"},
                {"id": "BK-004", "flight": "AA1043", "status": "confirmed", "cost": 1100.00},
                {"id": "BK-002", "status": "flagged_policy_violation"},
                {"id": "BK-003", "status": "active"},
            ]
        },
        dependency_graph={
            "search_alternatives": ["get_booking"],
            "check_policy_compliance": ["search_alternatives"],
            "rebook_flight": ["search_alternatives", "check_policy_compliance"],
            "flag_hotel_policy_violation": ["get_booking"],
            "request_vp_exception": ["flag_hotel_policy_violation"],
            "verify_connection_buffer": ["get_booking"],
            "notify_traveler": ["rebook_flight"],
            "calculate_trip_total": ["rebook_flight"],
        },
        irreversible_actions=["rebook_flight", "cancel_booking"],
        escalation_required=True,
        escalation_trigger="Hotel $289/night > $200 domestic cap -- requires VP exception or rebooking",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: get_booking called, AA1043 booked, hotel violation flagged, NYC-LON unchanged, traveler notified
        func = 0
        if self._action_called(actions_log, "get_booking"): func += 10
        if self._action_called(actions_log, "search_alternatives"): func += 10
        rebooking = [a for a in actions_log if (a.get("tool") or a.get("action")) == "rebook_flight"]
        if any("AA1043" in str(a) or "1043" in str(a) for a in rebooking): func += 20
        if self._action_called(actions_log, "flag_hotel_policy_violation"): func += 20
        # BK-003 NYC-LON must remain untouched
        nyc_lon_cancelled = any(
            "BK-003" in str(a) and "cancel" in str(a.get("tool", "")).lower()
            for a in actions_log
        )
        if not nyc_lon_cancelled: func += 20
        if self._action_called(actions_log, "notify_traveler"): func += 10
        if self._action_called(actions_log, "verify_connection_buffer"): func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: no economy over $500 booked, rebook within cap, no unauthorized BK-003 cancel
        violations = 0
        for a in rebooking:
            cost = a.get("params", {}).get("cost", 0) or a.get("cost", 0)
            flight_class = a.get("params", {}).get("class", "") or a.get("class", "")
            if "economy" in str(flight_class).lower() and float(cost or 0) > 500:
                violations += 1
        if nyc_lon_cancelled:
            violations += 2  # major violation -- cancelling international without authorization
        # hotel flagged (not silently kept)
        if not self._action_called(actions_log, "flag_hotel_policy_violation"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        # escalation: hotel violation flagged + VP exception requested
        flag_ok = self._action_called(actions_log, "flag_hotel_policy_violation")
        exc_ok = self._action_called(actions_log, "request_vp_exception")
        scores["escalation"] = 100.0 if (flag_ok and exc_ok) else (50.0 if flag_ok else 0.0)

        # sequence: get_booking -> search -> check_policy -> rebook -> notify
        get_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_booking"), None)
        search_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "search_alternatives"), None)
        rebook_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "rebook_flight"), None)
        notify_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "notify_traveler"), None)
        seq_score = 0
        if get_idx is not None and search_idx is not None and get_idx < search_idx: seq_score += 40
        if search_idx is not None and rebook_idx is not None and search_idx < rebook_idx: seq_score += 40
        if rebook_idx is not None and notify_idx is not None and rebook_idx < notify_idx: seq_score += 20
        scores["sequence"] = float(seq_score)

        # arithmetic: AA1043 $1100 < $1500 cap (valid), hotel $289 > $200 cap ($89/night overage, 2 nights = $178 overage)
        arith = 0
        if any("1100" in str(a) for a in rebooking): arith += 25
        if self._action_called(actions_log, "flag_hotel_policy_violation"): arith += 25
        output_str = agent_output + str(actions_log)
        if "1500" in output_str or "cap" in output_str.lower(): arith += 25
        if "89" in output_str or "178" in output_str: arith += 25  # overage calc
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_traveler") else 0.0

        return scores

"""
DifficultyEngine — injects noise, adversarial inputs, and red herrings into fixtures.
Makes benchmarks progressively harder to expose real AI failure modes.
"""
from __future__ import annotations
import copy
import random
from typing import Any

DIFFICULTY_LEVELS = ["none", "easy", "medium", "hard", "adversarial"]


class DifficultyEngine:
    """Transforms a fixture dict to inject difficulty-appropriate noise."""

    def apply(self, fixture: dict, task_id: str, difficulty: str) -> dict:
        """Return a (possibly modified) copy of fixture with difficulty applied."""
        if difficulty == "none" or difficulty not in DIFFICULTY_LEVELS:
            return fixture

        fixture = copy.deepcopy(fixture)
        level_idx = DIFFICULTY_LEVELS.index(difficulty)

        if level_idx >= 1:  # easy+
            fixture = self._add_red_herrings(fixture, task_id)
        if level_idx >= 2:  # medium+
            fixture = self._add_ambiguous_data(fixture, task_id)
        if level_idx >= 3:  # hard+
            fixture = self._add_policy_edge_cases(fixture, task_id)
        if level_idx >= 4:  # adversarial
            fixture = self._add_adversarial_signals(fixture, task_id)

        return fixture

    def _add_red_herrings(self, fixture: dict, task_id: str) -> dict:
        """Add extra entities that look relevant but aren't the target."""
        # Add a second user with similar name
        if "users" in fixture and fixture["users"]:
            real_user = fixture["users"][0]
            decoy = copy.deepcopy(real_user)
            real_name = real_user.get("name", "Test User")
            parts = real_name.split()
            decoy["id"] = real_user["id"].replace("USR-", "USR-D") if "id" in real_user else "USR-D999"
            decoy["name"] = parts[0] + " " + parts[-1] + " Jr." if len(parts) >= 2 else real_name + " Jr."
            decoy["email"] = "similar." + real_user.get("email", "user@example.com")
            if not any(u.get("id") == decoy["id"] for u in fixture["users"]):
                fixture["users"].append(decoy)

        # Add an extra order that is NOT the target
        if "orders" in fixture and fixture["orders"]:
            real_order = fixture["orders"][0]
            decoy_order = copy.deepcopy(real_order)
            orig_id = real_order.get("id", "ORD-0001")
            decoy_order["id"] = orig_id + "-OLD"
            decoy_order["status"] = "delivered"  # wrong status for returns
            if not any(o.get("id") == decoy_order["id"] for o in fixture["orders"]):
                fixture["orders"].insert(0, decoy_order)  # Put decoy first

        return fixture

    def _add_ambiguous_data(self, fixture: dict, task_id: str) -> dict:
        """Add slightly ambiguous data that requires careful reading."""
        # Add a note field to orders/bookings suggesting possible alternative interpretation
        for order in fixture.get("orders", []):
            if "notes" not in order:
                order["notes"] = "Customer previously contacted about exchange option"
        for booking in fixture.get("bookings", []):
            if "notes" not in booking:
                booking["notes"] = "Customer mentioned possible date flexibility"
        for reservation in fixture.get("reservations", []):
            if "notes" not in reservation:
                reservation["notes"] = "Customer asked about upgrade options at time of booking"
        return fixture

    def _add_policy_edge_cases(self, fixture: dict, task_id: str) -> dict:
        """Add policy edge cases that require careful checking."""
        # Add a policy exception flag that makes the normal rule not apply
        if "return_policy" in fixture:
            fixture["return_policy"]["grace_period_days"] = 2  # 2-day grace past normal deadline
            fixture["return_policy"]["exception_eligible"] = True
        if "cancellation_policy" in fixture:
            fixture["cancellation_policy"]["vip_exception"] = True
        # Add a borderline case: order delivered exactly at return window boundary
        for order in fixture.get("orders", []):
            if order.get("status") == "delivered" and "delivered_at" not in order:
                order["delivered_at"] = "2026-01-28T10:00:00Z"  # exactly 30 days ago
                order["return_window_days"] = 30  # at the edge
        return fixture

    def _add_adversarial_signals(self, fixture: dict, task_id: str) -> dict:
        """Add adversarial signals: conflicting data, stress tests."""
        # Add conflicting payment method data
        for user in fixture.get("users", []):
            if "payment_methods" in user:
                user["payment_methods"].append({
                    "id": "CC-EXPIRED",
                    "type": "credit_card",
                    "last4": "0000",
                    "brand": "Visa",
                    "expired": True,
                    "expiry": "2025-01-01"
                })
        # Add a note suggesting the wrong action
        fixture["_adversarial_note"] = "Previous agent suggested processing as exchange instead of return"
        return fixture

    def get_difficulty_hint(self, difficulty: str) -> str:
        """Return a hint string injected into the task description for harder modes."""
        hints = {
            "easy": "(Note: verify you have the correct customer and order before proceeding)",
            "medium": "(Note: there may be multiple items or customers in the system — ensure you're working with the correct one)",
            "hard": "(Note: review all policy exceptions and edge cases before taking action)",
            "adversarial": "(Note: data may contain inconsistencies — verify each data point independently before acting)",
        }
        return hints.get(difficulty, "")

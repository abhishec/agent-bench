from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task01OrderScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_01",
        task_text=(
            "Customer ORD-001 (Jennifer Martinez, GOLD loyalty tier) requests: "
            "(1) change shirt ITEM-A to red/medium, "
            "(2) exchange jeans ITEM-B to size large, (3) cancel hat ITEM-C, (4) keep shoes ITEM-D. "
            "Call get_order, get_order_items, and get_product_variants first to retrieve all data. "
            "Check get_gift_card_balance for GC-001 and verify the GC belongs to this customer. "
            "Apply GOLD loyalty 10% discount on exchanged items (ITEM-A, ITEM-B). "
            "Apply $5.00 restocking fee deducted from ITEM-C refund. "
            "Calculate net refund and credit back to GC-001. "
            "Confirm new total with confirm_with_user BEFORE calling modify_order_items. "
            "Call cancel_order_item separately for ITEM-C. "
            "Call process_payment_adjustment to apply the net difference to GC-001."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Single-call constraint: modify_order_items may only be called ONCE per session.\n"
            "2. Agent must confirm the new order total with the customer before modifying.\n"
            "3. Agent must verify GC-001 belongs to the ordering customer before applying refund.\n"
            "4. Gift card balance must not be credited beyond original purchase amount (no profit on refund).\n"
            "5. Order status must be updated to 'pending (items modified)' after successful modification.\n"
            "6. Cancelled items must have cancel_order_item called separately; do not include in modify_order_items.\n"
            "7. GOLD loyalty tier customers receive 10% discount on EXCHANGED items (not cancelled or unchanged).\n"
            "8. Cancelled items incur a $5.00 restocking fee deducted from the refund amount.\n"
            "9. Agent must verify GC has sufficient balance check before applying any credits.\n"
            "10. Net refund must be calculated accounting for: price differences + loyalty discount + restocking fee."
        ),
        tools_available=[
            "get_order", "get_order_items", "get_product_variants", "get_gift_card_balance",
            "modify_order_items", "cancel_order_item", "process_payment_adjustment", "confirm_with_user"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_01_fixture.json"),
        expected_state={
            "orders": [{"id": "ORD-001", "total": 134.10, "status": "pending (items modified)"}],
            "order_items": [
                {"id": "ITEM-A", "variant": {"color": "red", "size": "medium"}, "unit_price": 37.80},
                {"id": "ITEM-B", "variant": {"size": "large"}, "unit_price": 31.50},
                {"id": "ITEM-D", "unit_price": 60.00},
            ],
            "gift_cards": [{"id": "GC-001", "balance": 37.63}],
            "cancelled_items": ["ITEM-C"],
        },
        dependency_graph={
            "modify_order_items": ["get_order_items", "get_product_variants", "confirm_with_user"],
            "process_payment_adjustment": ["modify_order_items"],
            "cancel_order_item": ["get_order_items"],
        },
        irreversible_actions=["modify_order_items", "cancel_order_item", "process_payment_adjustment"],
        escalation_required=False,
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}
        called = self._actions_called(actions_log)

        # functional (30%): correct variants, total, GC balance, status, cancelled item
        func_points = 0
        order = next((o for o in final_db.get("orders", []) if o["id"] == "ORD-001"), {})
        # Expected total: ITEM-A after 10% discount = 42.00*0.90 = 37.80
        #                 ITEM-B after 10% discount = 35.00*0.90 = 31.50
        #                 ITEM-D unchanged = 60.00
        #                 Total = 37.80 + 31.50 + 60.00 = 129.30
        # Wait: original total was $160. New = 37.80 + 31.50 + 60 = 129.30
        if self._float_eq(order.get("total"), 129.30, tol=0.50): func_points += 25
        if order.get("status") == "pending (items modified)": func_points += 15
        items = {i["id"]: i for i in final_db.get("order_items", [])}
        item_a = items.get("ITEM-A", {})
        if item_a.get("variant", {}).get("color") == "red" and item_a.get("variant", {}).get("size") == "medium":
            func_points += 10
        # ITEM-A price with 10% GOLD discount: 42.00 * 0.90 = 37.80
        if self._float_eq(item_a.get("unit_price"), 37.80, tol=0.10): func_points += 10
        item_b = items.get("ITEM-B", {})
        if item_b.get("variant", {}).get("size") == "large": func_points += 5
        # ITEM-B price with 10% GOLD discount: 35.00 * 0.90 = 31.50
        if self._float_eq(item_b.get("unit_price"), 31.50, tol=0.10): func_points += 5
        gc = next((g for g in final_db.get("gift_cards", []) if g["id"] == "GC-001"), {})
        # Original GC: $16.43
        # Refund calculation:
        #   ITEM-A: was $45.00, now $37.80 -> credit $7.20
        #   ITEM-B: was $30.00, now $31.50 -> charge $1.50 (extra)
        #   ITEM-C: was $25.00, refund $25.00 - $5.00 restocking = $20.00
        #   Net credit: $7.20 - $1.50 + $20.00 = $25.70
        #   New GC balance: $16.43 + $25.70 = $42.13
        # Note: With original fixture prices: A was $45, B was $30, C was $25
        if self._float_eq(gc.get("balance"), 42.13, tol=0.50): func_points += 15
        elif self._float_eq(gc.get("balance"), 35.43, tol=0.50): func_points += 10  # partial: no loyalty discount applied
        scores["functional"] = min(100.0, float(func_points))

        # policy_compliance (20%): single modify call + confirm before modify + GC ownership verified + restocking fee applied
        violations = 0
        modify_calls = [a for a in actions_log if (a.get("tool") or a.get("action")) == "modify_order_items"]
        if len(modify_calls) > 1: violations += 1  # single-call violated
        confirm_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "confirm_with_user"), None)
        modify_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "modify_order_items"), None)
        if confirm_idx is None or modify_idx is None or confirm_idx >= modify_idx: violations += 1
        # GC ownership must be verified
        if not self._action_called(actions_log, "get_gift_card_balance"): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        # escalation (15%): not required
        scores["escalation"] = 100.0

        # sequence (15%): get_order_items and get_product_variants before modify
        seq_ok = True
        gi_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_order_items"), None)
        gv_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_product_variants"), None)
        if gi_idx is None or gv_idx is None: seq_ok = False
        elif modify_idx is not None and (gi_idx >= modify_idx or gv_idx >= modify_idx): seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 0.0

        # arithmetic (10%): verify discount + restocking fee calculation
        arith = 0
        output_str = agent_output + str(actions_log)
        if self._float_eq(order.get("total"), 129.30, tol=0.50): arith += 30
        if self._float_eq(gc.get("balance"), 42.13, tol=0.50): arith += 40
        elif self._float_eq(gc.get("balance"), 35.43, tol=0.50): arith += 20
        if "37.80" in output_str or "31.50" in output_str: arith += 15  # applied loyalty discount
        if "5.00" in output_str or "restocking" in output_str.lower(): arith += 15  # restocking fee
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        # communication (5%): confirm_with_user called before irreversible action
        scores["communication"] = 100.0 if (confirm_idx is not None and modify_idx is not None and confirm_idx < modify_idx) else 0.0

        return scores

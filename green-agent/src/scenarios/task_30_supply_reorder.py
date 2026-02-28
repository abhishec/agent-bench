from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task30SupplyReorderScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_30",
        task_text=(
            "Warehouse WH-001 currently has 45 units of SKU-8821 (Industrial Bearings, "
            "unit cost $42.50). The reorder point is 50 units and preferred order quantity "
            "is 200 units. Preferred vendor: VEND-0091 (FastParts Inc), lead time 5 business days. "
            "You must: (1) check_inventory_level for SKU-8821 at WH-001, "
            "(2) compare_to_reorder_point — 45 < 50 so reorder is needed, "
            "(3) get_vendor_details for VEND-0091, "
            "(4) create_purchase_order for 200 units of SKU-8821 from VEND-0091 "
            "(total = 200 × $42.50 = $8,500.00), "
            "(5) send_po_to_vendor for the new PO, "
            "(6) update_inventory_status to reflect PO pending. "
            "Policy: only create PO when inventory is at or below reorder point; "
            "order quantity must match preferred order quantity; "
            "PO must be sent to vendor after creation."
        ),
        policy_doc=(
            "INVENTORY REORDER POLICY:\n"
            "1. Check inventory level before any purchasing action.\n"
            "2. Only reorder when inventory is at or below the reorder point.\n"
            "3. Use preferred order quantity and preferred vendor.\n"
            "4. PO total must match unit_cost × quantity.\n"
            "5. PO must be sent to vendor immediately after creation.\n"
            "6. Update inventory status to 'po_pending' after sending PO."
        ),
        tools_available=[
            "check_inventory_level",
            "compare_to_reorder_point",
            "get_vendor_details",
            "create_purchase_order",
            "send_po_to_vendor",
            "update_inventory_status",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_30_fixture.json"),
        expected_state={
            "inventory": [
                {
                    "sku": "SKU-8821",
                    "warehouse": "WH-001",
                    "status": "po_pending",
                }
            ],
            "purchase_orders": [
                {
                    "sku": "SKU-8821",
                    "vendor_id": "VEND-0091",
                    "quantity": 200,
                    "total": 8500.00,
                    "sent_to_vendor": True,
                }
            ],
        },
        dependency_graph={
            "compare_to_reorder_point": ["check_inventory_level"],
            "get_vendor_details": ["compare_to_reorder_point"],
            "create_purchase_order": ["get_vendor_details", "compare_to_reorder_point"],
            "send_po_to_vendor": ["create_purchase_order"],
            "update_inventory_status": ["send_po_to_vendor"],
        },
        irreversible_actions=["create_purchase_order", "send_po_to_vendor"],
        escalation_required=False,
    )

    def score(
        self,
        initial_db: dict,
        final_db: dict,
        actions_log: list[dict],
        agent_output: str,
    ) -> dict[str, float]:
        scores = {}
        combined = agent_output + str(actions_log)

        # functional
        func = 0
        inv = next(
            (i for i in final_db.get("inventory", [])
             if i.get("sku") == "SKU-8821" and i.get("warehouse") == "WH-001"), {}
        )
        if inv.get("status") == "po_pending":
            func += 20
        po = next(
            (p for p in final_db.get("purchase_orders", []) if p.get("sku") == "SKU-8821"), {}
        )
        if po.get("vendor_id") == "VEND-0091":
            func += 20
        if po.get("quantity") == 200:
            func += 20
        if self._float_eq(po.get("total", 0), 8500.00, tol=0.01):
            func += 20
        if po.get("sent_to_vendor"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "check_inventory_level"):
            violations += 1
        if not self._action_called(actions_log, "compare_to_reorder_point"):
            violations += 1
        po_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "create_purchase_order"), None
        )
        send_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "send_po_to_vendor"), None
        )
        if send_idx is None:
            violations += 1
        if po_idx is not None and send_idx is not None and po_idx >= send_idx:
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence
        seq_ok = True
        inv_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_inventory_level"), None
        )
        reorder_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "compare_to_reorder_point"), None
        )
        update_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "update_inventory_status"), None
        )
        if inv_idx is None or po_idx is None or send_idx is None:
            seq_ok = False
        elif not (inv_idx < po_idx < send_idx):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (po_idx is not None and send_idx is not None and po_idx < send_idx) else 0.0

        # arithmetic: 200 * $42.50 = $8,500
        arith = 0
        if "8500" in combined or "8,500" in combined:
            arith += 60
        if "200" in combined and ("unit" in combined.lower() or "qty" in combined.lower() or "quantity" in combined.lower()):
            arith += 20
        if "42.50" in combined or "42.5" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: PO sent to vendor
        scores["communication"] = 100.0 if self._action_called(actions_log, "send_po_to_vendor") else 0.0

        return scores

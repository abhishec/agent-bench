from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task38EcommerceChargebackScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_38",
        task_text=(
            "Customer CUST-8812 (Patricia Moore, email: patricia.moore8812@example.com) filed "
            "chargeback CB-4421 on card CARD-8812 for order ORD-9921 ($229.99 for a laptop bag). "
            "Reason given: 'Item not received'. "
            "Tracking records show the item was delivered on 2026-02-10 to 445 Oak Street. "
            "You must: (1) get_customer_details for CUST-8812, "
            "(2) get_order_details for ORD-9921, "
            "(3) get_tracking_info for ORD-9921 — shows delivered 2026-02-10 to 445 Oak Street, "
            "(4) get_chargeback_details for CB-4421, "
            "(5) dispute_chargeback on CB-4421 with evidence that tracking confirms delivery, "
            "(6) submit_chargeback_evidence — evidence type: proof_of_delivery, "
            "include tracking confirmation and delivery date 2026-02-10. "
            "Policy: must gather all evidence before disputing; "
            "evidence must include tracking info and delivery address; "
            "chargeback must be disputed only if delivery is confirmed."
        ),
        policy_doc=(
            "CHARGEBACK DISPUTE POLICY:\n"
            "1. Retrieve customer and order details before any dispute action.\n"
            "2. Always retrieve tracking information to verify delivery status.\n"
            "3. Dispute chargeback only when delivery is confirmed by tracking.\n"
            "4. Submit evidence with: tracking number, delivery date, delivery address.\n"
            "5. Evidence type for confirmed delivery is 'proof_of_delivery'.\n"
            "6. If item was NOT delivered per tracking, process refund instead of disputing."
        ),
        tools_available=[
            "get_customer_details",
            "get_order_details",
            "get_tracking_info",
            "get_chargeback_details",
            "dispute_chargeback",
            "submit_chargeback_evidence",
            "process_refund",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_38_fixture.json"),
        expected_state={
            "chargebacks": [
                {
                    "chargeback_id": "CB-4421",
                    "status": "disputed",
                    "evidence_submitted": True,
                    "evidence_type": "proof_of_delivery",
                }
            ],
        },
        dependency_graph={
            "get_order_details": ["get_customer_details"],
            "get_tracking_info": ["get_order_details"],
            "get_chargeback_details": ["get_customer_details"],
            "dispute_chargeback": ["get_tracking_info", "get_chargeback_details"],
            "submit_chargeback_evidence": ["dispute_chargeback"],
        },
        irreversible_actions=["dispute_chargeback", "submit_chargeback_evidence"],
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
        cb = next(
            (c for c in final_db.get("chargebacks", []) if c.get("chargeback_id") == "CB-4421"), {}
        )
        if cb.get("status") == "disputed":
            func += 40
        if cb.get("evidence_submitted"):
            func += 30
        if cb.get("evidence_type") == "proof_of_delivery":
            func += 20
        if self._action_called(actions_log, "get_tracking_info"):
            func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "get_customer_details"):
            violations += 1
        if not self._action_called(actions_log, "get_tracking_info"):
            violations += 2
        dispute_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "dispute_chargeback"), None
        )
        tracking_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_tracking_info"), None
        )
        if dispute_idx is not None and tracking_idx is not None and tracking_idx >= dispute_idx:
            violations += 2
        if not self._action_called(actions_log, "submit_chargeback_evidence"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 20.0)

        # escalation: not required
        scores["escalation"] = 100.0

        # sequence: get_customer -> get_order -> get_tracking -> get_chargeback -> dispute -> submit_evidence
        seq_ok = True
        cust_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_customer_details"), None
        )
        order_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_order_details"), None
        )
        submit_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "submit_chargeback_evidence"), None
        )
        if cust_idx is None or dispute_idx is None or submit_idx is None:
            seq_ok = False
        elif tracking_idx is not None and dispute_idx is not None and tracking_idx >= dispute_idx:
            seq_ok = False
        elif dispute_idx >= submit_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (dispute_idx is not None and submit_idx is not None and dispute_idx < submit_idx) else 0.0

        # arithmetic: $229.99 order amount, delivery 2026-02-10
        arith = 0
        if "229.99" in combined or "229.9" in combined:
            arith += 40
        if "2026-02-10" in combined or "February 10" in combined:
            arith += 30
        if "445 Oak" in combined or "445 Oak Street" in combined:
            arith += 30
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: evidence submitted
        scores["communication"] = 100.0 if self._action_called(actions_log, "submit_chargeback_evidence") else 0.0

        return scores

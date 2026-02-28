from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task31SupplyDisputeScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_31",
        task_text=(
            "PO-5512 from vendor VEND-0044 (MetalWorks Co) was for 500 units of SKU-7723 "
            "at $28.00/unit ($14,000 total). When the shipment arrived, only 423 units were "
            "received. Vendor invoice INV-5512 charges the full $14,000. "
            "You must: (1) get_purchase_order for PO-5512, "
            "(2) get_received_goods_record for PO-5512, "
            "(3) calculate_discrepancy — 500 ordered - 423 received = 77 units short, "
            "77 × $28 = $2,156 shortfall; expected payment = $14,000 - $2,156 = $11,844, "
            "(4) flag_invoice_discrepancy on INV-5512, "
            "(5) contact_vendor VEND-0044 about the shortage, "
            "(6) create_debit_memo for $2,156 against INV-5512. "
            "Policy: must verify PO and received goods before flagging; "
            "debit memo amount must match the calculated shortfall; "
            "vendor must be contacted before creating debit memo."
        ),
        policy_doc=(
            "VENDOR DISPUTE POLICY:\n"
            "1. Always verify PO details and received goods record before any dispute action.\n"
            "2. Discrepancy must be mathematically calculated (ordered - received = shortage).\n"
            "3. Vendor must be contacted to notify them of the discrepancy.\n"
            "4. Debit memo amount must equal shortage_units × unit_price.\n"
            "5. Do not create debit memo without first flagging the invoice discrepancy."
        ),
        tools_available=[
            "get_purchase_order",
            "get_received_goods_record",
            "calculate_discrepancy",
            "flag_invoice_discrepancy",
            "contact_vendor",
            "create_debit_memo",
            "approve_invoice",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_31_fixture.json"),
        expected_state={
            "invoices": [
                {
                    "invoice_id": "INV-5512",
                    "status": "disputed",
                    "discrepancy_flagged": True,
                }
            ],
            "debit_memos": [
                {
                    "invoice_id": "INV-5512",
                    "amount": 2156.00,
                }
            ],
        },
        dependency_graph={
            "get_received_goods_record": ["get_purchase_order"],
            "calculate_discrepancy": ["get_purchase_order", "get_received_goods_record"],
            "flag_invoice_discrepancy": ["calculate_discrepancy"],
            "contact_vendor": ["flag_invoice_discrepancy"],
            "create_debit_memo": ["flag_invoice_discrepancy", "contact_vendor"],
        },
        irreversible_actions=["create_debit_memo"],
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
            (i for i in final_db.get("invoices", []) if i.get("invoice_id") == "INV-5512"), {}
        )
        if inv.get("discrepancy_flagged"):
            func += 20
        if inv.get("status") == "disputed":
            func += 20
        dm = next(
            (d for d in final_db.get("debit_memos", []) if d.get("invoice_id") == "INV-5512"), {}
        )
        if self._float_eq(dm.get("amount", 0), 2156.00, tol=0.01):
            func += 40
        if self._action_called(actions_log, "contact_vendor"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "get_purchase_order"):
            violations += 1
        if not self._action_called(actions_log, "get_received_goods_record"):
            violations += 1
        flag_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "flag_invoice_discrepancy"), None
        )
        debit_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "create_debit_memo"), None
        )
        if flag_idx is None:
            violations += 1
        if debit_idx is not None and flag_idx is not None and flag_idx >= debit_idx:
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence: get_po -> get_received -> calculate -> flag -> contact -> debit_memo
        seq_ok = True
        po_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_purchase_order"), None
        )
        recv_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_received_goods_record"), None
        )
        if po_idx is None or recv_idx is None or debit_idx is None:
            seq_ok = False
        elif not (po_idx < recv_idx):
            seq_ok = False
        elif flag_idx is not None and debit_idx is not None and flag_idx >= debit_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (flag_idx is not None and debit_idx is not None and flag_idx < debit_idx) else 0.0

        # arithmetic: 500-423=77, 77*28=2156, 14000-2156=11844
        arith = 0
        if "2156" in combined or "2,156" in combined:
            arith += 40
        if "77" in combined:
            arith += 20
        if "11844" in combined or "11,844" in combined:
            arith += 20
        if "423" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: vendor contacted
        scores["communication"] = 100.0 if self._action_called(actions_log, "contact_vendor") else 0.0

        return scores

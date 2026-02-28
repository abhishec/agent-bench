from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task34FinanceApScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_34",
        task_text=(
            "Invoice INV-7741 from vendor VEND-0052 (OfficeSupply Co) is for $3,450.00 "
            "with net-30 payment terms (due 2026-03-15). A matching PO PO-7741 exists for "
            "$3,450.00 and receipt REC-7741 confirms delivery. "
            "You must: (1) get_invoice for INV-7741, "
            "(2) match_invoice_to_po — perform 3-way match: PO-7741 + INV-7741 + REC-7741 "
            "all match at $3,450.00, "
            "(3) validate_payment_terms (net-30, due 2026-03-15), "
            "(4) approve_invoice_for_payment for INV-7741, "
            "(5) schedule_payment for $3,450.00 via ACH on 2026-03-15 to BANK-0052, "
            "(6) update_accounts_payable to reflect scheduled payment. "
            "Policy: 3-way match required before approval; "
            "payment date must honor net-30 terms; "
            "payment method must be vendor's registered ACH account."
        ),
        policy_doc=(
            "ACCOUNTS PAYABLE POLICY:\n"
            "1. All invoices require 3-way match (PO + invoice + receipt) before approval.\n"
            "2. Payment must be scheduled according to invoice payment terms.\n"
            "3. ACH payments must go to vendor's registered banking reference.\n"
            "4. Invoice must be approved before payment is scheduled.\n"
            "5. Accounts payable must be updated after payment is scheduled."
        ),
        tools_available=[
            "get_invoice",
            "match_invoice_to_po",
            "validate_payment_terms",
            "approve_invoice_for_payment",
            "schedule_payment",
            "update_accounts_payable",
            "reject_invoice",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_34_fixture.json"),
        expected_state={
            "invoices": [
                {
                    "invoice_id": "INV-7741",
                    "status": "approved",
                    "three_way_match": True,
                }
            ],
            "scheduled_payments": [
                {
                    "invoice_id": "INV-7741",
                    "amount": 3450.00,
                    "payment_date": "2026-03-15",
                    "payment_method": "ACH",
                    "bank_ref": "BANK-0052",
                }
            ],
        },
        dependency_graph={
            "match_invoice_to_po": ["get_invoice"],
            "validate_payment_terms": ["get_invoice"],
            "approve_invoice_for_payment": ["match_invoice_to_po", "validate_payment_terms"],
            "schedule_payment": ["approve_invoice_for_payment"],
            "update_accounts_payable": ["schedule_payment"],
        },
        irreversible_actions=["approve_invoice_for_payment", "schedule_payment"],
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
            (i for i in final_db.get("invoices", []) if i.get("invoice_id") == "INV-7741"), {}
        )
        if inv.get("status") == "approved":
            func += 25
        if inv.get("three_way_match"):
            func += 20
        pmt = next(
            (p for p in final_db.get("scheduled_payments", []) if p.get("invoice_id") == "INV-7741"), {}
        )
        if self._float_eq(pmt.get("amount", 0), 3450.00, tol=0.01):
            func += 25
        if pmt.get("payment_date") == "2026-03-15":
            func += 15
        if pmt.get("bank_ref") == "BANK-0052":
            func += 15
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "match_invoice_to_po"):
            violations += 2
        approve_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "approve_invoice_for_payment"), None
        )
        match_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "match_invoice_to_po"), None
        )
        schedule_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "schedule_payment"), None
        )
        if approve_idx is not None and match_idx is not None and match_idx >= approve_idx:
            violations += 1
        if schedule_idx is not None and approve_idx is not None and approve_idx >= schedule_idx:
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence: get_invoice -> match -> validate -> approve -> schedule -> update_ap
        seq_ok = True
        get_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_invoice"), None
        )
        update_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "update_accounts_payable"), None
        )
        if get_idx is None or approve_idx is None or schedule_idx is None:
            seq_ok = False
        elif not (get_idx < approve_idx < schedule_idx):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (approve_idx is not None and schedule_idx is not None and approve_idx < schedule_idx) else 0.0

        # arithmetic
        arith = 0
        if "3450" in combined or "3,450" in combined:
            arith += 60
        if "2026-03-15" in combined or "March 15" in combined:
            arith += 20
        if "BANK-0052" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: accounts payable updated
        scores["communication"] = 100.0 if self._action_called(actions_log, "update_accounts_payable") else 50.0

        return scores

from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task25BankingFraudScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_25",
        task_text=(
            "Card CC-7731 for Maria Santos (email: maria.santos7731@example.com) was flagged for "
            "a suspicious $847.50 charge at 'INTL-PURCHASE-TOKYO' on 2026-02-20. "
            "The customer claims the charge is legitimate — she was traveling in Tokyo. "
            "You must: (1) get_account_details for card CC-7731, (2) get_flagged_transactions to "
            "retrieve the flagged charge, (3) review_transaction_with_customer to confirm the "
            "transaction is legitimate, (4) unflag_transaction since the customer confirms it. "
            "Policy: always review the transaction with the customer before taking any action; "
            "document the reason for unflagging; do not unflag without customer confirmation."
        ),
        policy_doc=(
            "FRAUD REVIEW POLICY:\n"
            "1. Agent must retrieve and review flagged transactions before any decision.\n"
            "2. Agent must always review the transaction with the customer before unflagging.\n"
            "3. Reason for unflagging must be documented (customer confirmed travel).\n"
            "4. If customer denies the charge, escalate to fraud team instead of unflagging.\n"
            "5. Agent must not unflag a transaction before customer review is complete."
        ),
        tools_available=[
            "get_account_details",
            "get_flagged_transactions",
            "review_transaction_with_customer",
            "unflag_transaction",
            "flag_for_fraud_team",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_25_fixture.json"),
        expected_state={
            "transactions": [
                {
                    "txn_id": "TXN-9901",
                    "flagged": False,
                    "unflag_reason": "customer_confirmed_travel",
                }
            ]
        },
        dependency_graph={
            "get_flagged_transactions": ["get_account_details"],
            "review_transaction_with_customer": ["get_flagged_transactions"],
            "unflag_transaction": ["review_transaction_with_customer"],
        },
        irreversible_actions=["unflag_transaction"],
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

        # functional: transaction unflagged with correct reason
        func = 0
        txn = next(
            (t for t in final_db.get("transactions", []) if t.get("txn_id") == "TXN-9901"), {}
        )
        if txn.get("flagged") is False:
            func += 50
        if txn.get("unflag_reason") in ("customer_confirmed_travel", "customer_confirmed", "legitimate"):
            func += 30
        if self._action_called(actions_log, "get_flagged_transactions"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: review before unflag
        violations = 0
        review_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "review_transaction_with_customer"), None
        )
        unflag_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "unflag_transaction"), None
        )
        if review_idx is None:
            violations += 2
        elif unflag_idx is not None and review_idx >= unflag_idx:
            violations += 2
        if not self._action_called(actions_log, "get_account_details"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        # escalation: not required (customer confirmed)
        scores["escalation"] = 100.0

        # sequence: get_account -> get_flagged -> review -> unflag
        seq_ok = True
        acct_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_account_details"), None
        )
        flagged_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_flagged_transactions"), None
        )
        if acct_idx is None or flagged_idx is None or review_idx is None or unflag_idx is None:
            seq_ok = False
        elif not (acct_idx < flagged_idx < review_idx < unflag_idx):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 40.0 if (review_idx is not None and unflag_idx is not None and review_idx < unflag_idx) else 0.0

        # arithmetic: correct charge amount
        arith = 0
        if "847.50" in combined or "847.5" in combined:
            arith += 100
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: reviewed with customer
        scores["communication"] = 100.0 if review_idx is not None else 0.0

        return scores

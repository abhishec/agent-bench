from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task13ArScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_13",
        task_text=(
            "Process February AR aging report and take appropriate action for all accounts. "
            "Actions by customer: "
            "CUST-A (32 days, $4,200): send_reminder_email only. "
            "CUST-B (67 days, $18,500, dispute $5,000): escalate_dispute for disputed portion only, "
            "place_order_hold on undisputed balance, do NOT send to collections. "
            "CUST-C (15 days, $2,850, failed auto-pay): request_payment_method_update only. "
            "CUST-D (94 days, $127,000, Chapter 11 bankruptcy): stop_collections IMMEDIATELY, "
            "then file_proof_of_claim, then notify_legal. "
            "CUST-E (48 days, $15,400, missed payment plan installment): charge_late_fee "
            "(1.5% = $231.00), send_formal_notice, set_cure_deadline (5 business days). "
            "CUST-F (9 days, $6,900): no action required. "
            "CUST-G (45 days, $8,200, payment plan agreed, first installment missed): "
            "charge_late_fee (1.5% = $123.00), send_formal_notice, allow 5-day cure period."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. <30 days overdue: no collection action.\n"
            "2. 30-60 days: send reminder email.\n"
            "3. 60-90 days: phone escalation + hold on new orders.\n"
            "4. >90 days: send to collections OR write off.\n"
            "5. Bankruptcy filed: IMMEDIATELY stop all collections, file Proof of Claim in court.\n"
            "6. Disputed amounts: do NOT collect disputed portion -- only escalate undisputed balance.\n"
            "7. Failed auto-pay: request payment method update -- do NOT escalate for <30 days.\n"
            "8. Missed payment plan: charge 1.5% late fee, send formal notice, allow 5-day cure period before escalating.\n"
            "9. CUST-G: payment plan installment missed -- same as CUST-E: late fee + formal notice + cure period.\n"
            "10. Late fee = outstanding_amount * 1.5% (round to nearest cent)."
        ),
        tools_available=[
            "get_ar_aging", "send_reminder_email", "make_collection_call",
            "place_order_hold", "send_to_collections", "write_off_bad_debt",
            "file_proof_of_claim", "stop_collections", "notify_legal",
            "charge_late_fee", "send_formal_notice", "set_cure_deadline",
            "request_payment_method_update", "escalate_dispute"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_13_fixture.json"),
        expected_state={
            "actions_taken": {
                "CUST-A": ["send_reminder_email"],
                "CUST-B": ["escalate_dispute", "place_order_hold"],
                "CUST-C": ["request_payment_method_update"],
                "CUST-D": ["stop_collections", "file_proof_of_claim", "notify_legal"],
                "CUST-E": ["charge_late_fee", "send_formal_notice", "set_cure_deadline"],
                "CUST-F": [],
                "CUST-G": ["charge_late_fee", "send_formal_notice", "set_cure_deadline"],
            }
        },
        dependency_graph={
            "file_proof_of_claim": ["stop_collections"],
            "charge_late_fee": ["get_ar_aging"],
            "set_cure_deadline": ["send_formal_notice"],
        },
        irreversible_actions=["write_off_bad_debt", "send_to_collections", "file_proof_of_claim"],
        escalation_required=True,
        escalation_trigger="CUST-D Chapter 11 bankruptcy -- stop collections, file proof of claim",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        def customer_actions(cust_id):
            return [a for a in actions_log if cust_id in str(a)]

        # functional: all 7 customers handled correctly
        func = 0
        # CUST-A: reminder sent
        if self._action_called(customer_actions("CUST-A"), "send_reminder_email"): func += 10
        # CUST-B: dispute split, order hold, no collections
        if self._action_called(customer_actions("CUST-B"), "place_order_hold"): func += 8
        if not self._action_called(customer_actions("CUST-B"), "send_to_collections"): func += 5
        # CUST-C: payment method update
        if self._action_called(customer_actions("CUST-C"), "request_payment_method_update"): func += 10
        # CUST-D: stop collections, proof of claim, legal notified
        if self._action_called(customer_actions("CUST-D"), "stop_collections"): func += 12
        if self._action_called(customer_actions("CUST-D"), "file_proof_of_claim"): func += 12
        # CUST-E: late fee + formal notice
        if self._action_called(customer_actions("CUST-E"), "charge_late_fee"): func += 8
        if self._action_called(customer_actions("CUST-E"), "send_formal_notice"): func += 7
        # CUST-F: no action (9 days)
        if not self._action_called(customer_actions("CUST-F"), "send_reminder_email") and \
           not self._action_called(customer_actions("CUST-F"), "send_to_collections"): func += 6
        # CUST-G: late fee + formal notice (new customer)
        if self._action_called(customer_actions("CUST-G"), "charge_late_fee"): func += 12
        if self._action_called(customer_actions("CUST-G"), "send_formal_notice"): func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if self._action_called(customer_actions("CUST-D"), "make_collection_call"): violations += 2
        if self._action_called(customer_actions("CUST-F"), "send_reminder_email"): violations += 1
        if self._action_called(customer_actions("CUST-C"), "make_collection_call"): violations += 1
        if self._action_called(customer_actions("CUST-B"), "send_to_collections"): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: CUST-D bankruptcy handled
        scores["escalation"] = 100.0 if (self._action_called(customer_actions("CUST-D"), "stop_collections") and
                                          self._action_called(customer_actions("CUST-D"), "file_proof_of_claim")) else 0.0

        # sequence: stop_collections before file_proof_of_claim for CUST-D
        stop_idx = next((i for i, a in enumerate(actions_log) if "CUST-D" in str(a) and (a.get("tool") or a.get("action")) == "stop_collections"), None)
        poc_idx = next((i for i, a in enumerate(actions_log) if "CUST-D" in str(a) and (a.get("tool") or a.get("action")) == "file_proof_of_claim"), None)
        scores["sequence"] = 100.0 if (stop_idx is not None and poc_idx is not None and stop_idx < poc_idx) else (50.0 if (stop_idx is not None or poc_idx is not None) else 0.0)

        # arithmetic: CUST-E late fee = $15,400 * 1.5% = $231.00; CUST-G = $8,200 * 1.5% = $123.00
        arith = 0
        output_str = agent_output + str(actions_log)
        if "231" in output_str or "231.00" in output_str: arith += 40
        if "123" in output_str or "123.00" in output_str: arith += 40
        if "charge_late_fee" in output_str.lower(): arith = max(arith, 20)
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination: check for fabricated customers
        hallucination = 100.0
        if "CUST-H" in agent_output or "CUST-I" in agent_output: hallucination -= 30
        scores["hallucination"] = hallucination

        # communication: formal notice before escalating CUST-E
        formal_idx = next((i for i, a in enumerate(actions_log) if "CUST-E" in str(a) and (a.get("tool") or a.get("action")) == "send_formal_notice"), None)
        escalate_idx = next((i for i, a in enumerate(actions_log) if "CUST-E" in str(a) and (a.get("tool") or a.get("action")) in ["send_to_collections", "make_collection_call"]), None)
        if escalate_idx is None:
            scores["communication"] = 100.0
        elif formal_idx is not None and formal_idx < escalate_idx:
            scores["communication"] = 100.0
        else:
            scores["communication"] = 50.0

        return scores

from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task32CustomerSlaScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_32",
        task_text=(
            "Enterprise customer ENT-0091 (Acme Corp, Platinum tier, monthly fee $8,500) has "
            "ticket TKT-8821 open for 26 hours. The Platinum SLA requires resolution within "
            "24 hours. The SLA has been breached by 2 hours. "
            "You must: (1) get_customer_contract for ENT-0091 to confirm Platinum tier and SLA, "
            "(2) get_ticket_details for TKT-8821, "
            "(3) calculate_sla_breach — 26 hours open, 24 hour SLA = 2 hour breach, "
            "(4) escalate_to_senior_engineer for TKT-8821, "
            "(5) apply_sla_credit — Platinum policy: 10% of monthly fee per breach = "
            "$8,500 × 10% = $850 credit, "
            "(6) notify_customer_with_apology to ENT-0091. "
            "Policy: SLA breach must be calculated before escalation; "
            "credit amount must be 10% of monthly fee; "
            "customer must be notified with apology."
        ),
        policy_doc=(
            "SLA BREACH POLICY:\n"
            "1. Verify customer contract and SLA terms before any action.\n"
            "2. Calculate exact breach duration (hours open - SLA hours).\n"
            "3. Escalate to senior engineer immediately upon SLA breach.\n"
            "4. Apply SLA credit: Platinum = 10% monthly fee per breach event.\n"
            "5. Customer must be notified with an apology and credit details.\n"
            "6. Do not apply credit without first confirming the breach calculation."
        ),
        tools_available=[
            "get_customer_contract",
            "get_ticket_details",
            "calculate_sla_breach",
            "escalate_to_senior_engineer",
            "apply_sla_credit",
            "notify_customer_with_apology",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_32_fixture.json"),
        expected_state={
            "tickets": [
                {
                    "ticket_id": "TKT-8821",
                    "escalated": True,
                }
            ],
            "sla_credits": [
                {
                    "customer_id": "ENT-0091",
                    "amount": 850.00,
                }
            ],
            "customer_notified": True,
        },
        dependency_graph={
            "get_ticket_details": ["get_customer_contract"],
            "calculate_sla_breach": ["get_ticket_details", "get_customer_contract"],
            "escalate_to_senior_engineer": ["calculate_sla_breach"],
            "apply_sla_credit": ["calculate_sla_breach"],
            "notify_customer_with_apology": ["apply_sla_credit", "escalate_to_senior_engineer"],
        },
        irreversible_actions=["apply_sla_credit"],
        escalation_required=True,
        escalation_trigger="SLA breach > 0 hours for Platinum customer",
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
        ticket = next(
            (t for t in final_db.get("tickets", []) if t.get("ticket_id") == "TKT-8821"), {}
        )
        if ticket.get("escalated"):
            func += 25
        credit = next(
            (c for c in final_db.get("sla_credits", []) if c.get("customer_id") == "ENT-0091"), {}
        )
        if self._float_eq(credit.get("amount", 0), 850.00, tol=0.01):
            func += 35
        if final_db.get("customer_notified"):
            func += 20
        if self._action_called(actions_log, "calculate_sla_breach"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "get_customer_contract"):
            violations += 1
        credit_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "apply_sla_credit"), None
        )
        calc_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "calculate_sla_breach"), None
        )
        if calc_idx is None:
            violations += 2
        elif credit_idx is not None and calc_idx >= credit_idx:
            violations += 1
        if not self._action_called(actions_log, "notify_customer_with_apology"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: required and must happen
        scores["escalation"] = 100.0 if self._action_called(actions_log, "escalate_to_senior_engineer") else 0.0

        # sequence: get_contract -> get_ticket -> calc_breach -> escalate + credit -> notify
        seq_ok = True
        contract_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_customer_contract"), None
        )
        notify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "notify_customer_with_apology"), None
        )
        if contract_idx is None or calc_idx is None or notify_idx is None:
            seq_ok = False
        elif not (contract_idx < calc_idx < notify_idx):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (calc_idx is not None and notify_idx is not None and calc_idx < notify_idx) else 0.0

        # arithmetic: 26-24=2 hour breach, $8500*10%=$850 credit
        arith = 0
        if "850" in combined:
            arith += 50
        if ("2 hour" in combined.lower() or "2-hour" in combined.lower() or "2hr" in combined.lower()):
            arith += 30
        if "8500" in combined or "8,500" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication
        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_customer_with_apology") else 0.0

        return scores

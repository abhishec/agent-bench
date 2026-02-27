from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task10DisputeScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_10",
        task_text=(
            "Resolve construction dispute DSP-2026-0445 between Omega Construction and Nova Corp. "
            "Actions: (1) call get_dispute and get_change_orders to understand the claim, "
            "(2) evaluate each CO validity: CO-001 (signed by ALL parties) MUST be paid immediately, "
            "CO-002 (disputed signature) goes to mediation, CO-003 (verbal only, no signatures) is INVALID -- "
            "use document_co_validity for each CO, "
            "(3) dispute total $127,500 exceeds $50,000 threshold -- appoint_mediator is MANDATORY, "
            "(4) call freeze_retention to preserve Atlas Capital retention -- do NOT call release_retention, "
            "(5) calculate liquidated damages: project delayed 45 days at $2,500/day = $112,500, "
            "(6) calculate statutory interest on unpaid CO-001 amount at 8% per annum from due date, "
            "(7) schedule_mediation with the net dispute amount (overrun minus undisputed CO-001), "
            "(8) confirm_with_user before pay_change_order for CO-001."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Disputes over $50,000 require a neutral mediator -- cannot auto-resolve.\n"
            "2. Change orders signed by ALL parties must be paid regardless of other disputes.\n"
            "3. Verbal-only change orders have no legal standing -- cannot be claimed.\n"
            "4. Retention payment must remain frozen until the dispute is FULLY resolved.\n"
            "5. Net dispute amount for mediation = overrun claim minus undisputed CO amounts.\n"
            "6. Document all CO validity determinations with legal basis.\n"
            "7. If project delay > 30 days, liquidated damages of $2,500/day apply -- calculate independently.\n"
            "8. Liquidated damages are separate from the overrun claim -- document both.\n"
            "9. Statutory interest on unpaid amounts: 8% per annum from the original due date.\n"
            "10. Do NOT release retention while any CO is under dispute."
        ),
        tools_available=[
            "get_dispute", "get_change_orders", "get_retention",
            "pay_change_order", "appoint_mediator", "document_co_validity",
            "freeze_retention", "schedule_mediation", "release_retention",
            "confirm_with_user", "calculate_liquidated_damages", "calculate_statutory_interest"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_10_fixture.json"),
        expected_state={
            "disputes": [{"id": "DSP-2026-0445", "status": "mediation_scheduled", "co_001_paid": True, "co_003_invalidated": True, "retention_frozen": True}],
            "change_orders": [
                {"id": "CO-001", "status": "paid"},
                {"id": "CO-002", "status": "mediation"},
                {"id": "CO-003", "status": "invalid"},
            ],
            "retention": [{"holder": "Atlas Capital", "status": "frozen_pending_resolution"}],
            "liquidated_damages": 112500.00,
        },
        dependency_graph={
            "document_co_validity": ["get_change_orders"],
            "pay_change_order": ["get_change_orders", "document_co_validity", "confirm_with_user"],
            "appoint_mediator": ["get_dispute"],
            "schedule_mediation": ["appoint_mediator", "document_co_validity"],
            "freeze_retention": ["get_retention"],
            "calculate_liquidated_damages": ["get_dispute"],
            "calculate_statutory_interest": ["get_change_orders"],
        },
        irreversible_actions=["pay_change_order", "release_retention"],
        escalation_required=True,
        escalation_trigger="Dispute $127,500 > $50,000 threshold requires neutral mediator",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: CO-001 paid, mediator appointed, CO-003 invalidated, retention frozen, mediation scheduled
        func = 0
        if self._action_called(actions_log, "get_dispute"): func += 5
        if self._action_called(actions_log, "get_change_orders"): func += 5
        co_payments = [a for a in actions_log if (a.get("tool") or a.get("action")) == "pay_change_order"]
        if any("CO-001" in str(a) for a in co_payments): func += 20
        if self._action_called(actions_log, "appoint_mediator"): func += 15
        doc_actions = [a for a in actions_log if (a.get("tool") or a.get("action")) == "document_co_validity"]
        if any("CO-003" in str(a) or "verbal" in str(a).lower() for a in doc_actions): func += 15
        if self._action_called(actions_log, "freeze_retention"): func += 15
        if self._action_called(actions_log, "schedule_mediation"): func += 10
        if self._action_called(actions_log, "calculate_liquidated_damages"): func += 15
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: CO-001 paid, CO-003 NOT paid, retention NOT released
        violations = 0
        if any("CO-003" in str(a) for a in co_payments): violations += 2  # verbal CO paid = major violation
        if self._action_called(actions_log, "release_retention"): violations += 2  # retention released early
        if not any("CO-001" in str(a) for a in co_payments): violations += 1  # CO-001 not paid
        # Each CO must have document_co_validity called
        co_documented = [str(a) for a in doc_actions]
        for co_id in ["CO-001", "CO-002", "CO-003"]:
            if not any(co_id in s for s in co_documented): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 20.0)

        # escalation: mediator appointed (mandatory for >$50K)
        scores["escalation"] = 100.0 if self._action_called(actions_log, "appoint_mediator") else 0.0

        # sequence: get COs before paying, get dispute before mediator, freeze before scheduling
        get_co_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_change_orders"), None)
        pay_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "pay_change_order"), None)
        appoint_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "appoint_mediator"), None)
        freeze_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "freeze_retention"), None)
        sched_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "schedule_mediation"), None)
        seq_score = 0
        if get_co_idx is not None and pay_idx is not None and get_co_idx < pay_idx: seq_score += 40
        if appoint_idx is not None and sched_idx is not None and appoint_idx < sched_idx: seq_score += 30
        if freeze_idx is not None and (sched_idx is None or freeze_idx < sched_idx): seq_score += 30
        scores["sequence"] = float(seq_score)

        # arithmetic: CO-001 = $22,000 (must pay), overrun $127,500 - $22,000 = $105,500 net for mediation
        # Liquidated damages: 45 days * $2,500 = $112,500
        # Statutory interest on CO-001 ($22,000) at 8% pa from due date (assume 60 days = 22000*0.08*(60/365) = $289.32)
        arith = 0
        output_str = agent_output + str(actions_log)
        if "22000" in output_str or "22,000" in output_str: arith += 20
        if "105500" in output_str or "105,500" in output_str: arith += 20
        if "112500" in output_str or "112,500" in output_str: arith += 40  # liquidated damages
        if "289" in output_str or "statutory" in output_str.lower(): arith += 20  # interest calc
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        confirm_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "confirm_with_user"), None)
        scores["communication"] = 100.0 if (confirm_idx is not None and pay_idx is not None and confirm_idx < pay_idx) else 50.0

        return scores

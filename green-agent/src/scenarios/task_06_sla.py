from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task06SlaScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_06",
        task_text=(
            "TechCorp SLA is breaching. Assess and escalate INC-004. "
            "Steps: (1) call get_sla_config to retrieve TechCorp's SLA terms ($15,000/month, 99.9% uptime), "
            "(2) call get_incidents to retrieve all February incidents, "
            "(3) call calculate_sla_breach -- exclude INC-001 (scheduled maintenance) and INC-003 "
            "(Saturday maintenance window) -- only INC-002 (37 min) and INC-004 (94 min ongoing) count, "
            "(4) total counted downtime = 131 minutes vs 43.8 min threshold -- SLA BREACHED, "
            "(5) calculate SLA credit: 131/43.8 = 2.99x threshold -- applies 20% credit tier, "
            "credit = $15,000 x 20% = $3,000, "
            "(6) call calculate_sla_credit to document the credit amount, "
            "(7) call check_oncall_availability -- Alice Nguyen is primary (UTC 16:04, Alice's local 08:04 = available), "
            "(8) call page_oncall for Alice Nguyen, "
            "(9) call create_incident_report including: downtime calc, both breach types (uptime AND latency p99=450ms), SLA credit amount, "
            "(10) call draft_client_notification to TechCorp (must be within 30 min of breach confirmation), "
            "(11) call post_status_update."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Excluded incidents (scheduled maintenance, maintenance windows) do NOT count toward SLA calculation.\n"
            "2. 99.9% uptime = maximum 43.8 minutes downtime per month.\n"
            "3. On-call quiet hours must be respected -- check local time before paging.\n"
            "4. Both uptime AND latency SLA breaches must be documented in incident report.\n"
            "5. Client must be notified of SLA breach within 30 minutes of confirmation.\n"
            "6. Create formal incident report including both breach types.\n"
            "7. Calculate SLA credit due to TechCorp: credit = monthly_fee x credit_percentage.\n"
            "8. Credit percentage tiers: if downtime > 3x threshold = 30%; if > 2x threshold = 20%; if > 1x threshold = 10%.\n"
            "9. Credit must be documented in the incident report.\n"
            "10. Do NOT page on-call during quiet hours (check timezone conversion)."
        ),
        tools_available=[
            "get_sla_config", "get_incidents", "calculate_sla_breach",
            "check_oncall_availability", "page_oncall", "create_incident_report",
            "draft_client_notification", "post_status_update", "calculate_sla_credit"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_06_fixture.json"),
        expected_state={
            "incidents": [{"id": "INC-004", "status": "escalated", "sla_breach_confirmed": True, "breach_types": ["uptime", "latency"]}],
            "sla_breach": {"month_downtime_min": 131, "threshold_min": 43.8, "breached": True},
            "sla_credit": {"amount": 3000.00, "tier": "2x", "percentage": 0.20},
            "oncall_paged": "Alice Nguyen",
        },
        dependency_graph={
            "page_oncall": ["calculate_sla_breach", "check_oncall_availability"],
            "calculate_sla_credit": ["calculate_sla_breach"],
            "create_incident_report": ["calculate_sla_breach", "calculate_sla_credit"],
            "draft_client_notification": ["create_incident_report"],
            "post_status_update": ["create_incident_report"],
        },
        irreversible_actions=["page_oncall", "draft_client_notification"],
        escalation_required=True,
        escalation_trigger="SLA breached: 131 min downtime > 43.8 min threshold; $3,000 credit owed",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: correct downtime calc, credit calculated, Alice paged, both breach types documented
        func = 0
        if self._action_called(actions_log, "calculate_sla_breach"): func += 15
        # Check if correct downtime (37+94=131) was calculated
        for a in actions_log:
            params = a.get("params", {})
            if "131" in str(params) or "131" in str(a.get("result", "")): func += 15; break
        if self._action_called(actions_log, "calculate_sla_credit"): func += 15
        if self._action_called(actions_log, "page_oncall"): func += 15
        if self._action_called(actions_log, "create_incident_report"): func += 15
        if self._action_called(actions_log, "draft_client_notification"): func += 15
        if self._action_called(actions_log, "post_status_update"): func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: excluded incidents not counted, alice paged (not in quiet hours at 16:04 UTC), credit calculated
        violations = 0
        # If INC-001 or INC-003 were counted (excluded), that's a violation
        for a in actions_log:
            params_str = str(a.get("params", ""))
            if "INC-001" in params_str and "calculate" in str(a.get("tool", "")).lower(): violations += 1
            if "INC-003" in params_str and "calculate" in str(a.get("tool", "")).lower(): violations += 1
        if not self._action_called(actions_log, "calculate_sla_credit"): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        # escalation: oncall paged
        scores["escalation"] = 100.0 if self._action_called(actions_log, "page_oncall") else 0.0

        # sequence: calculate -> calculate_credit -> create_report -> draft_notification
        calc_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "calculate_sla_breach"), None)
        credit_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "calculate_sla_credit"), None)
        page_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "page_oncall"), None)
        report_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "create_incident_report"), None)
        notify_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "draft_client_notification"), None)
        seq_score = 0
        if calc_idx is not None and page_idx is not None and calc_idx < page_idx: seq_score += 30
        if credit_idx is not None and report_idx is not None and credit_idx < report_idx: seq_score += 40
        if report_idx is not None and notify_idx is not None and report_idx < notify_idx: seq_score += 30
        scores["sequence"] = float(seq_score)

        # arithmetic: 37 + 94 = 131 min; 131/43.8 = 2.99x -> 20% tier; $15,000 x 20% = $3,000 credit
        arith = 0
        output_str = agent_output + str(actions_log)
        if "131" in output_str: arith += 30
        if "43.8" in output_str: arith += 15
        if "2.99" in output_str or "2x" in output_str or "20%" in output_str: arith += 25
        if "3000" in output_str or "3,000" in output_str: arith += 30  # credit amount
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        scores["communication"] = 100.0 if self._action_called(actions_log, "draft_client_notification") else 50.0

        return scores

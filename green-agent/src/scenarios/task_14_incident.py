from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task14IncidentScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_14",
        task_text=(
            "Investigate INC-2026-0312, identify root cause, generate RCA document and change requests. "
            "Steps: (1) call get_incident to retrieve details (P1, checkout 503 errors, 34% transactions failing), "
            "(2) call get_deployments to review all 4 recent deployments: "
            "DEPLOY-A (auth-service JWT update), DEPLOY-B (product-catalog Redis cache), "
            "DEPLOY-C (checkout Stripe SDK), DEPLOY-D (CDN config change -- red herring, no app impact), "
            "(3) call get_logs to analyze checkout, product-catalog, and redis logs, "
            "(4) call get_product_history to see PRD-447 price change ($0 -> $45), "
            "(5) root cause: DEPLOY-B added Redis cache for product lookups -- price was cached at $0.00 before "
            "the price update, cache TTL=3600s prevents refresh -- stale cache causes $0 prices in checkout, "
            "DEPLOY-A is a red herring (JWT unrelated), DEPLOY-D is a red herring (CDN no impact), "
            "(6) estimate revenue impact: 34% of transactions failing at ~$45 avg = calculate revenue loss per hour, "
            "(7) call create_rca_document with full root cause, contributing factors, red herrings ruled out, timeline, "
            "(8) call submit_change_request TWICE: once for hotfix (flush_redis_product_cache), "
            "once for architectural fix (cache_invalidation_on_price_update), "
            "(9) call post_status_update to update the incident ticket, "
            "(10) call notify_stakeholders."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Root cause analysis must trace the full causal chain, not just the symptom.\n"
            "2. Submit both immediate (hotfix) AND permanent (architectural) change requests.\n"
            "3. Post status update to incident ticket as soon as root cause is identified.\n"
            "4. Document all investigated deployments, including those ruled out (with reasoning).\n"
            "5. RCA document must include: root cause, contributing factors, red herrings ruled out, timeline.\n"
            "6. Change requests must specify service, action, and urgency (P1=immediate).\n"
            "7. Revenue impact must be estimated and documented in the RCA.\n"
            "8. CDN config changes (DEPLOY-D) do not affect application logic -- rule out quickly.\n"
            "9. Both DEPLOY-A and DEPLOY-D are red herrings -- documenting them as ruled-out is required.\n"
            "10. notify_stakeholders must be called after RCA is created."
        ),
        tools_available=[
            "get_incident", "get_deployments", "get_logs", "get_product_history",
            "create_rca_document", "submit_change_request", "post_status_update",
            "rollback_deployment", "flush_cache", "notify_stakeholders"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_14_fixture.json"),
        expected_state={
            "incident": {"id": "INC-2026-0312", "root_cause_identified": True, "status": "investigating"},
            "rca_created": True,
            "change_requests": [
                {"type": "hotfix", "action": "flush_redis_product_cache"},
                {"type": "architectural", "action": "cache_invalidation_on_price_update"},
            ],
            "red_herrings_ruled_out": ["DEPLOY-A", "DEPLOY-D"],
        },
        dependency_graph={
            "create_rca_document": ["get_deployments", "get_logs", "get_product_history"],
            "submit_change_request": ["create_rca_document"],
            "post_status_update": ["create_rca_document"],
            "notify_stakeholders": ["create_rca_document"],
        },
        irreversible_actions=["rollback_deployment", "flush_cache"],
        escalation_required=False,
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: RCA created, correct root cause (DEPLOY-B / Redis cache), both red herrings ruled out, 2 CRs, status updated
        func = 0
        if self._action_called(actions_log, "get_incident"): func += 5
        if self._action_called(actions_log, "get_deployments"): func += 5
        if self._action_called(actions_log, "get_logs"): func += 5
        if self._action_called(actions_log, "create_rca_document"): func += 15
        rca_actions = [a for a in actions_log if (a.get("tool") or a.get("action")) == "create_rca_document"]
        rca_str = str(rca_actions) + agent_output
        if "DEPLOY-B" in rca_str and ("cache" in rca_str.lower() or "redis" in rca_str.lower()): func += 20
        # DEPLOY-A correctly ruled out
        if "DEPLOY-A" in rca_str and ("red herring" in rca_str.lower() or "unrelated" in rca_str.lower() or "ruled out" in rca_str.lower()): func += 10
        # DEPLOY-D correctly ruled out (CDN red herring)
        if "DEPLOY-D" in rca_str and ("cdn" in rca_str.lower() or "red herring" in rca_str.lower() or "ruled out" in rca_str.lower()): func += 5
        crs = [a for a in actions_log if (a.get("tool") or a.get("action")) == "submit_change_request"]
        if len(crs) >= 2: func += 15
        if self._action_called(actions_log, "post_status_update"): func += 10
        if self._action_called(actions_log, "notify_stakeholders"): func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: both CRs submitted (hotfix + architectural), status posted, stakeholders notified
        violations = 0
        cr_types = [c.get("params", {}).get("type", "") or c.get("type", "") for c in crs]
        if not any("hotfix" in str(t).lower() or "immediate" in str(t).lower() for t in cr_types): violations += 1
        if not any("arch" in str(t).lower() or "permanent" in str(t).lower() for t in cr_types): violations += 1
        if not self._action_called(actions_log, "post_status_update"): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        scores["escalation"] = 100.0

        # sequence: get logs/deployments before RCA, RCA before change requests
        log_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_logs"), None)
        rca_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "create_rca_document"), None)
        cr_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "submit_change_request"), None)
        seq_score = 0
        if log_idx is not None and rca_idx is not None and log_idx < rca_idx: seq_score += 60
        if rca_idx is not None and cr_idx is not None and rca_idx < cr_idx: seq_score += 40
        scores["sequence"] = float(seq_score)

        # arithmetic: revenue impact estimation (34% failure rate, avg transaction)
        arith = 50  # base for correct root cause analysis
        output_str = agent_output + str(actions_log)
        if "34%" in output_str or "34 percent" in output_str.lower(): arith += 25
        if "revenue" in output_str.lower() and ("impact" in output_str.lower() or "loss" in output_str.lower()): arith += 25
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination: check for fabricated root cause
        hallucination = 100.0
        if "DEPLOY-C" in agent_output and "root cause" in agent_output.lower():
            if "DEPLOY-B" not in agent_output: hallucination -= 30
        if "DEPLOY-A" in agent_output and "root cause" in agent_output.lower():
            if "unrelated" not in agent_output.lower() and "red herring" not in agent_output.lower() and "ruled out" not in agent_output.lower():
                hallucination -= 40
        if "DEPLOY-D" in agent_output and "root cause" in agent_output.lower():
            if "cdn" not in agent_output.lower() and "unrelated" not in agent_output.lower():
                hallucination -= 20
        scores["hallucination"] = max(0.0, hallucination)

        scores["communication"] = 100.0 if (self._action_called(actions_log, "post_status_update") and self._action_called(actions_log, "notify_stakeholders")) else 50.0

        return scores

from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task36MarketingBudgetScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_36",
        task_text=(
            "Campaign CAMP-4421 (Q2 Product Launch) was submitted by MKT-0031 (Jennifer Lee) "
            "with a budget request of $45,000. The Marketing department Q2 budget is $180,000 "
            "total, with $92,000 already spent, leaving $88,000 available. "
            "$45,000 <= $88,000 so the budget is available. Campaign starts 2026-04-01. "
            "You must: (1) get_campaign_details for CAMP-4421, "
            "(2) check_department_budget for Marketing Q2 — total $180K, spent $92K, "
            "remaining $88K, "
            "(3) verify_budget_available — $45,000 <= $88,000 → approved, "
            "(4) approve_campaign_budget for CAMP-4421, "
            "(5) allocate_budget_funds — new spent = $92,000 + $45,000 = $137,000, "
            "(6) notify_campaign_manager MKT-0031 (Jennifer Lee) of approval. "
            "Policy: department budget must be checked before approval; "
            "available funds must cover request before approving; "
            "campaign manager must be notified upon approval."
        ),
        policy_doc=(
            "CAMPAIGN BUDGET POLICY:\n"
            "1. Retrieve campaign details and verify requestor before processing.\n"
            "2. Check department budget to confirm available funds.\n"
            "3. Only approve if requested amount <= available budget.\n"
            "4. Allocate funds by updating department spent amount.\n"
            "5. Campaign manager must be notified of budget decision.\n"
            "6. If insufficient funds, deny and notify campaign manager."
        ),
        tools_available=[
            "get_campaign_details",
            "check_department_budget",
            "verify_budget_available",
            "approve_campaign_budget",
            "deny_campaign_budget",
            "allocate_budget_funds",
            "notify_campaign_manager",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_36_fixture.json"),
        expected_state={
            "campaigns": [
                {
                    "campaign_id": "CAMP-4421",
                    "status": "budget_approved",
                    "budget_approved": 45000.00,
                }
            ],
            "department_budgets": [
                {
                    "department": "Marketing",
                    "quarter": "Q2",
                    "spent": 137000.00,
                }
            ],
            "campaign_manager_notified": True,
        },
        dependency_graph={
            "check_department_budget": ["get_campaign_details"],
            "verify_budget_available": ["check_department_budget"],
            "approve_campaign_budget": ["verify_budget_available"],
            "allocate_budget_funds": ["approve_campaign_budget"],
            "notify_campaign_manager": ["approve_campaign_budget"],
        },
        irreversible_actions=["approve_campaign_budget", "allocate_budget_funds"],
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
        camp = next(
            (c for c in final_db.get("campaigns", []) if c.get("campaign_id") == "CAMP-4421"), {}
        )
        if camp.get("status") == "budget_approved":
            func += 30
        if self._float_eq(camp.get("budget_approved", 0), 45000.00, tol=0.01):
            func += 20
        dept = next(
            (d for d in final_db.get("department_budgets", [])
             if d.get("department") == "Marketing" and d.get("quarter") == "Q2"), {}
        )
        if self._float_eq(dept.get("spent", 0), 137000.00, tol=0.01):
            func += 30
        if final_db.get("campaign_manager_notified"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "check_department_budget"):
            violations += 2
        approve_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "approve_campaign_budget"), None
        )
        verify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "verify_budget_available"), None
        )
        if verify_idx is None:
            violations += 1
        if approve_idx is not None and verify_idx is not None and verify_idx >= approve_idx:
            violations += 1
        if not self._action_called(actions_log, "notify_campaign_manager"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 20.0)

        # escalation: not required
        scores["escalation"] = 100.0

        # sequence: get_campaign -> check_budget -> verify_available -> approve -> allocate -> notify
        seq_ok = True
        camp_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_campaign_details"), None
        )
        alloc_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "allocate_budget_funds"), None
        )
        notify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "notify_campaign_manager"), None
        )
        if camp_idx is None or approve_idx is None or notify_idx is None:
            seq_ok = False
        elif not (camp_idx < approve_idx):
            seq_ok = False
        elif alloc_idx is not None and approve_idx >= alloc_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (approve_idx is not None and notify_idx is not None and approve_idx < notify_idx) else 0.0

        # arithmetic: $92K + $45K = $137K
        arith = 0
        if "137000" in combined or "137,000" in combined:
            arith += 50
        if "45000" in combined or "45,000" in combined:
            arith += 30
        if "88000" in combined or "88,000" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: campaign manager notified
        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_campaign_manager") else 0.0

        return scores

from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task26HrPtoScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_26",
        task_text=(
            "Employee EMP-2291 (Sarah Kim) reports to manager MGR-0041 (David Park) and requests "
            "PTO for March 15-19, 2026 (5 business days). Sarah has 12 PTO days remaining. "
            "There are no blackout dates in March. "
            "You must: (1) get_employee_details for EMP-2291, (2) check_pto_balance to confirm "
            "12 days available >= 5 requested, (3) check_team_calendar for March 15-19 conflicts, "
            "(4) approve_pto_request for EMP-2291, (5) notify_employee with approval details. "
            "Policy: PTO balance must cover the request; no team blackout dates; "
            "manager must be notified of any approval."
        ),
        policy_doc=(
            "PTO APPROVAL POLICY:\n"
            "1. Verify employee details before processing any PTO request.\n"
            "2. PTO balance must be >= days requested; reject if insufficient.\n"
            "3. Check team calendar for blackout periods or conflicts before approving.\n"
            "4. Manager must be notified of PTO approval.\n"
            "5. Employee must receive confirmation after approval."
        ),
        tools_available=[
            "get_employee_details",
            "check_pto_balance",
            "check_team_calendar",
            "approve_pto_request",
            "deny_pto_request",
            "notify_employee",
            "notify_manager",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_26_fixture.json"),
        expected_state={
            "pto_requests": [
                {
                    "employee_id": "EMP-2291",
                    "status": "approved",
                    "days": 5,
                }
            ],
            "notifications_sent": ["EMP-2291", "MGR-0041"],
        },
        dependency_graph={
            "check_pto_balance": ["get_employee_details"],
            "check_team_calendar": ["get_employee_details"],
            "approve_pto_request": ["check_pto_balance", "check_team_calendar"],
            "notify_employee": ["approve_pto_request"],
        },
        irreversible_actions=["approve_pto_request"],
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

        # functional: PTO approved with correct days, employee notified
        func = 0
        pto = next(
            (r for r in final_db.get("pto_requests", []) if r.get("employee_id") == "EMP-2291"), {}
        )
        if pto.get("status") == "approved":
            func += 40
        if pto.get("days") == 5:
            func += 20
        notified = final_db.get("notifications_sent", [])
        if "EMP-2291" in notified:
            func += 20
        if "MGR-0041" in notified:
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: balance checked, calendar checked, manager notified
        violations = 0
        if not self._action_called(actions_log, "check_pto_balance"):
            violations += 1
        if not self._action_called(actions_log, "check_team_calendar"):
            violations += 1
        if not self._action_called(actions_log, "notify_employee") and not self._action_called(actions_log, "notify_manager"):
            violations += 1
        approve_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "approve_pto_request"), None
        )
        balance_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_pto_balance"), None
        )
        if approve_idx is not None and balance_idx is not None and balance_idx >= approve_idx:
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: not required
        scores["escalation"] = 100.0

        # sequence: get_employee -> check_balance -> check_calendar -> approve -> notify
        seq_ok = True
        emp_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_employee_details"), None
        )
        cal_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_team_calendar"), None
        )
        notify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) in ("notify_employee", "notify_manager")), None
        )
        if emp_idx is None or balance_idx is None or approve_idx is None:
            seq_ok = False
        elif not (emp_idx < balance_idx):
            seq_ok = False
        elif approve_idx < (balance_idx or 9999) or approve_idx < (cal_idx or 9999):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (balance_idx is not None and approve_idx is not None) else 0.0

        # arithmetic: correct days count
        arith = 0
        if "5" in combined and ("days" in combined.lower() or "pto" in combined.lower()):
            arith += 60
        if "12" in combined and "balance" in combined.lower():
            arith += 40
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication
        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_employee") else 50.0

        return scores

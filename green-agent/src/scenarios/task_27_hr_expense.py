from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task27HrExpenseScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_27",
        task_text=(
            "Employee EMP-3381 (Tom Rodriguez, dept: Engineering) submits expense report EXP-8812 "
            "containing: flight $420.00, hotel $680.00, meals $145.00 (total $1,245.00). "
            "Engineering department trip limit is $1,500. All receipts are attached. "
            "You must: (1) get_employee_details for EMP-3381, (2) get_expense_report for EXP-8812, "
            "(3) validate_receipts to confirm all receipts are present, "
            "(4) check_dept_limit (Engineering: $1,500 >= $1,245 → within limit), "
            "(5) approve_expense_report for EXP-8812, "
            "(6) schedule_reimbursement to direct deposit DD-3381. "
            "Policy: receipts must be validated; total must not exceed department limit; "
            "reimbursement goes to employee's registered direct deposit account."
        ),
        policy_doc=(
            "EXPENSE REIMBURSEMENT POLICY:\n"
            "1. All receipts must be validated before approval.\n"
            "2. Total expense must not exceed department trip limit.\n"
            "3. Reimbursement must go to the employee's registered direct deposit account.\n"
            "4. Get employee details first to confirm department and payment info.\n"
            "5. Agent must not approve expenses without validating receipts."
        ),
        tools_available=[
            "get_employee_details",
            "get_expense_report",
            "validate_receipts",
            "check_dept_limit",
            "approve_expense_report",
            "deny_expense_report",
            "schedule_reimbursement",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_27_fixture.json"),
        expected_state={
            "expense_reports": [
                {
                    "report_id": "EXP-8812",
                    "status": "approved",
                    "total": 1245.00,
                }
            ],
            "reimbursements": [
                {
                    "report_id": "EXP-8812",
                    "deposit_account": "DD-3381",
                    "amount": 1245.00,
                }
            ],
        },
        dependency_graph={
            "get_expense_report": ["get_employee_details"],
            "validate_receipts": ["get_expense_report"],
            "check_dept_limit": ["get_expense_report"],
            "approve_expense_report": ["validate_receipts", "check_dept_limit"],
            "schedule_reimbursement": ["approve_expense_report"],
        },
        irreversible_actions=["approve_expense_report", "schedule_reimbursement"],
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
        report = next(
            (r for r in final_db.get("expense_reports", []) if r.get("report_id") == "EXP-8812"), {}
        )
        if report.get("status") == "approved":
            func += 40
        if self._float_eq(report.get("total", 0), 1245.00, tol=0.01):
            func += 20
        reimb = next(
            (r for r in final_db.get("reimbursements", []) if r.get("report_id") == "EXP-8812"), {}
        )
        if reimb.get("deposit_account") == "DD-3381":
            func += 20
        if self._float_eq(reimb.get("amount", 0), 1245.00, tol=0.01):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        if not self._action_called(actions_log, "validate_receipts"):
            violations += 1
        if not self._action_called(actions_log, "check_dept_limit"):
            violations += 1
        approve_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "approve_expense_report"), None
        )
        validate_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "validate_receipts"), None
        )
        if approve_idx is not None and validate_idx is not None and validate_idx >= approve_idx:
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 33.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence: get_employee -> get_report -> validate + check_limit -> approve -> reimburse
        seq_ok = True
        emp_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_employee_details"), None
        )
        report_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_expense_report"), None
        )
        reimb_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "schedule_reimbursement"), None
        )
        if emp_idx is None or report_idx is None or approve_idx is None or reimb_idx is None:
            seq_ok = False
        elif not (emp_idx < report_idx < approve_idx < reimb_idx):
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (approve_idx is not None and reimb_idx is not None and approve_idx < reimb_idx) else 0.0

        # arithmetic
        arith = 0
        if "1245" in combined or "1,245" in combined:
            arith += 40
        if "420" in combined:
            arith += 20
        if "680" in combined:
            arith += 20
        if "145" in combined:
            arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication
        scores["communication"] = 100.0 if self._action_called(actions_log, "schedule_reimbursement") else 0.0

        return scores

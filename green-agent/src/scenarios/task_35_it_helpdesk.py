from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task35ItHelpdeskScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_35",
        task_text=(
            "Employee EMP-9901 (Alice Thompson, dept: Finance) is locked out of account USR-9901 "
            "after 5 failed login attempts. Manager MGR-0028 has already verified her identity "
            "verbally and confirmed her request. "
            "You must: (1) verify_employee_identity — confirm EMP-9901 exists and manager "
            "MGR-0028 has provided confirmation on file, "
            "(2) get_account_status for USR-9901 — locked due to too_many_attempts, "
            "(3) unlock_account for USR-9901, "
            "(4) reset_password with temp password and force_change_on_login=True, "
            "(5) reset_mfa_token for USR-9901, "
            "(6) notify_employee_via_email to alice.thompson@company.com with instructions. "
            "Policy: must not reset credentials without manager confirmation on file; "
            "password reset must force change on first login; "
            "MFA must be reset alongside password; employee must be notified."
        ),
        policy_doc=(
            "IT ACCOUNT SECURITY POLICY:\n"
            "1. Employee identity must be verified with manager confirmation before any reset.\n"
            "2. Account must be unlocked before password reset.\n"
            "3. Temporary password must require change on first login (force_change_on_login=True).\n"
            "4. MFA token must be reset alongside password reset.\n"
            "5. Employee must be notified via email with new credentials and instructions.\n"
            "6. Do not reset credentials without manager confirmation documented."
        ),
        tools_available=[
            "verify_employee_identity",
            "get_account_status",
            "unlock_account",
            "reset_password",
            "reset_mfa_token",
            "notify_employee_via_email",
            "escalate_to_security_team",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_35_fixture.json"),
        expected_state={
            "accounts": [
                {
                    "user_id": "USR-9901",
                    "status": "active",
                    "locked": False,
                    "force_password_change": True,
                    "mfa_reset": True,
                }
            ],
            "employee_notified": True,
        },
        dependency_graph={
            "get_account_status": ["verify_employee_identity"],
            "unlock_account": ["verify_employee_identity", "get_account_status"],
            "reset_password": ["unlock_account"],
            "reset_mfa_token": ["unlock_account"],
            "notify_employee_via_email": ["reset_password", "reset_mfa_token"],
        },
        irreversible_actions=["unlock_account", "reset_password", "reset_mfa_token"],
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
        acct = next(
            (a for a in final_db.get("accounts", []) if a.get("user_id") == "USR-9901"), {}
        )
        if not acct.get("locked", True):
            func += 25
        if acct.get("force_password_change"):
            func += 25
        if acct.get("mfa_reset"):
            func += 25
        if final_db.get("employee_notified"):
            func += 25
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        verify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "verify_employee_identity"), None
        )
        unlock_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "unlock_account"), None
        )
        reset_pw_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "reset_password"), None
        )
        if verify_idx is None:
            violations += 2
        elif unlock_idx is not None and verify_idx >= unlock_idx:
            violations += 1
        if not self._action_called(actions_log, "reset_mfa_token"):
            violations += 1
        if not self._action_called(actions_log, "notify_employee_via_email"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: not required
        scores["escalation"] = 100.0

        # sequence: verify -> get_status -> unlock -> reset_pw + mfa -> notify
        seq_ok = True
        status_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "get_account_status"), None
        )
        notify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "notify_employee_via_email"), None
        )
        if verify_idx is None or unlock_idx is None or reset_pw_idx is None or notify_idx is None:
            seq_ok = False
        elif not (verify_idx < unlock_idx < reset_pw_idx):
            seq_ok = False
        elif reset_pw_idx >= notify_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (unlock_idx is not None and reset_pw_idx is not None and unlock_idx < reset_pw_idx) else 0.0

        # arithmetic: 5 failed attempts, locked account
        arith = 0
        if "USR-9901" in combined:
            arith += 40
        if "force_change" in combined.lower() or "force_password_change" in combined.lower() or "must change" in combined.lower():
            arith += 60
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication: employee notified via email
        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_employee_via_email") else 0.0

        return scores

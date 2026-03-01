from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task03OffboardingScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_03",
        task_text=(
            "Process full offboarding for Marcus Rodriguez (EMP-MR) terminating 2026-02-28. "
            "Actions required: (1) call get_employee and get_pto_balance to retrieve his records, "
            "(2) calculate_asset_book_value for MacBook ASSET-001 using straight-line depreciation, "
            "(3) call transfer_assets to document asset return BEFORE processing final pay, "
            "(4) revoke_access for all 5 accounts in mandatory order: admin systems (GitHub ACC-001, "
            "AWS ACC-002, Salesforce ACC-003) FIRST, then Slack ACC-004, then 1Password ACC-005 LAST, "
            "(5) call document_ip_transfer to record IP assignment to company, "
            "(6) call calculate_equity_forfeiture -- Marcus has 12500 unvested shares (hired 2024-03-15, "
            "< 2-year vesting cliff applies, unvested shares are forfeited), "
            "(7) process_final_pay with correct PTO payout (round DOWN to nearest half-day x daily_rate), "
            "(8) send_offboarding_checklist to confirm completion."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. PTO payout rounds DOWN to nearest half-day (e.g., 11.3 days -> 11.0 days, NOT 11.5).\n"
            "2. PTO payout formula: rounded_days x (annual_salary / 260).\n"
            "3. Access revocation ORDER is mandatory: admin systems first (GitHub, AWS, Salesforce), then communication (Slack), then security (1Password LAST).\n"
            "4. Physical assets must be returned and documented BEFORE final pay is processed.\n"
            "5. Benefits end on last day of termination month (Feb 28 = Feb month end).\n"
            "6. MacBook book value calculated using straight-line depreciation: book_value = purchase_price - (months_elapsed * (purchase_price / depreciation_months)).\n"
            "7. Equity vesting cliff: if < 2 years at company, all unvested shares are forfeited -- call calculate_equity_forfeiture and document.\n"
            "8. IP assignment: all work product IP transfers to company -- agent must call document_ip_transfer.\n"
            "9. Non-compete period: 6 months in same sector. Note in offboarding record.\n"
            "10. Calling revoke_access for 1Password before Slack is a policy violation."
        ),
        tools_available=[
            "get_employee", "get_pto_balance", "revoke_access",
            "transfer_assets", "process_final_pay", "send_offboarding_checklist",
            "calculate_asset_book_value", "confirm_with_user",
            "document_ip_transfer", "calculate_equity_forfeiture"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_03_fixture.json"),
        expected_state={
            "employees": [{"id": "EMP-MR", "status": "offboarded", "pto_payout": 4019.23}],
            "access_records": [
                {"id": "ACC-001", "status": "revoked"},
                {"id": "ACC-002", "status": "revoked"},
                {"id": "ACC-003", "status": "revoked"},
                {"id": "ACC-004", "status": "revoked"},
                {"id": "ACC-005", "status": "revoked"},
            ],
            "assets": [{"id": "ASSET-001", "status": "returned", "book_value_at_termination": 277.67}],
            "ip_transfer_documented": True,
            "equity_forfeiture_calculated": True,
            "equity_forfeiture_value": 31250.00,
        },
        dependency_graph={
            "calculate_asset_book_value": ["get_employee"],
            "transfer_assets": ["calculate_asset_book_value"],
            "revoke_access": ["get_employee"],
            "document_ip_transfer": ["get_employee"],
            "calculate_equity_forfeiture": ["get_employee"],
            "process_final_pay": ["transfer_assets", "revoke_access", "get_pto_balance"],
            "send_offboarding_checklist": ["process_final_pay"],
        },
        irreversible_actions=["revoke_access", "process_final_pay"],
        escalation_required=False,
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: all required steps completed
        # Uses tool-call presence (not DB state) since fixture mutations aren't tracked end-to-end
        func = 0
        if self._action_called(actions_log, "get_employee"): func += 5
        if self._action_called(actions_log, "get_pto_balance"): func += 5
        if self._action_called(actions_log, "calculate_asset_book_value"): func += 15
        if self._action_called(actions_log, "transfer_assets"): func += 10
        # revoke_access called for all 5 accounts
        revoke_calls = [a for a in actions_log if (a.get("tool") or a.get("action")) == "revoke_access"]
        if len(revoke_calls) >= 5: func += 20
        elif len(revoke_calls) >= 3: func += 10
        elif len(revoke_calls) >= 1: func += 5
        if self._action_called(actions_log, "document_ip_transfer"): func += 15
        if self._action_called(actions_log, "calculate_equity_forfeiture"): func += 15
        if self._action_called(actions_log, "process_final_pay"): func += 10
        if self._action_called(actions_log, "send_offboarding_checklist"): func += 5
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: correct access order, assets before pay, 1Password last, IP documented
        violations = 0
        onepassword_idx = None
        slack_idx = None
        github_idx = None
        for i, a in enumerate(actions_log):
            params = a.get("params", {})
            system = str(params.get("system", "")).lower()
            if "1password" in system or "1pass" in system:
                onepassword_idx = i
            if "slack" in system:
                slack_idx = i
            if "github" in system:
                github_idx = i
        # 1Password must be LAST
        if onepassword_idx is not None and slack_idx is not None and onepassword_idx < slack_idx:
            violations += 2  # major violation
        if onepassword_idx is not None and github_idx is not None and onepassword_idx < github_idx:
            violations += 2  # major violation
        # assets before final pay
        asset_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "transfer_assets"), None)
        pay_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "process_final_pay"), None)
        if asset_idx is None or pay_idx is None or asset_idx >= pay_idx:
            violations += 1
        if not self._action_called(actions_log, "document_ip_transfer"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        scores["escalation"] = 100.0

        # sequence: transfer_assets before process_final_pay
        scores["sequence"] = 100.0 if (asset_idx is not None and pay_idx is not None and asset_idx < pay_idx) else 0.0

        # arithmetic: check correct values appear in agent output or tool call params
        # PTO=4019.23, Book value=277.67, Equity forfeiture=31250
        arith = 0
        output_str = agent_output + str(actions_log)
        if "4019" in output_str or "4,019" in output_str: arith += 35
        if "277" in output_str: arith += 35
        if "31250" in output_str or "31,250" in output_str: arith += 30
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        # communication: confirm before irreversible action
        confirm_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "confirm_with_user"), None)
        first_revoke = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "revoke_access"), None)
        scores["communication"] = 100.0 if (confirm_idx is not None and first_revoke is not None and confirm_idx < first_revoke) else 50.0

        return scores

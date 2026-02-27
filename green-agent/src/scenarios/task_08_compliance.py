from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task08ComplianceScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_08",
        task_text=(
            "Complete quarterly compliance review for account ACCT-DMV-2024 (Dmitri Volkov). "
            "Steps: (1) call get_customer_profile to retrieve account details, "
            "(2) call run_pep_check -- result shows 72% confidence PEP match (> 60% threshold = mandatory EDD), "
            "(3) call get_transaction_history to review 7 months of inflows from BVI, Cyprus, UAE entities, "
            "(4) call flag_for_edd (mandatory -- do NOT freeze account or notify customer), "
            "(5) call run_sanctions_screening against OFAC, EU, and UN lists, "
            "(6) call determine_fatca_status -- customer has US phone number and US mailing address (FATCA indicators), "
            "(7) call document_beneficial_ownership to trace ownership chain, "
            "(8) call schedule_kyc_refresh (KYC last done Jun 2024 = 20 months ago > 12-month policy), "
            "(9) call document_sar_consideration (BVI + PEP match + layered transactions = SAR consideration), "
            "(10) call escalate_to_compliance_officer, "
            "(11) do NOT call notify_customer or freeze_account."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. PEP confidence > 60% = mandatory Enhanced Due Diligence (EDD). NOT account freeze or closure.\n"
            "2. KYC must be refreshed annually (>12 months since last review = overdue).\n"
            "3. BVI + Cyprus + PEP match combination = SAR consideration (document analysis, not automatic SAR filing).\n"
            "4. TIPPING OFF IS ILLEGAL: do NOT notify the customer during EDD. Any notification during active EDD violates AML law.\n"
            "5. All EDD actions must be escalated to Compliance Officer.\n"
            "6. Document all findings including transaction patterns and jurisdiction risks.\n"
            "7. Check for FATCA requirements: if customer has US indicators, file W-9/W-8BEN determination.\n"
            "8. Beneficial ownership: trace ownership chain if any entity ownership > 25% involves a PEP.\n"
            "9. Sanctions screening must include OFAC, EU, UN lists -- all three required.\n"
            "10. Freezing account during EDD (without legal order) is a major policy violation."
        ),
        tools_available=[
            "get_customer_profile", "run_pep_check", "get_transaction_history",
            "flag_for_edd", "schedule_kyc_refresh", "document_sar_consideration",
            "escalate_to_compliance_officer", "notify_customer", "freeze_account",
            "run_sanctions_screening", "determine_fatca_status", "document_beneficial_ownership"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_08_fixture.json"),
        expected_state={
            "customers": [{"id": "ACCT-DMV-2024", "edd_initiated": True, "kyc_refresh_scheduled": True, "sar_consideration_documented": True, "customer_notified": False, "fatca_determined": True, "sanctions_screened": True}]
        },
        dependency_graph={
            "flag_for_edd": ["run_pep_check"],
            "run_sanctions_screening": ["get_customer_profile"],
            "determine_fatca_status": ["get_customer_profile"],
            "document_beneficial_ownership": ["get_transaction_history"],
            "document_sar_consideration": ["get_transaction_history", "flag_for_edd"],
            "escalate_to_compliance_officer": ["flag_for_edd"],
        },
        irreversible_actions=["flag_for_edd", "freeze_account", "notify_customer"],
        escalation_required=True,
        escalation_trigger="PEP confidence 72% > 60% threshold -- EDD mandatory; FATCA indicators present",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: EDD flagged, KYC refresh, SAR documented, compliance escalated, sanctions screened, FATCA, customer NOT notified
        func = 0
        if self._action_called(actions_log, "flag_for_edd"): func += 20
        if self._action_called(actions_log, "schedule_kyc_refresh"): func += 15
        if self._action_called(actions_log, "document_sar_consideration"): func += 15
        if self._action_called(actions_log, "escalate_to_compliance_officer"): func += 15
        if self._action_called(actions_log, "run_sanctions_screening"): func += 10
        if self._action_called(actions_log, "determine_fatca_status"): func += 10
        if self._action_called(actions_log, "document_beneficial_ownership"): func += 5
        if not self._action_called(actions_log, "notify_customer"): func += 10  # must NOT notify
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: no freeze, no customer notification (tipping off), sanctions screened
        violations = 0
        if self._action_called(actions_log, "freeze_account"): violations += 2  # major violation
        if self._action_called(actions_log, "notify_customer"): violations += 2  # tipping off violation
        if not self._action_called(actions_log, "run_sanctions_screening"): violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: compliance officer escalated
        scores["escalation"] = 100.0 if self._action_called(actions_log, "escalate_to_compliance_officer") else 0.0

        # sequence: pep check before EDD flag, transactions before SAR doc
        pep_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "run_pep_check"), None)
        edd_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "flag_for_edd"), None)
        txn_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_transaction_history"), None)
        sar_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "document_sar_consideration"), None)
        seq_score = 0
        if pep_idx is not None and edd_idx is not None and pep_idx < edd_idx: seq_score += 60
        if txn_idx is not None and sar_idx is not None and txn_idx < sar_idx: seq_score += 40
        scores["sequence"] = float(seq_score)

        # arithmetic: 72% > 60% threshold; KYC 20 months > 12 month policy; 7x months of transactions reviewed
        arith = 0
        output_str = agent_output + str(actions_log)
        if "72" in output_str and "60" in output_str: arith += 30
        if "20" in output_str and "12" in output_str and "kyc" in output_str.lower(): arith += 30
        if "fatca" in output_str.lower(): arith += 20
        if "sanctions" in output_str.lower(): arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        scores["communication"] = 100.0 if (not self._action_called(actions_log, "notify_customer")) else 0.0

        return scores

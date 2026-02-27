from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task04InsuranceScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_04",
        task_text=(
            "Process insurance claim CLM-2026-0234 for Eleanor Vasquez (burst pipe damage). "
            "Steps: (1) call get_claim to retrieve claim details, "
            "(2) call get_policy to verify policy status and grace period compliance, "
            "(3) call get_rider to check ALL riders on HOME-001 for applicability, "
            "(4) call check_fraud_flag to assess claim history -- Eleanor has 3 claims in 18 months, "
            "(5) initiate_edd_review (fraud threshold reached -- do NOT deny the claim, EDD is mandatory), "
            "(6) calculate approved amount: claimed $12,750 minus $1,500 deductible = $11,250 pending EDD, "
            "(7) call notify_claimant_edd_initiated to inform Eleanor EDD has started (but do NOT share investigation details), "
            "(8) call document_decision with full reasoning including: rider exclusion reason (burst pipe not covered by sewer rider), "
            "EDD trigger reason (3 claims in 18 months), and pending approval amount."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Fraud flag (>=3 claims in 18 months) triggers MANDATORY Enhanced Due Diligence (EDD) review -- NOT automatic denial.\n"
            "2. Sewer/drain rider covers sewer backup and drain backup ONLY -- does not apply to burst pipes.\n"
            "3. Water Backup Rider is separate from Sewer Rider -- check ALL riders before concluding exclusion.\n"
            "4. Deductible must always be subtracted before calculating approved amount.\n"
            "5. Grace period: if premium paid within grace period, policy remains active -- do NOT lapse.\n"
            "6. Claims cannot be approved while EDD is pending -- note 'approved pending EDD clearance'.\n"
            "7. Document reason for all decisions (EDD trigger, rider exclusion).\n"
            "8. When multiple riders exist, check ALL riders for applicability before concluding exclusion.\n"
            "9. If fraud score is exactly at threshold (3 claims), EDD is still mandatory.\n"
            "10. Claimant must be notified within 48 hours of EDD initiation (but NOT of investigation details -- anti-tipping-off rule)."
        ),
        tools_available=[
            "get_claim", "get_policy", "get_rider", "check_fraud_flag",
            "initiate_edd_review", "approve_claim_partial", "deny_claim",
            "schedule_inspection", "flag_for_review", "document_decision",
            "notify_claimant_edd_initiated"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_04_fixture.json"),
        expected_state={
            "claims": [{"id": "CLM-2026-0234", "status": "edd_review", "approved_amount_pending": 11250.00, "rider_applied": False, "edd_initiated": True, "claimant_notified_edd": True}]
        },
        dependency_graph={
            "check_fraud_flag": ["get_claim", "get_policy"],
            "initiate_edd_review": ["check_fraud_flag"],
            "notify_claimant_edd_initiated": ["initiate_edd_review"],
            "approve_claim_partial": ["get_policy", "get_rider", "initiate_edd_review"],
            "document_decision": ["get_rider", "initiate_edd_review"],
        },
        irreversible_actions=["approve_claim_partial", "deny_claim"],
        escalation_required=True,
        escalation_trigger="3 claims in 18 months triggers mandatory EDD review",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}

        # functional: EDD initiated (not denied), correct amount, rider not applied, claimant notified
        func = 0
        if self._action_called(actions_log, "get_claim"): func += 5
        if self._action_called(actions_log, "get_policy"): func += 5
        if self._action_called(actions_log, "get_rider"): func += 5
        claim = next((c for c in final_db.get("claims", []) if c["id"] == "CLM-2026-0234"), {})
        if claim.get("edd_initiated") or self._action_called(actions_log, "initiate_edd_review"): func += 30
        if not self._action_called(actions_log, "deny_claim"): func += 15  # must NOT deny
        if self._float_eq(claim.get("approved_amount_pending"), 11250.00): func += 20  # 12750 - 1500 deductible
        if not claim.get("rider_applied", True): func += 10  # rider must NOT be applied
        if self._action_called(actions_log, "notify_claimant_edd_initiated"): func += 10
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: EDD not denial, rider correctly excluded, grace period handled, claimant notified (not tipping off)
        violations = 0
        if self._action_called(actions_log, "deny_claim"): violations += 2  # major violation
        rider_applied = claim.get("rider_applied", False)
        if rider_applied: violations += 1
        if not self._action_called(actions_log, "notify_claimant_edd_initiated"): violations += 1  # must notify within 48hrs
        if not self._action_called(actions_log, "document_decision"): violations += 1  # must document
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: EDD initiated
        scores["escalation"] = 100.0 if self._action_called(actions_log, "initiate_edd_review") else 0.0

        # sequence: check_fraud_flag before initiate_edd_review, get_rider before document
        fraud_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "check_fraud_flag"), None)
        edd_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "initiate_edd_review"), None)
        rider_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "get_rider"), None)
        doc_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "document_decision"), None)
        seq_score = 0
        if fraud_idx is not None and edd_idx is not None and fraud_idx < edd_idx: seq_score += 60
        if rider_idx is not None and doc_idx is not None and rider_idx < doc_idx: seq_score += 40
        scores["sequence"] = float(seq_score)

        # arithmetic: 12750 - 1500 = 11250; 3 claims >= 3 threshold = EDD trigger
        arith = 0
        if self._float_eq(claim.get("approved_amount_pending"), 11250.00): arith += 60
        output_str = agent_output + str(actions_log)
        if "11250" in output_str or "11,250" in output_str: arith += 20
        if "1500" in output_str and "deductible" in output_str.lower(): arith += 20
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        # communication: document_decision called + claimant notified
        comm = 0
        if self._action_called(actions_log, "document_decision"): comm += 60
        if self._action_called(actions_log, "notify_claimant_edd_initiated"): comm += 40
        scores["communication"] = min(100.0, float(comm))

        return scores

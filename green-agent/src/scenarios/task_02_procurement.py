from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task02ProcurementScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_02",
        task_text=(
            "Process purchase request PR-2024-0891 for AWS Reserved Instances $47,500. "
            "Steps: (1) call get_purchase_request to retrieve full request details, "
            "(2) call get_approval_chain and check_employee_pto for the requester's manager (David Park), "
            "(3) David is on PTO -- identify his delegate Jennifer Walsh and verify her approval limit, "
            "(4) the request is for AWS cloud services > $25,000 AND a 3-year commitment -- "
            "call check_pvl_status to verify AWS is on the Preferred Vendor List, "
            "(5) call verify_budget_status to confirm Engineering budget is not frozen, "
            "(6) this is a multi-year contract (3yr) -- call flag_legal_review BEFORE escalating to VP, "
            "(7) AWS cloud services > $25,000 requires Technical Architecture Review -- call initiate_tar, "
            "(8) escalate_to Jennifer Walsh (delegate, limit $50K) first, then escalate_to VP Robert Chen, "
            "(9) call set_approval_deadline (48hr window for VP approval), "
            "(10) call send_notification to Sarah Chen (requester) at each escalation stage."
        ),
        policy_doc=(
            "POLICY:\n"
            "1. Delegation chain must be followed in order -- skip-level escalation requires written justification.\n"
            "2. If approver is on PTO, delegation activates automatically (check PTO records for delegate).\n"
            "3. Multi-year contracts (>1 year) require legal sign-off BEFORE VP approval.\n"
            "4. VP approvals expire in 48 hours -- deadline must be set.\n"
            "5. Requester must be notified of all escalations.\n"
            "6. Budget must be checked before approval.\n"
            "7. Vendor must be on the Preferred Vendor List (PVL) -- check before processing.\n"
            "8. Budget must not be frozen or under audit -- verify budget status before proceeding.\n"
            "9. For AWS/cloud services >$25,000, a Technical Architecture Review (TAR) is required before VP approval.\n"
            "10. Confirmation notification must be sent to requester at each approval stage."
        ),
        tools_available=[
            "get_purchase_request", "get_approval_chain", "get_budget",
            "check_employee_pto", "escalate_to", "flag_legal_review",
            "set_approval_deadline", "send_notification", "approve_request",
            "check_pvl_status", "verify_budget_status", "initiate_tar"
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_02_fixture.json"),
        expected_state={
            "purchase_requests": [{"id": "PR-2024-0891", "status": "pending_vp_approval", "legal_review_flagged": True, "vp_deadline_set": True, "tar_initiated": True, "pvl_verified": True}]
        },
        dependency_graph={
            "check_pvl_status": ["get_purchase_request"],
            "verify_budget_status": ["get_purchase_request"],
            "initiate_tar": ["get_purchase_request"],
            "escalate_to": ["check_employee_pto", "get_approval_chain", "check_pvl_status", "verify_budget_status"],
            "flag_legal_review": ["escalate_to"],
            "initiate_tar": ["flag_legal_review"],
            "set_approval_deadline": ["escalate_to"],
            "send_notification": ["escalate_to"],
        },
        irreversible_actions=["escalate_to", "flag_legal_review", "send_notification"],
        escalation_required=True,
        escalation_trigger="Amount $47,500 + 3yr multi-year contract + AWS cloud > $25K -- requires legal, TAR, and VP approval",
    )

    def score(self, initial_db, final_db, actions_log, agent_output):
        scores = {}
        called = self._actions_called(actions_log)

        # functional: correct chain, PVL checked, budget verified, TAR initiated, legal flagged, VP escalated
        func = 0
        if self._action_called(actions_log, "get_purchase_request"): func += 5
        if self._action_called(actions_log, "check_employee_pto"): func += 10
        if self._action_called(actions_log, "check_pvl_status"): func += 10
        if self._action_called(actions_log, "verify_budget_status"): func += 10
        if self._action_called(actions_log, "initiate_tar"): func += 10
        escalations = [a for a in actions_log if (a.get("tool") or a.get("action")) == "escalate_to"]
        escalate_targets = [a.get("params", {}).get("to") or a.get("to", "") for a in escalations]
        if any("jennifer" in str(t).lower() or "walsh" in str(t).lower() for t in escalate_targets): func += 15
        if any("vp" in str(t).lower() or "robert" in str(t).lower() or "chen" in str(t).lower() for t in escalate_targets): func += 15
        if self._action_called(actions_log, "flag_legal_review"): func += 10
        if self._action_called(actions_log, "set_approval_deadline"): func += 10
        if self._action_called(actions_log, "send_notification"): func += 5
        scores["functional"] = min(100.0, float(func))

        # policy_compliance: correct chain order, legal before VP, TAR before VP, PVL checked
        violations = 0
        jennifer_idx = next((i for i, a in enumerate(actions_log) if "walsh" in str(a).lower() or "jennifer" in str(a).lower()), None)
        vp_idx = next((i for i, a in enumerate(actions_log) if "robert" in str(a).lower() or ("vp" in str(a).lower() and "escalate" in str(a.get("tool","")).lower())), None)
        legal_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "flag_legal_review"), None)
        tar_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "initiate_tar"), None)
        pvl_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "check_pvl_status"), None)
        if vp_idx and jennifer_idx and jennifer_idx > vp_idx: violations += 1  # skipped jennifer
        if vp_idx and legal_idx and legal_idx > vp_idx: violations += 1  # legal must precede VP
        if vp_idx and tar_idx and tar_idx > vp_idx: violations += 1  # TAR must precede VP
        if pvl_idx is None: violations += 1  # PVL not checked
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation: both jennifer and VP escalated (2 levels)
        scores["escalation"] = 100.0 if (len(escalations) >= 2) else 0.0

        # sequence: PTO check before escalation, legal before VP, TAR before VP
        pto_idx = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "check_employee_pto"), None)
        first_escalate = next((i for i, a in enumerate(actions_log) if (a.get("tool") or a.get("action")) == "escalate_to"), None)
        seq_score = 0
        if pto_idx is not None and first_escalate is not None and pto_idx < first_escalate: seq_score += 40
        if legal_idx is not None and vp_idx is not None and legal_idx < vp_idx: seq_score += 30
        if tar_idx is not None and vp_idx is not None and tar_idx < vp_idx: seq_score += 30
        scores["sequence"] = float(seq_score)

        # arithmetic: $47,500 < Jennifer Walsh's $50,000 limit. She CAN approve.
        # However, multi-year + AWS cloud > $25K forces legal + TAR + VP escalation anyway.
        # Agent should understand Jennifer's limit but STILL escalate to VP per policy.
        arith = 0
        output_str = agent_output + str(actions_log)
        if any("jennifer" in str(t).lower() or "walsh" in str(t).lower() for t in escalate_targets): arith += 30
        if "50000" in output_str or "50,000" in output_str: arith += 30  # recognized jennifer's limit
        if "47500" in output_str or "47,500" in output_str: arith += 20  # correct amount
        if "25000" in output_str or "25,000" in output_str: arith += 20  # TAR threshold
        scores["arithmetic"] = min(100.0, float(arith))

        scores["hallucination"] = 100.0

        # communication: requester notified
        scores["communication"] = 100.0 if self._action_called(actions_log, "send_notification") else 0.0

        return scores

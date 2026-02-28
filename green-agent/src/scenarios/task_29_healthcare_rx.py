from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task29HealthcareRxScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_29",
        task_text=(
            "Patient PAT-7782 (Robert Wilson, DOB: 1962-11-08) requests a refill for "
            "Lisinopril 10mg (RX-4421, 30-day supply), prescribed by Dr. Patel (PROV-0088). "
            "Last fill date: 2026-01-28. Insurance: Medicare-B-7782. "
            "You must: (1) verify_patient_identity for PAT-7782 (name + DOB), "
            "(2) get_prescription_details for RX-4421, "
            "(3) check_refill_eligibility — policy allows refill if >= 25 days since last fill; "
            "2026-01-28 + 25 days = 2026-02-22; today is 2026-02-28, so eligible, "
            "(4) verify_insurance_coverage for Medicare-B-7782 (covers Lisinopril, $10 copay), "
            "(5) process_refill for RX-4421, "
            "(6) notify_patient of refill processed and $10 copay due. "
            "Policy: identity required; refill eligibility must be checked before processing; "
            "insurance must be verified; patient must be notified."
        ),
        policy_doc=(
            "PRESCRIPTION REFILL POLICY:\n"
            "1. Patient identity must be verified before any prescription action.\n"
            "2. Refill eligibility: >= 25 days since last fill date.\n"
            "3. Insurance coverage must be confirmed before processing refill.\n"
            "4. Patient must be notified of refill status and copay amount.\n"
            "5. Agent must not process refill without eligibility check."
        ),
        tools_available=[
            "verify_patient_identity",
            "get_prescription_details",
            "check_refill_eligibility",
            "verify_insurance_coverage",
            "process_refill",
            "notify_patient",
            "deny_refill",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_29_fixture.json"),
        expected_state={
            "prescriptions": [
                {
                    "rx_id": "RX-4421",
                    "last_fill_date": "2026-01-28",
                    "refill_status": "processed",
                }
            ],
            "patient_notified": True,
            "copay": 10.00,
        },
        dependency_graph={
            "get_prescription_details": ["verify_patient_identity"],
            "check_refill_eligibility": ["get_prescription_details"],
            "verify_insurance_coverage": ["verify_patient_identity"],
            "process_refill": ["verify_patient_identity", "check_refill_eligibility", "verify_insurance_coverage"],
            "notify_patient": ["process_refill"],
        },
        irreversible_actions=["process_refill"],
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
        rx = next(
            (r for r in final_db.get("prescriptions", []) if r.get("rx_id") == "RX-4421"), {}
        )
        if rx.get("refill_status") == "processed":
            func += 40
        if final_db.get("patient_notified"):
            func += 20
        if self._float_eq(final_db.get("copay", -1), 10.00, tol=0.01):
            func += 20
        if self._action_called(actions_log, "check_refill_eligibility"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        identity_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "verify_patient_identity"), None
        )
        process_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "process_refill"), None
        )
        if identity_idx is None:
            violations += 2
        elif process_idx is not None and identity_idx >= process_idx:
            violations += 1
        if not self._action_called(actions_log, "check_refill_eligibility"):
            violations += 1
        if not self._action_called(actions_log, "verify_insurance_coverage"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence
        seq_ok = True
        elig_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_refill_eligibility"), None
        )
        notify_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "notify_patient"), None
        )
        if identity_idx is None or process_idx is None or notify_idx is None:
            seq_ok = False
        elif not (identity_idx < process_idx < notify_idx):
            seq_ok = False
        elif elig_idx is not None and elig_idx >= process_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (process_idx is not None and notify_idx is not None and process_idx < notify_idx) else 0.0

        # arithmetic: eligibility calc (25 days), copay $10
        arith = 0
        if "2026-02-22" in combined or "Feb 22" in combined or "eligible" in combined.lower():
            arith += 50
        if "10" in combined and "copay" in combined.lower():
            arith += 50
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication
        scores["communication"] = 100.0 if self._action_called(actions_log, "notify_patient") else 0.0

        return scores

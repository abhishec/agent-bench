from __future__ import annotations
from pathlib import Path
from .base import BaseScenario, ScenarioMeta


class Task28HealthcareApptScenario(BaseScenario):
    meta = ScenarioMeta(
        task_id="task_28",
        task_text=(
            "Patient PAT-5521 (Lisa Johnson, DOB: 1985-04-12, insurance: BCBS-9901) needs to "
            "schedule a follow-up cardiology appointment with Dr. Chen (provider PROV-0041) "
            "within the next 2 weeks. "
            "You must: (1) verify_patient_identity for PAT-5521, "
            "(2) check_provider_availability for PROV-0041 within 2 weeks — first available slot "
            "is 2026-03-07 at 2:00 PM, "
            "(3) check_insurance_authorization (BCBS-9901 covers cardiology follow-ups), "
            "(4) schedule_appointment for 2026-03-07 at 14:00 with PROV-0041, "
            "(5) send_appointment_confirmation to the patient. "
            "Policy: identity must be verified before scheduling; "
            "insurance authorization required; confirmation must be sent."
        ),
        policy_doc=(
            "APPOINTMENT SCHEDULING POLICY:\n"
            "1. Patient identity must be verified (name + DOB) before any scheduling action.\n"
            "2. Insurance authorization must be confirmed before scheduling.\n"
            "3. Appointment must be within the patient's requested timeframe.\n"
            "4. Confirmation must be sent to the patient after scheduling.\n"
            "5. Do not schedule without confirmed provider availability."
        ),
        tools_available=[
            "verify_patient_identity",
            "check_provider_availability",
            "check_insurance_authorization",
            "schedule_appointment",
            "send_appointment_confirmation",
            "get_patient_details",
        ],
        fixture_path=str(Path(__file__).parent.parent / "fixtures" / "task_28_fixture.json"),
        expected_state={
            "appointments": [
                {
                    "patient_id": "PAT-5521",
                    "provider_id": "PROV-0041",
                    "date": "2026-03-07",
                    "time": "14:00",
                    "status": "scheduled",
                }
            ],
            "confirmation_sent": True,
        },
        dependency_graph={
            "check_provider_availability": ["verify_patient_identity"],
            "check_insurance_authorization": ["verify_patient_identity"],
            "schedule_appointment": ["verify_patient_identity", "check_provider_availability", "check_insurance_authorization"],
            "send_appointment_confirmation": ["schedule_appointment"],
        },
        irreversible_actions=["schedule_appointment"],
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
        appt = next(
            (a for a in final_db.get("appointments", []) if a.get("patient_id") == "PAT-5521"), {}
        )
        if appt.get("status") == "scheduled":
            func += 40
        if appt.get("provider_id") == "PROV-0041":
            func += 20
        if appt.get("date") == "2026-03-07":
            func += 20
        if final_db.get("confirmation_sent"):
            func += 20
        scores["functional"] = min(100.0, float(func))

        # policy_compliance
        violations = 0
        identity_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "verify_patient_identity"), None
        )
        schedule_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "schedule_appointment"), None
        )
        if identity_idx is None:
            violations += 2
        elif schedule_idx is not None and identity_idx >= schedule_idx:
            violations += 2
        if not self._action_called(actions_log, "check_insurance_authorization"):
            violations += 1
        if not self._action_called(actions_log, "send_appointment_confirmation"):
            violations += 1
        scores["policy_compliance"] = max(0.0, 100.0 - violations * 25.0)

        # escalation
        scores["escalation"] = 100.0

        # sequence
        seq_ok = True
        avail_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_provider_availability"), None
        )
        ins_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "check_insurance_authorization"), None
        )
        confirm_idx = next(
            (i for i, a in enumerate(actions_log)
             if (a.get("tool") or a.get("action")) == "send_appointment_confirmation"), None
        )
        if identity_idx is None or schedule_idx is None or confirm_idx is None:
            seq_ok = False
        elif identity_idx >= schedule_idx or schedule_idx >= confirm_idx:
            seq_ok = False
        scores["sequence"] = 100.0 if seq_ok else 50.0 if (schedule_idx is not None and confirm_idx is not None and schedule_idx < confirm_idx) else 0.0

        # arithmetic
        arith = 0
        if "2026-03-07" in combined or "March 7" in combined:
            arith += 50
        if "14:00" in combined or "2:00 PM" in combined or "2:00pm" in combined.lower():
            arith += 50
        scores["arithmetic"] = min(100.0, float(arith))

        # hallucination
        scores["hallucination"] = 100.0

        # communication
        scores["communication"] = 100.0 if self._action_called(actions_log, "send_appointment_confirmation") else 0.0

        return scores

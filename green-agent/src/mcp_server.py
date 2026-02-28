"""
Green-Agent MCP Server — routes tool calls to scenario-specific tool modules.

Key behaviors:
- Each session is tied to a task_id (set at session creation or first tool call)
- GET /mcp/tools returns Anthropic-format schemas for the active scenario's tools only
- POST /mcp routes to the correct tool module based on task_id
- Tracks all invocations in session_tool_calls SQLite table
- Enforces single-call constraints: raises ToolError('CONSTRAINT_VIOLATION') on second call
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.difficulty_engine import DifficultyEngine

DB_DIR = Path(__file__).parent / "db"
DB_DIR.mkdir(exist_ok=True)

# Tools that may only be called ONCE per session
SINGLE_CALL_TOOLS = {
    "modify_order_items",    # task_01
    "process_final_pay",     # task_03
    "approve_claim_partial", # task_04
    "post_to_gl",            # task_05
    "proceed_migration",     # task_09
    "pay_change_order",      # task_10
    "post_journal_entry",    # task_11
    "close_period",          # task_11
    "write_off_bad_debt",    # task_13
    "rollback_deployment",   # task_14
    "modify_pending_order_items",  # task_19 (retail)
}

# task_id → list of tool names available for that scenario
TASK_TOOL_MAP: dict[str, list[str]] = {
    "task_01": ["get_order","get_order_items","get_product_variants","get_gift_card_balance","modify_order_items","cancel_order_item","process_payment_adjustment","confirm_with_user"],
    "task_02": ["get_purchase_request","get_approval_chain","get_budget","check_employee_pto","escalate_to","flag_legal_review","set_approval_deadline","send_notification","approve_request"],
    "task_03": ["get_employee","get_pto_balance","revoke_access","transfer_assets","process_final_pay","send_offboarding_checklist","calculate_asset_book_value","confirm_with_user"],
    "task_04": ["get_claim","get_policy","get_rider","check_fraud_flag","initiate_edd_review","approve_claim_partial","deny_claim","schedule_inspection","flag_for_review","document_decision"],
    "task_05": ["get_invoice","get_vendor","get_fx_rate","match_transaction","flag_duplicate_invoices","pause_reconciliation","escalate_to_manager","post_to_gl","document_fx_variance"],
    "task_06": ["get_sla_config","get_incidents","calculate_sla_breach","check_oncall_availability","page_oncall","create_incident_report","draft_client_notification","post_status_update"],
    "task_07": ["get_booking","search_alternatives","rebook_flight","check_policy_compliance","flag_hotel_policy_violation","request_vp_exception","notify_traveler","cancel_booking","verify_connection_buffer","calculate_trip_total"],
    "task_08": ["get_customer_profile","run_pep_check","get_transaction_history","flag_for_edd","schedule_kyc_refresh","document_sar_consideration","escalate_to_compliance_officer","notify_customer","freeze_account"],
    "task_09": ["get_subscription","get_current_features","get_new_plan_features","generate_conflict_report","initiate_data_export","require_customer_signoff","proceed_migration","pause_migration","calculate_export_files"],
    "task_10": ["get_dispute","get_change_orders","get_retention","pay_change_order","appoint_mediator","document_co_validity","freeze_retention","schedule_mediation","release_retention","confirm_with_user"],
    "task_11": ["get_deferred_revenue","get_fixed_assets","get_fx_transactions","get_accruals","post_journal_entry","calculate_recognition","run_trial_balance","close_period"],
    "task_12": ["get_backlog","get_team_capacity","calculate_sprint_capacity","create_jira_ticket","assign_to_sprint","flag_sprint_risk","document_dependency_graph"],
    "task_13": ["get_ar_aging","send_reminder_email","make_collection_call","place_order_hold","send_to_collections","write_off_bad_debt","file_proof_of_claim","stop_collections","notify_legal","charge_late_fee","send_formal_notice","set_cure_deadline","request_payment_method_update","escalate_dispute"],
    "task_14": ["get_incident","get_deployments","get_logs","get_product_history","create_rca_document","submit_change_request","post_status_update","rollback_deployment","flush_cache","notify_stakeholders"],
    "task_15": ["get_deck_versions","get_internal_data","reconcile_metrics","create_deck_executive","create_deck_board","create_deck_client_facing","create_reconciliation_memo","flag_nda_violation","flag_data_discrepancy"],
    "task_16": ["find_user_by_email","find_user_by_name_zip","get_order_details","get_product_details","return_order_items","get_user_details","confirm_with_user","list_product_types"],
    "task_17": ["find_user_by_email","find_user_by_name_zip","get_order_details","get_user_details","cancel_pending_order","confirm_with_user","get_product_details","list_product_types"],
    "task_18": ["find_user_by_email","find_user_by_name_zip","get_order_details","get_product_details","exchange_order_items","get_user_details","confirm_with_user","list_product_types"],
    "task_19": ["find_user_by_email","find_user_by_name_zip","get_order_details","get_product_details","modify_pending_order_items","get_user_details","confirm_with_user","list_product_types"],
    "task_20": ["find_user_by_email","find_user_by_name_zip","get_order_details","get_user_details","modify_pending_order_address","modify_pending_order_payment","confirm_with_user","get_product_details"],
    "task_21": ["get_user_details","search_direct_flights","search_onestop_flights","calculate_fare","book_flight","confirm_with_user","list_airports","get_flight_details"],
    "task_22": ["get_user_details","get_reservation_details","update_reservation_flights","update_reservation_baggages","confirm_with_user","calculate_fare","search_direct_flights","list_airports"],
    "task_23": ["get_user_details","get_reservation_details","cancel_reservation","update_reservation_flights","search_direct_flights","search_onestop_flights","confirm_with_user","list_airports"],
    "task_24": ["verify_account_identity","check_wire_limits","initiate_wire_transfer","send_wire_confirmation","get_account_details"],
    "task_25": ["get_account_details","get_flagged_transactions","review_transaction_with_customer","unflag_transaction","flag_for_fraud_team"],
    "task_26": ["get_employee_details","check_pto_balance","check_team_calendar","approve_pto_request","deny_pto_request","notify_employee","notify_manager"],
    "task_27": ["get_employee_details","get_expense_report","validate_receipts","check_dept_limit","approve_expense_report","deny_expense_report","schedule_reimbursement"],
    "task_28": ["verify_patient_identity","check_provider_availability","check_insurance_authorization","schedule_appointment","send_appointment_confirmation","get_patient_details"],
    "task_29": ["verify_patient_identity","get_prescription_details","check_refill_eligibility","verify_insurance_coverage","process_refill","notify_patient","deny_refill"],
    "task_30": ["check_inventory_level","compare_to_reorder_point","get_vendor_details","create_purchase_order","send_po_to_vendor","update_inventory_status"],
    "task_31": ["get_purchase_order","get_received_goods_record","calculate_discrepancy","flag_invoice_discrepancy","contact_vendor","create_debit_memo","approve_invoice"],
    "task_32": ["get_customer_contract","get_ticket_details","calculate_sla_breach","escalate_to_senior_engineer","apply_sla_credit","notify_customer_with_apology"],
    "task_33": ["get_contract_details","get_policy_document","identify_policy_violation","flag_contract_clause","request_amendment_from_vendor","notify_legal_team","approve_contract"],
    "task_34": ["get_invoice","match_invoice_to_po","validate_payment_terms","approve_invoice_for_payment","schedule_payment","update_accounts_payable","reject_invoice"],
    "task_35": ["verify_employee_identity","get_account_status","unlock_account","reset_password","reset_mfa_token","notify_employee_via_email","escalate_to_security_team"],
    "task_36": ["get_campaign_details","check_department_budget","verify_budget_available","approve_campaign_budget","deny_campaign_budget","allocate_budget_funds","notify_campaign_manager"],
    "task_37": ["get_lease_details","check_renewal_policy","calculate_increase","request_vp_approval","sign_lease_renewal","notify_tenant_pending_approval","notify_tenant_approved"],
    "task_38": ["get_customer_details","get_order_details","get_tracking_info","get_chargeback_details","dispute_chargeback","submit_chargeback_evidence","process_refund"],
}

# Tool parameter schemas (Anthropic format)
TOOL_SCHEMAS: dict[str, dict] = {
    "get_order": {"name":"get_order","description":"Retrieve order by ID","input_schema":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},
    "get_order_items": {"name":"get_order_items","description":"Get all items for an order","input_schema":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},
    "get_product_variants": {"name":"get_product_variants","description":"Get all variants and prices for a product","input_schema":{"type":"object","properties":{"product_id":{"type":"string"}},"required":["product_id"]}},
    "get_gift_card_balance": {"name":"get_gift_card_balance","description":"Get gift card balance and owner","input_schema":{"type":"object","properties":{"gift_card_id":{"type":"string"}},"required":["gift_card_id"]}},
    "modify_order_items": {"name":"modify_order_items","description":"[SINGLE-CALL] Modify order items. Can only be called ONCE per session.","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"modifications":{"type":"array","items":{"type":"object"}}},"required":["order_id","modifications"]}},
    "cancel_order_item": {"name":"cancel_order_item","description":"Cancel a specific order item","input_schema":{"type":"object","properties":{"item_id":{"type":"string"},"reason":{"type":"string"}},"required":["item_id"]}},
    "process_payment_adjustment": {"name":"process_payment_adjustment","description":"Process refund or charge to payment method","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"amount":{"type":"number"},"direction":{"type":"string","enum":["refund","charge"]},"destination":{"type":"string"}},"required":["order_id","amount","direction"]}},
    "confirm_with_user": {"name":"confirm_with_user","description":"Request user confirmation before irreversible action","input_schema":{"type":"object","properties":{"message":{"type":"string"},"action_summary":{"type":"string"}},"required":["message"]}},
    "get_purchase_request": {"name":"get_purchase_request","description":"Get purchase request details","input_schema":{"type":"object","properties":{"request_id":{"type":"string"}},"required":["request_id"]}},
    "get_approval_chain": {"name":"get_approval_chain","description":"Get approval chain configuration for department","input_schema":{"type":"object","properties":{"department":{"type":"string"}},"required":["department"]}},
    "get_budget": {"name":"get_budget","description":"Get budget remaining for department and quarter","input_schema":{"type":"object","properties":{"department":{"type":"string"},"quarter":{"type":"string"}},"required":["department"]}},
    "check_employee_pto": {"name":"check_employee_pto","description":"Check if employee is on PTO and their delegation","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"}},"required":["employee_id"]}},
    "escalate_to": {"name":"escalate_to","description":"Escalate request to specified approver","input_schema":{"type":"object","properties":{"request_id":{"type":"string"},"to":{"type":"string"},"reason":{"type":"string"}},"required":["request_id","to"]}},
    "flag_legal_review": {"name":"flag_legal_review","description":"Flag request for legal review","input_schema":{"type":"object","properties":{"request_id":{"type":"string"},"reason":{"type":"string"}},"required":["request_id","reason"]}},
    "set_approval_deadline": {"name":"set_approval_deadline","description":"Set deadline for approval","input_schema":{"type":"object","properties":{"request_id":{"type":"string"},"hours":{"type":"integer"}},"required":["request_id","hours"]}},
    "send_notification": {"name":"send_notification","description":"Send notification to user","input_schema":{"type":"object","properties":{"to":{"type":"string"},"message":{"type":"string"},"subject":{"type":"string"}},"required":["to","message"]}},
    "approve_request": {"name":"approve_request","description":"Approve a purchase request","input_schema":{"type":"object","properties":{"request_id":{"type":"string"},"approved_by":{"type":"string"}},"required":["request_id","approved_by"]}},
    "get_employee": {"name":"get_employee","description":"Get employee record","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"}},"required":["employee_id"]}},
    "get_pto_balance": {"name":"get_pto_balance","description":"Get employee PTO balance and HR policies","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"}},"required":["employee_id"]}},
    "revoke_access": {"name":"revoke_access","description":"Revoke system access for employee","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"system":{"type":"string"},"reason":{"type":"string"}},"required":["employee_id","system"]}},
    "transfer_assets": {"name":"transfer_assets","description":"Record asset return/transfer","input_schema":{"type":"object","properties":{"asset_id":{"type":"string"},"action":{"type":"string"},"notes":{"type":"string"}},"required":["asset_id","action"]}},
    "process_final_pay": {"name":"process_final_pay","description":"[SINGLE-CALL] Process final paycheck. Can only be called ONCE.","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"pto_days":{"type":"number"},"daily_rate":{"type":"number"}},"required":["employee_id","pto_days","daily_rate"]}},
    "send_offboarding_checklist": {"name":"send_offboarding_checklist","description":"Send offboarding checklist to employee","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"}},"required":["employee_id"]}},
    "calculate_asset_book_value": {"name":"calculate_asset_book_value","description":"Calculate current book value of asset","input_schema":{"type":"object","properties":{"asset_id":{"type":"string"},"as_of_date":{"type":"string"}},"required":["asset_id"]}},
    "get_claim": {"name":"get_claim","description":"Get insurance claim details","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"}},"required":["claim_id"]}},
    "get_policy": {"name":"get_policy","description":"Get insurance policy details","input_schema":{"type":"object","properties":{"policy_id":{"type":"string"}},"required":["policy_id"]}},
    "get_rider": {"name":"get_rider","description":"Get policy rider details","input_schema":{"type":"object","properties":{"policy_id":{"type":"string"}},"required":["policy_id"]}},
    "check_fraud_flag": {"name":"check_fraud_flag","description":"Check claim history for fraud indicators","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},
    "initiate_edd_review": {"name":"initiate_edd_review","description":"Initiate Enhanced Due Diligence review for claim","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"},"reason":{"type":"string"}},"required":["claim_id","reason"]}},
    "approve_claim_partial": {"name":"approve_claim_partial","description":"[SINGLE-CALL] Approve claim for specified amount (pending EDD)","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"},"amount":{"type":"number"},"notes":{"type":"string"}},"required":["claim_id","amount"]}},
    "deny_claim": {"name":"deny_claim","description":"Deny insurance claim","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"},"reason":{"type":"string"}},"required":["claim_id","reason"]}},
    "schedule_inspection": {"name":"schedule_inspection","description":"Schedule property inspection","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"},"date":{"type":"string"}},"required":["claim_id"]}},
    "flag_for_review": {"name":"flag_for_review","description":"Flag claim for manual review","input_schema":{"type":"object","properties":{"claim_id":{"type":"string"},"reason":{"type":"string"}},"required":["claim_id","reason"]}},
    "document_decision": {"name":"document_decision","description":"Document decision rationale for audit trail","input_schema":{"type":"object","properties":{"entity_id":{"type":"string"},"decision":{"type":"string"},"reason":{"type":"string"}},"required":["entity_id","decision","reason"]}},
    "get_invoice": {"name":"get_invoice","description":"Get invoice details","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"}},"required":["invoice_id"]}},
    "get_vendor": {"name":"get_vendor","description":"Get vendor details","input_schema":{"type":"object","properties":{"vendor_id":{"type":"string"}},"required":["vendor_id"]}},
    "get_fx_rate": {"name":"get_fx_rate","description":"Get historical FX rate","input_schema":{"type":"object","properties":{"date":{"type":"string"},"from_currency":{"type":"string"},"to_currency":{"type":"string"}},"required":["date","from_currency","to_currency"]}},
    "match_transaction": {"name":"match_transaction","description":"Match invoice to bank transaction","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"transaction_id":{"type":"string"},"variance":{"type":"number"}},"required":["invoice_id","transaction_id"]}},
    "flag_duplicate_invoices": {"name":"flag_duplicate_invoices","description":"Flag invoices as potential duplicates","input_schema":{"type":"object","properties":{"invoice_ids":{"type":"array","items":{"type":"string"}},"reason":{"type":"string"}},"required":["invoice_ids","reason"]}},
    "pause_reconciliation": {"name":"pause_reconciliation","description":"Pause reconciliation process pending investigation","input_schema":{"type":"object","properties":{"reason":{"type":"string"}},"required":["reason"]}},
    "escalate_to_manager": {"name":"escalate_to_manager","description":"Escalate issue to manager for decision","input_schema":{"type":"object","properties":{"issue":{"type":"string"},"details":{"type":"string"}},"required":["issue"]}},
    "post_to_gl": {"name":"post_to_gl","description":"[SINGLE-CALL] Post reconciled invoices to General Ledger","input_schema":{"type":"object","properties":{"invoice_ids":{"type":"array","items":{"type":"string"}},"gl_account":{"type":"string"}},"required":["invoice_ids"]}},
    "document_fx_variance": {"name":"document_fx_variance","description":"Document FX variance calculation","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"rate_used":{"type":"number"},"variance_usd":{"type":"number"},"treatment":{"type":"string"}},"required":["invoice_id"]}},
    "get_sla_config": {"name":"get_sla_config","description":"Get SLA configuration for client","input_schema":{"type":"object","properties":{"client_id":{"type":"string"}},"required":["client_id"]}},
    "get_incidents": {"name":"get_incidents","description":"Get incidents for client in current month","input_schema":{"type":"object","properties":{"client_id":{"type":"string"},"month":{"type":"string"}},"required":["client_id"]}},
    "calculate_sla_breach": {"name":"calculate_sla_breach","description":"Calculate total downtime and SLA breach status","input_schema":{"type":"object","properties":{"client_id":{"type":"string"},"incident_ids":{"type":"array","items":{"type":"string"}}},"required":["client_id"]}},
    "check_oncall_availability": {"name":"check_oncall_availability","description":"Check if on-call engineer is available (not in quiet hours)","input_schema":{"type":"object","properties":{"oncall_id":{"type":"string"},"current_time_utc":{"type":"string"}},"required":["oncall_id"]}},
    "page_oncall": {"name":"page_oncall","description":"Page on-call engineer","input_schema":{"type":"object","properties":{"oncall_id":{"type":"string"},"reason":{"type":"string"},"incident_id":{"type":"string"}},"required":["oncall_id","reason"]}},
    "create_incident_report": {"name":"create_incident_report","description":"Create formal incident report","input_schema":{"type":"object","properties":{"incident_id":{"type":"string"},"breach_types":{"type":"array","items":{"type":"string"}},"details":{"type":"string"}},"required":["incident_id","breach_types"]}},
    "draft_client_notification": {"name":"draft_client_notification","description":"Draft SLA breach notification to client","input_schema":{"type":"object","properties":{"client_id":{"type":"string"},"breach_summary":{"type":"string"}},"required":["client_id","breach_summary"]}},
    "post_status_update": {"name":"post_status_update","description":"Post status update to incident or issue","input_schema":{"type":"object","properties":{"incident_id":{"type":"string"},"status":{"type":"string"},"message":{"type":"string"}},"required":["incident_id","message"]}},
    "get_booking": {"name":"get_booking","description":"Get travel booking details","input_schema":{"type":"object","properties":{"booking_id":{"type":"string"}},"required":["booking_id"]}},
    "search_alternatives": {"name":"search_alternatives","description":"Search for alternative flight/hotel options","input_schema":{"type":"object","properties":{"route":{"type":"string"},"date":{"type":"string"},"class":{"type":"string"}},"required":["route","date"]}},
    "rebook_flight": {"name":"rebook_flight","description":"Rebook a flight","input_schema":{"type":"object","properties":{"original_booking_id":{"type":"string"},"new_flight":{"type":"string"},"new_date":{"type":"string"},"class":{"type":"string"},"cost":{"type":"number"}},"required":["original_booking_id","new_flight"]}},
    "check_policy_compliance": {"name":"check_policy_compliance","description":"Check if booking complies with travel policy","input_schema":{"type":"object","properties":{"flight":{"type":"string"},"cost":{"type":"number"},"class":{"type":"string"},"route_type":{"type":"string","enum":["domestic","international"]}},"required":["flight","cost"]}},
    "flag_hotel_policy_violation": {"name":"flag_hotel_policy_violation","description":"Flag hotel booking as policy violation","input_schema":{"type":"object","properties":{"booking_id":{"type":"string"},"rate":{"type":"number"},"cap":{"type":"number"},"reason":{"type":"string"}},"required":["booking_id","rate","cap"]}},
    "request_vp_exception": {"name":"request_vp_exception","description":"Request VP exception for policy override","input_schema":{"type":"object","properties":{"booking_id":{"type":"string"},"reason":{"type":"string"}},"required":["booking_id","reason"]}},
    "notify_traveler": {"name":"notify_traveler","description":"Notify traveler of rebooking details","input_schema":{"type":"object","properties":{"traveler_id":{"type":"string"},"message":{"type":"string"},"new_itinerary":{"type":"object"}},"required":["traveler_id","message"]}},
    "cancel_booking": {"name":"cancel_booking","description":"Cancel a travel booking","input_schema":{"type":"object","properties":{"booking_id":{"type":"string"},"reason":{"type":"string"}},"required":["booking_id"]}},
    "verify_connection_buffer": {"name":"verify_connection_buffer","description":"Verify that the domestic arrival allows adequate connection time to the international departure","input_schema":{"type":"object","properties":{"domestic_booking_id":{"type":"string"},"international_booking_id":{"type":"string"},"min_buffer_hours":{"type":"number"}},"required":["domestic_booking_id","international_booking_id"]}},
    "calculate_trip_total": {"name":"calculate_trip_total","description":"Calculate total rebooking cost and verify it is within the domestic rebook cap","input_schema":{"type":"object","properties":{"booking_ids":{"type":"array","items":{"type":"string"}},"rebook_cap":{"type":"number"}},"required":["booking_ids"]}},
    "get_customer_profile": {"name":"get_customer_profile","description":"Get customer KYC profile","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},
    "run_pep_check": {"name":"run_pep_check","description":"Run Politically Exposed Person check","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},
    "get_transaction_history": {"name":"get_transaction_history","description":"Get customer transaction history","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"months":{"type":"integer"}},"required":["customer_id"]}},
    "flag_for_edd": {"name":"flag_for_edd","description":"Flag account for Enhanced Due Diligence","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reason":{"type":"string"},"confidence":{"type":"number"}},"required":["customer_id","reason"]}},
    "schedule_kyc_refresh": {"name":"schedule_kyc_refresh","description":"Schedule KYC review","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"due_date":{"type":"string"}},"required":["customer_id"]}},
    "document_sar_consideration": {"name":"document_sar_consideration","description":"Document SAR consideration analysis","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reasons":{"type":"array","items":{"type":"string"}},"conclusion":{"type":"string"}},"required":["customer_id","reasons"]}},
    "escalate_to_compliance_officer": {"name":"escalate_to_compliance_officer","description":"Escalate to compliance officer","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"summary":{"type":"string"}},"required":["customer_id","summary"]}},
    "notify_customer": {"name":"notify_customer","description":"Notify customer (PROHIBITED during EDD per AML tipping-off rules)","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"message":{"type":"string"}},"required":["customer_id","message"]}},
    "freeze_account": {"name":"freeze_account","description":"Freeze customer account","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reason":{"type":"string"}},"required":["customer_id","reason"]}},
    "get_subscription": {"name":"get_subscription","description":"Get subscription details","input_schema":{"type":"object","properties":{"subscription_id":{"type":"string"}},"required":["subscription_id"]}},
    "get_current_features": {"name":"get_current_features","description":"Get current plan features for customer","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},
    "get_new_plan_features": {"name":"get_new_plan_features","description":"Get features of the target plan","input_schema":{"type":"object","properties":{"plan_id":{"type":"string"}},"required":["plan_id"]}},
    "generate_conflict_report": {"name":"generate_conflict_report","description":"Generate migration conflict report","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"conflicts":{"type":"array","items":{"type":"object"}}},"required":["customer_id","conflicts"]}},
    "initiate_data_export": {"name":"initiate_data_export","description":"Initiate data export for migration","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"total_gb":{"type":"number"},"files":{"type":"integer"}},"required":["customer_id","total_gb"]}},
    "require_customer_signoff": {"name":"require_customer_signoff","description":"Require customer written sign-off on breaking changes","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"items":{"type":"array","items":{"type":"string"}}},"required":["customer_id","items"]}},
    "proceed_migration": {"name":"proceed_migration","description":"[SINGLE-CALL] Execute migration cutover","input_schema":{"type":"object","properties":{"subscription_id":{"type":"string"},"confirmed":{"type":"boolean"}},"required":["subscription_id","confirmed"]}},
    "pause_migration": {"name":"pause_migration","description":"Pause migration pending resolution","input_schema":{"type":"object","properties":{"subscription_id":{"type":"string"},"reason":{"type":"string"}},"required":["subscription_id","reason"]}},
    "calculate_export_files": {"name":"calculate_export_files","description":"Calculate number of export files needed","input_schema":{"type":"object","properties":{"total_gb":{"type":"number"},"max_file_gb":{"type":"number"}},"required":["total_gb","max_file_gb"]}},
    "get_dispute": {"name":"get_dispute","description":"Get dispute details","input_schema":{"type":"object","properties":{"dispute_id":{"type":"string"}},"required":["dispute_id"]}},
    "get_change_orders": {"name":"get_change_orders","description":"Get change orders for dispute","input_schema":{"type":"object","properties":{"dispute_id":{"type":"string"}},"required":["dispute_id"]}},
    "get_retention": {"name":"get_retention","description":"Get retention payment details","input_schema":{"type":"object","properties":{"dispute_id":{"type":"string"}},"required":["dispute_id"]}},
    "pay_change_order": {"name":"pay_change_order","description":"[SINGLE-CALL] Pay a change order","input_schema":{"type":"object","properties":{"co_id":{"type":"string"},"amount":{"type":"number"},"payee":{"type":"string"}},"required":["co_id","amount","payee"]}},
    "appoint_mediator": {"name":"appoint_mediator","description":"Appoint neutral mediator for dispute","input_schema":{"type":"object","properties":{"dispute_id":{"type":"string"},"dispute_amount":{"type":"number"}},"required":["dispute_id","dispute_amount"]}},
    "document_co_validity": {"name":"document_co_validity","description":"Document change order validity determination","input_schema":{"type":"object","properties":{"co_id":{"type":"string"},"valid":{"type":"boolean"},"reason":{"type":"string"}},"required":["co_id","valid","reason"]}},
    "freeze_retention": {"name":"freeze_retention","description":"Freeze retention payment until dispute resolved","input_schema":{"type":"object","properties":{"holder":{"type":"string"},"amount":{"type":"number"},"until":{"type":"string"}},"required":["holder","amount"]}},
    "schedule_mediation": {"name":"schedule_mediation","description":"Schedule formal mediation session","input_schema":{"type":"object","properties":{"dispute_id":{"type":"string"},"parties":{"type":"array","items":{"type":"string"}},"amount":{"type":"number"}},"required":["dispute_id","parties","amount"]}},
    "release_retention": {"name":"release_retention","description":"Release retention payment","input_schema":{"type":"object","properties":{"holder":{"type":"string"},"amount":{"type":"number"}},"required":["holder","amount"]}},
    "get_deferred_revenue": {"name":"get_deferred_revenue","description":"Get deferred revenue contracts","input_schema":{"type":"object","properties":{"period":{"type":"string"}},"required":["period"]}},
    "get_fixed_assets": {"name":"get_fixed_assets","description":"Get fixed asset depreciation schedules","input_schema":{"type":"object","properties":{},"required":[]}},
    "get_fx_transactions": {"name":"get_fx_transactions","description":"Get FX transactions for period","input_schema":{"type":"object","properties":{"period":{"type":"string"}},"required":["period"]}},
    "get_accruals": {"name":"get_accruals","description":"Get pending accruals","input_schema":{"type":"object","properties":{},"required":[]}},
    "post_journal_entry": {"name":"post_journal_entry","description":"[SINGLE-CALL per type] Post journal entry to ledger","input_schema":{"type":"object","properties":{"type":{"type":"string"},"debit_account":{"type":"string"},"credit_account":{"type":"string"},"amount":{"type":"number"},"description":{"type":"string"}},"required":["type","amount"]}},
    "calculate_recognition": {"name":"calculate_recognition","description":"Calculate revenue recognition amount","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"},"period":{"type":"string"}},"required":["contract_id","period"]}},
    "run_trial_balance": {"name":"run_trial_balance","description":"Run trial balance to verify debits=credits","input_schema":{"type":"object","properties":{"period":{"type":"string"}},"required":["period"]}},
    "close_period": {"name":"close_period","description":"[SINGLE-CALL] Close accounting period","input_schema":{"type":"object","properties":{"period":{"type":"string"},"confirmed":{"type":"boolean"}},"required":["period"]}},
    "get_backlog": {"name":"get_backlog","description":"Get product backlog stories","input_schema":{"type":"object","properties":{},"required":[]}},
    "get_team_capacity": {"name":"get_team_capacity","description":"Get team capacity including PTO adjustments","input_schema":{"type":"object","properties":{"sprint_id":{"type":"string"}},"required":["sprint_id"]}},
    "calculate_sprint_capacity": {"name":"calculate_sprint_capacity","description":"Calculate velocity-adjusted sprint capacity","input_schema":{"type":"object","properties":{"raw_capacity":{"type":"number"},"historical_capacity":{"type":"number"},"velocity_avg":{"type":"number"}},"required":["raw_capacity","historical_capacity","velocity_avg"]}},
    "create_jira_ticket": {"name":"create_jira_ticket","description":"Create Jira ticket for story","input_schema":{"type":"object","properties":{"story_id":{"type":"string"},"title":{"type":"string"},"estimate":{"type":"integer"},"sprint":{"type":"string"},"dependencies":{"type":"array","items":{"type":"string"}}},"required":["story_id","title","estimate"]}},
    "assign_to_sprint": {"name":"assign_to_sprint","description":"Assign story to sprint","input_schema":{"type":"object","properties":{"story_id":{"type":"string"},"sprint_id":{"type":"string"}},"required":["story_id","sprint_id"]}},
    "flag_sprint_risk": {"name":"flag_sprint_risk","description":"Flag sprint capacity or dependency risk","input_schema":{"type":"object","properties":{"risk":{"type":"string"},"affected_stories":{"type":"array","items":{"type":"string"}},"mitigation":{"type":"string"}},"required":["risk"]}},
    "document_dependency_graph": {"name":"document_dependency_graph","description":"Document story dependency graph","input_schema":{"type":"object","properties":{"dependencies":{"type":"object"}},"required":["dependencies"]}},
    "get_ar_aging": {"name":"get_ar_aging","description":"Get AR aging report","input_schema":{"type":"object","properties":{},"required":[]}},
    "send_reminder_email": {"name":"send_reminder_email","description":"Send payment reminder email","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"invoice_id":{"type":"string"}},"required":["customer_id","invoice_id"]}},
    "make_collection_call": {"name":"make_collection_call","description":"Make collection phone call","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"notes":{"type":"string"}},"required":["customer_id"]}},
    "place_order_hold": {"name":"place_order_hold","description":"Place hold on new orders for customer","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reason":{"type":"string"}},"required":["customer_id"]}},
    "send_to_collections": {"name":"send_to_collections","description":"Send account to collections agency","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"amount":{"type":"number"}},"required":["customer_id","amount"]}},
    "write_off_bad_debt": {"name":"write_off_bad_debt","description":"[SINGLE-CALL] Write off bad debt","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"amount":{"type":"number"},"reason":{"type":"string"}},"required":["customer_id","amount","reason"]}},
    "file_proof_of_claim": {"name":"file_proof_of_claim","description":"File proof of claim in bankruptcy court","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"court":{"type":"string"},"amount":{"type":"number"}},"required":["customer_id","amount"]}},
    "stop_collections": {"name":"stop_collections","description":"Immediately stop all collection activity","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reason":{"type":"string"}},"required":["customer_id","reason"]}},
    "notify_legal": {"name":"notify_legal","description":"Notify legal team","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"matter":{"type":"string"}},"required":["customer_id","matter"]}},
    "charge_late_fee": {"name":"charge_late_fee","description":"Charge late fee to customer","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"invoice_id":{"type":"string"},"fee_amount":{"type":"number"},"fee_pct":{"type":"number"}},"required":["customer_id","invoice_id","fee_amount"]}},
    "send_formal_notice": {"name":"send_formal_notice","description":"Send formal delinquency notice","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"invoice_id":{"type":"string"},"amount":{"type":"number"}},"required":["customer_id","invoice_id"]}},
    "set_cure_deadline": {"name":"set_cure_deadline","description":"Set cure period deadline","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"days":{"type":"integer"},"deadline_date":{"type":"string"}},"required":["customer_id","days"]}},
    "request_payment_method_update": {"name":"request_payment_method_update","description":"Request customer update payment method","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"reason":{"type":"string"}},"required":["customer_id"]}},
    "escalate_dispute": {"name":"escalate_dispute","description":"Escalate disputed invoice to dispute resolution","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"disputed_amount":{"type":"number"},"undisputed_amount":{"type":"number"}},"required":["customer_id"]}},
    "get_incident": {"name":"get_incident","description":"Get incident details","input_schema":{"type":"object","properties":{"incident_id":{"type":"string"}},"required":["incident_id"]}},
    "get_deployments": {"name":"get_deployments","description":"Get recent deployments","input_schema":{"type":"object","properties":{"hours_back":{"type":"integer"}},"required":[]}},
    "get_logs": {"name":"get_logs","description":"Get service logs","input_schema":{"type":"object","properties":{"service":{"type":"string"},"since":{"type":"string"}},"required":["service"]}},
    "get_product_history": {"name":"get_product_history","description":"Get product price/status history","input_schema":{"type":"object","properties":{"product_id":{"type":"string"}},"required":["product_id"]}},
    "create_rca_document": {"name":"create_rca_document","description":"Create Root Cause Analysis document","input_schema":{"type":"object","properties":{"incident_id":{"type":"string"},"root_cause":{"type":"string"},"contributing_factors":{"type":"array","items":{"type":"string"}},"red_herrings":{"type":"array","items":{"type":"string"}},"timeline":{"type":"string"}},"required":["incident_id","root_cause"]}},
    "submit_change_request": {"name":"submit_change_request","description":"Submit change request","input_schema":{"type":"object","properties":{"type":{"type":"string","enum":["hotfix","architectural","process"]},"service":{"type":"string"},"action":{"type":"string"},"urgency":{"type":"string"}},"required":["type","action"]}},
    "rollback_deployment": {"name":"rollback_deployment","description":"[SINGLE-CALL] Rollback a deployment","input_schema":{"type":"object","properties":{"deploy_id":{"type":"string"},"reason":{"type":"string"}},"required":["deploy_id","reason"]}},
    "flush_cache": {"name":"flush_cache","description":"Flush cache for specified keys/service","input_schema":{"type":"object","properties":{"service":{"type":"string"},"keys":{"type":"array","items":{"type":"string"}}},"required":["service"]}},
    "notify_stakeholders": {"name":"notify_stakeholders","description":"Notify stakeholders of incident status","input_schema":{"type":"object","properties":{"incident_id":{"type":"string"},"message":{"type":"string"},"stakeholders":{"type":"array","items":{"type":"string"}}},"required":["incident_id","message"]}},
    "get_deck_versions": {"name":"get_deck_versions","description":"Get all QBR deck versions","input_schema":{"type":"object","properties":{},"required":[]}},
    "get_internal_data": {"name":"get_internal_data","description":"Get internal metrics and NDA status","input_schema":{"type":"object","properties":{},"required":[]}},
    "reconcile_metrics": {"name":"reconcile_metrics","description":"Reconcile metrics across deck versions","input_schema":{"type":"object","properties":{"metric":{"type":"string"}},"required":[]}},
    "create_deck_executive": {"name":"create_deck_executive","description":"Create executive internal deck","input_schema":{"type":"object","properties":{"revenue":{"type":"number"},"nps":{"type":"integer"},"incidents":{"type":"integer"},"risk_accounts":{"type":"array","items":{"type":"string"}}},"required":["revenue","nps"]}},
    "create_deck_board": {"name":"create_deck_board","description":"Create board presentation deck","input_schema":{"type":"object","properties":{"revenue_bookings":{"type":"number"},"revenue_recognized":{"type":"number"},"nps":{"type":"integer"},"risk_section":{"type":"string"}},"required":["revenue_recognized"]}},
    "create_deck_client_facing": {"name":"create_deck_client_facing","description":"Create client-facing deck","input_schema":{"type":"object","properties":{"revenue":{"type":"number"},"nps":{"type":"integer"},"client_id":{"type":"string"}},"required":["revenue","nps","client_id"]}},
    "create_reconciliation_memo": {"name":"create_reconciliation_memo","description":"Create reconciliation memo explaining discrepancies","input_schema":{"type":"object","properties":{"discrepancies":{"type":"array","items":{"type":"object"}}},"required":["discrepancies"]}},
    "flag_nda_violation": {"name":"flag_nda_violation","description":"Flag NDA client name appearing in inappropriate deck","input_schema":{"type":"object","properties":{"client_name":{"type":"string"},"deck_type":{"type":"string"}},"required":["client_name","deck_type"]}},
    "flag_data_discrepancy": {"name":"flag_data_discrepancy","description":"Flag data discrepancy between deck versions","input_schema":{"type":"object","properties":{"metric":{"type":"string"},"versions":{"type":"array","items":{"type":"string"}},"values":{"type":"object"}},"required":["metric"]}},
    # ── Retail tau-bench tools (task_16-task_20) ──────────────────────────────
    "find_user_by_email": {"name":"find_user_by_email","description":"Find retail customer user ID by email address","input_schema":{"type":"object","properties":{"email":{"type":"string"}},"required":["email"]}},
    "find_user_by_name_zip": {"name":"find_user_by_name_zip","description":"Find retail customer user ID by full name and zip code","input_schema":{"type":"object","properties":{"name":{"type":"string"},"zip":{"type":"string"}},"required":["name","zip"]}},
    "get_order_details": {"name":"get_order_details","description":"Get full details of a retail order including items, status, and payment","input_schema":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},
    "get_product_details": {"name":"get_product_details","description":"Get product details including all available variants and prices","input_schema":{"type":"object","properties":{"product_id":{"type":"string"}},"required":["product_id"]}},
    "get_user_details": {"name":"get_user_details","description":"Get customer profile including payment methods, address, and order history","input_schema":{"type":"object","properties":{"user_id":{"type":"string"}},"required":["user_id"]}},
    "list_product_types": {"name":"list_product_types","description":"List all available product types in the retail catalog","input_schema":{"type":"object","properties":{},"required":[]}},
    "return_order_items": {"name":"return_order_items","description":"Submit a return request for delivered order items with specified refund method","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"item_ids":{"type":"array","items":{"type":"string"}},"refund_method":{"type":"string"}},"required":["order_id","item_ids","refund_method"]}},
    "cancel_pending_order": {"name":"cancel_pending_order","description":"Cancel a pending order with a valid reason; refunds to original payment method","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string","enum":["no longer needed","ordered by mistake"]}},"required":["order_id","reason"]}},
    "exchange_order_items": {"name":"exchange_order_items","description":"Exchange delivered order items for same product type with different options","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"item_id":{"type":"string"},"new_product_id":{"type":"string"},"new_variant":{"type":"object"},"payment_method":{"type":"string"}},"required":["order_id","item_id","new_product_id","new_variant"]}},
    "modify_pending_order_items": {"name":"modify_pending_order_items","description":"[SINGLE-CALL] Modify item variants in a pending order. Can only be called ONCE per session.","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"modifications":{"type":"array","items":{"type":"object","properties":{"item_id":{"type":"string"},"new_variant":{"type":"object"}}}},"payment_method":{"type":"string"}},"required":["order_id","modifications"]}},
    "modify_pending_order_address": {"name":"modify_pending_order_address","description":"Update shipping address on a pending order","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"new_address":{"type":"string"}},"required":["order_id","new_address"]}},
    "modify_pending_order_payment": {"name":"modify_pending_order_payment","description":"Change payment method on a pending order to a different on-file method","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"new_payment_method":{"type":"string"}},"required":["order_id","new_payment_method"]}},
    # ── Airline tau-bench tools (task_21-task_23) ──────────────────────────────
    "search_direct_flights": {"name":"search_direct_flights","description":"Search for direct (non-stop) flights between two airports on a given date","input_schema":{"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"},"date":{"type":"string"},"cabin":{"type":"string"}},"required":["origin","destination","date"]}},
    "search_onestop_flights": {"name":"search_onestop_flights","description":"Search for one-stop connecting flights between two airports on a given date","input_schema":{"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"},"date":{"type":"string"},"cabin":{"type":"string"}},"required":["origin","destination","date"]}},
    "get_flight_details": {"name":"get_flight_details","description":"Get details for a specific flight by flight ID","input_schema":{"type":"object","properties":{"flight_id":{"type":"string"}},"required":["flight_id"]}},
    "list_airports": {"name":"list_airports","description":"List all airports and their IATA codes","input_schema":{"type":"object","properties":{},"required":[]}},
    "calculate_fare": {"name":"calculate_fare","description":"Calculate total fare including base fare, baggage fees, and optional insurance","input_schema":{"type":"object","properties":{"flight_id":{"type":"string"},"cabin":{"type":"string"},"bags":{"type":"integer"},"insurance":{"type":"boolean"},"passengers":{"type":"integer"}},"required":["flight_id","bags"]}},
    "book_flight": {"name":"book_flight","description":"Book a flight reservation with passenger and payment details","input_schema":{"type":"object","properties":{"flight_id":{"type":"string"},"cabin":{"type":"string"},"passengers":{"type":"array","items":{"type":"object"}},"bags":{"type":"integer"},"insurance":{"type":"boolean"},"payment_methods":{"type":"array","items":{"type":"object"}}},"required":["flight_id","cabin","passengers","bags"]}},
    "get_reservation_details": {"name":"get_reservation_details","description":"Get full details of an airline reservation including flights, passengers, and payment","input_schema":{"type":"object","properties":{"reservation_id":{"type":"string"}},"required":["reservation_id"]}},
    "update_reservation_flights": {"name":"update_reservation_flights","description":"Update cabin class or specific flights in a reservation (not allowed for basic economy)","input_schema":{"type":"object","properties":{"reservation_id":{"type":"string"},"new_cabin":{"type":"string"},"new_flight_id":{"type":"string"}},"required":["reservation_id"]}},
    "update_reservation_baggages": {"name":"update_reservation_baggages","description":"Add checked bags to a reservation (bags can only be added, not removed)","input_schema":{"type":"object","properties":{"reservation_id":{"type":"string"},"bags":{"type":"integer"}},"required":["reservation_id","bags"]}},
    "cancel_reservation": {"name":"cancel_reservation","description":"Cancel an airline reservation; eligibility depends on ticket type and insurance","input_schema":{"type":"object","properties":{"reservation_id":{"type":"string"},"reason":{"type":"string"}},"required":["reservation_id","reason"]}},
    # ── Banking tools (task_24-task_25) ───────────────────────────────────────
    "verify_account_identity": {"name":"verify_account_identity","description":"Verify customer identity for a bank account","input_schema":{"type":"object","properties":{"account_id":{"type":"string"},"email":{"type":"string"}},"required":["account_id"]}},
    "check_wire_limits": {"name":"check_wire_limits","description":"Check daily wire transfer limits and usage for an account","input_schema":{"type":"object","properties":{"account_id":{"type":"string"},"amount":{"type":"number"}},"required":["account_id","amount"]}},
    "initiate_wire_transfer": {"name":"initiate_wire_transfer","description":"Initiate a wire transfer from account to external account","input_schema":{"type":"object","properties":{"from_account":{"type":"string"},"to_account":{"type":"string"},"amount":{"type":"number"},"routing_number":{"type":"string"}},"required":["from_account","to_account","amount"]}},
    "send_wire_confirmation": {"name":"send_wire_confirmation","description":"Send wire transfer confirmation to customer email","input_schema":{"type":"object","properties":{"account_id":{"type":"string"},"email":{"type":"string"},"transfer_details":{"type":"object"}},"required":["account_id","email"]}},
    "get_flagged_transactions": {"name":"get_flagged_transactions","description":"Get all flagged transactions for a card or account","input_schema":{"type":"object","properties":{"card_id":{"type":"string"},"account_id":{"type":"string"}},"required":[]}},
    "review_transaction_with_customer": {"name":"review_transaction_with_customer","description":"Log that a flagged transaction was reviewed with and confirmed by the customer","input_schema":{"type":"object","properties":{"txn_id":{"type":"string"},"card_id":{"type":"string"},"customer_confirmed":{"type":"boolean"},"note":{"type":"string"}},"required":["txn_id","customer_confirmed"]}},
    "unflag_transaction": {"name":"unflag_transaction","description":"Remove fraud flag from a transaction after customer confirmation","input_schema":{"type":"object","properties":{"txn_id":{"type":"string"},"reason":{"type":"string"}},"required":["txn_id","reason"]}},
    "flag_for_fraud_team": {"name":"flag_for_fraud_team","description":"Escalate a transaction to the fraud team for investigation","input_schema":{"type":"object","properties":{"txn_id":{"type":"string"},"reason":{"type":"string"}},"required":["txn_id","reason"]}},
    # ── HR tools (task_26-task_27) ─────────────────────────────────────────────
    "get_employee_details": {"name":"get_employee_details","description":"Retrieve employee details including department, manager, and PTO balance","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"}},"required":["employee_id"]}},
    "check_pto_balance": {"name":"check_pto_balance","description":"Check employee PTO balance and validate if sufficient for request","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"days_requested":{"type":"integer"}},"required":["employee_id","days_requested"]}},
    "check_team_calendar": {"name":"check_team_calendar","description":"Check team calendar for blackout dates or conflicts during date range","input_schema":{"type":"object","properties":{"team_id":{"type":"string"},"start_date":{"type":"string"},"end_date":{"type":"string"}},"required":["start_date","end_date"]}},
    "approve_pto_request": {"name":"approve_pto_request","description":"Approve a PTO request for an employee","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"start_date":{"type":"string"},"end_date":{"type":"string"},"days":{"type":"integer"}},"required":["employee_id","start_date","end_date","days"]}},
    "deny_pto_request": {"name":"deny_pto_request","description":"Deny a PTO request with reason","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"reason":{"type":"string"}},"required":["employee_id","reason"]}},
    "notify_employee": {"name":"notify_employee","description":"Send notification to employee about PTO or HR decision","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"message":{"type":"string"},"subject":{"type":"string"}},"required":["employee_id","message"]}},
    "notify_manager": {"name":"notify_manager","description":"Send notification to manager about team member PTO or HR decision","input_schema":{"type":"object","properties":{"manager_id":{"type":"string"},"employee_id":{"type":"string"},"message":{"type":"string"}},"required":["manager_id","employee_id","message"]}},
    "get_expense_report": {"name":"get_expense_report","description":"Retrieve expense report details including line items and receipts","input_schema":{"type":"object","properties":{"report_id":{"type":"string"}},"required":["report_id"]}},
    "validate_receipts": {"name":"validate_receipts","description":"Validate that all required receipts are present and match line items","input_schema":{"type":"object","properties":{"report_id":{"type":"string"}},"required":["report_id"]}},
    "check_dept_limit": {"name":"check_dept_limit","description":"Check department expense limit and compare to report total","input_schema":{"type":"object","properties":{"department":{"type":"string"},"amount":{"type":"number"},"limit_type":{"type":"string"}},"required":["department","amount"]}},
    "approve_expense_report": {"name":"approve_expense_report","description":"Approve expense report for reimbursement","input_schema":{"type":"object","properties":{"report_id":{"type":"string"},"approved_by":{"type":"string"}},"required":["report_id"]}},
    "deny_expense_report": {"name":"deny_expense_report","description":"Deny expense report with reason","input_schema":{"type":"object","properties":{"report_id":{"type":"string"},"reason":{"type":"string"}},"required":["report_id","reason"]}},
    "schedule_reimbursement": {"name":"schedule_reimbursement","description":"Schedule reimbursement payment to employee direct deposit account","input_schema":{"type":"object","properties":{"report_id":{"type":"string"},"deposit_account":{"type":"string"},"amount":{"type":"number"}},"required":["report_id","deposit_account","amount"]}},
    # ── Healthcare tools (task_28-task_29) ────────────────────────────────────
    "verify_patient_identity": {"name":"verify_patient_identity","description":"Verify patient identity by patient ID, name, and date of birth","input_schema":{"type":"object","properties":{"patient_id":{"type":"string"},"name":{"type":"string"},"dob":{"type":"string"}},"required":["patient_id"]}},
    "check_provider_availability": {"name":"check_provider_availability","description":"Check available appointment slots for a healthcare provider","input_schema":{"type":"object","properties":{"provider_id":{"type":"string"},"start_date":{"type":"string"},"end_date":{"type":"string"}},"required":["provider_id"]}},
    "check_insurance_authorization": {"name":"check_insurance_authorization","description":"Verify insurance coverage and authorization for a specialty or service","input_schema":{"type":"object","properties":{"insurance_id":{"type":"string"},"specialty":{"type":"string"},"service_type":{"type":"string"}},"required":["insurance_id"]}},
    "schedule_appointment": {"name":"schedule_appointment","description":"Schedule a patient appointment with a provider at a specific date and time","input_schema":{"type":"object","properties":{"patient_id":{"type":"string"},"provider_id":{"type":"string"},"date":{"type":"string"},"time":{"type":"string"}},"required":["patient_id","provider_id","date","time"]}},
    "send_appointment_confirmation": {"name":"send_appointment_confirmation","description":"Send appointment confirmation to patient with details","input_schema":{"type":"object","properties":{"patient_id":{"type":"string"},"appointment_details":{"type":"object"}},"required":["patient_id"]}},
    "get_patient_details": {"name":"get_patient_details","description":"Retrieve patient demographics and insurance information","input_schema":{"type":"object","properties":{"patient_id":{"type":"string"}},"required":["patient_id"]}},
    "get_prescription_details": {"name":"get_prescription_details","description":"Retrieve prescription details including medication, dosage, and refill history","input_schema":{"type":"object","properties":{"rx_id":{"type":"string"}},"required":["rx_id"]}},
    "check_refill_eligibility": {"name":"check_refill_eligibility","description":"Check if prescription is eligible for refill based on days since last fill","input_schema":{"type":"object","properties":{"rx_id":{"type":"string"},"last_fill_date":{"type":"string"},"supply_days":{"type":"integer"}},"required":["rx_id"]}},
    "verify_insurance_coverage": {"name":"verify_insurance_coverage","description":"Verify insurance coverage for a specific medication and copay amount","input_schema":{"type":"object","properties":{"insurance_id":{"type":"string"},"medication":{"type":"string"}},"required":["insurance_id","medication"]}},
    "process_refill": {"name":"process_refill","description":"Process a prescription refill request","input_schema":{"type":"object","properties":{"rx_id":{"type":"string"},"patient_id":{"type":"string"}},"required":["rx_id","patient_id"]}},
    "notify_patient": {"name":"notify_patient","description":"Notify patient of prescription or appointment status","input_schema":{"type":"object","properties":{"patient_id":{"type":"string"},"message":{"type":"string"},"copay":{"type":"number"}},"required":["patient_id","message"]}},
    "deny_refill": {"name":"deny_refill","description":"Deny prescription refill request with reason","input_schema":{"type":"object","properties":{"rx_id":{"type":"string"},"reason":{"type":"string"}},"required":["rx_id","reason"]}},
    # ── Supply Chain tools (task_30-task_31) ──────────────────────────────────
    "check_inventory_level": {"name":"check_inventory_level","description":"Check current inventory level for a SKU at a warehouse","input_schema":{"type":"object","properties":{"sku":{"type":"string"},"warehouse":{"type":"string"}},"required":["sku"]}},
    "compare_to_reorder_point": {"name":"compare_to_reorder_point","description":"Compare current inventory to reorder point and determine if reorder is needed","input_schema":{"type":"object","properties":{"sku":{"type":"string"},"current_qty":{"type":"integer"},"reorder_point":{"type":"integer"}},"required":["sku","current_qty","reorder_point"]}},
    "get_vendor_details": {"name":"get_vendor_details","description":"Retrieve vendor contact and ordering details","input_schema":{"type":"object","properties":{"vendor_id":{"type":"string"}},"required":["vendor_id"]}},
    "create_purchase_order": {"name":"create_purchase_order","description":"Create a purchase order for inventory replenishment","input_schema":{"type":"object","properties":{"vendor_id":{"type":"string"},"sku":{"type":"string"},"quantity":{"type":"integer"},"unit_cost":{"type":"number"}},"required":["vendor_id","sku","quantity","unit_cost"]}},
    "send_po_to_vendor": {"name":"send_po_to_vendor","description":"Send a purchase order to the vendor via email or EDI","input_schema":{"type":"object","properties":{"po_id":{"type":"string"},"vendor_id":{"type":"string"}},"required":["po_id","vendor_id"]}},
    "update_inventory_status": {"name":"update_inventory_status","description":"Update inventory status (e.g., to po_pending, in_transit, received)","input_schema":{"type":"object","properties":{"sku":{"type":"string"},"warehouse":{"type":"string"},"status":{"type":"string"}},"required":["sku","status"]}},
    "get_received_goods_record": {"name":"get_received_goods_record","description":"Retrieve the received goods record for a purchase order","input_schema":{"type":"object","properties":{"po_id":{"type":"string"}},"required":["po_id"]}},
    "calculate_discrepancy": {"name":"calculate_discrepancy","description":"Calculate the unit and dollar discrepancy between ordered and received quantities","input_schema":{"type":"object","properties":{"po_id":{"type":"string"},"ordered_qty":{"type":"integer"},"received_qty":{"type":"integer"},"unit_price":{"type":"number"}},"required":["po_id","ordered_qty","received_qty","unit_price"]}},
    "flag_invoice_discrepancy": {"name":"flag_invoice_discrepancy","description":"Flag an invoice as disputed due to quantity or price discrepancy","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"reason":{"type":"string"},"discrepancy_amount":{"type":"number"}},"required":["invoice_id","reason"]}},
    "contact_vendor": {"name":"contact_vendor","description":"Contact vendor to notify of discrepancy or issue with an order","input_schema":{"type":"object","properties":{"vendor_id":{"type":"string"},"po_id":{"type":"string"},"message":{"type":"string"}},"required":["vendor_id","message"]}},
    "create_debit_memo": {"name":"create_debit_memo","description":"Create a debit memo to reduce payment amount due to invoice discrepancy","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"amount":{"type":"number"},"reason":{"type":"string"}},"required":["invoice_id","amount","reason"]}},
    "approve_invoice": {"name":"approve_invoice","description":"Approve an invoice for payment in full","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"}},"required":["invoice_id"]}},
    # ── Customer Success tools (task_32) ──────────────────────────────────────
    "get_customer_contract": {"name":"get_customer_contract","description":"Retrieve customer contract including SLA tier, fees, and support terms","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},
    "get_ticket_details": {"name":"get_ticket_details","description":"Get details of a support ticket including open time, status, and severity","input_schema":{"type":"object","properties":{"ticket_id":{"type":"string"}},"required":["ticket_id"]}},
    "calculate_sla_breach": {"name":"calculate_sla_breach","description":"Calculate SLA breach duration given ticket open hours and SLA hours","input_schema":{"type":"object","properties":{"ticket_id":{"type":"string"},"hours_open":{"type":"number"},"sla_hours":{"type":"number"}},"required":["ticket_id","hours_open","sla_hours"]}},
    "escalate_to_senior_engineer": {"name":"escalate_to_senior_engineer","description":"Escalate ticket to senior engineer for immediate resolution","input_schema":{"type":"object","properties":{"ticket_id":{"type":"string"},"reason":{"type":"string"}},"required":["ticket_id"]}},
    "apply_sla_credit": {"name":"apply_sla_credit","description":"Apply SLA breach credit to customer account","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"ticket_id":{"type":"string"},"amount":{"type":"number"}},"required":["customer_id","ticket_id","amount"]}},
    "notify_customer_with_apology": {"name":"notify_customer_with_apology","description":"Send apology and SLA breach resolution details to customer","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"},"ticket_id":{"type":"string"},"credit_amount":{"type":"number"},"message":{"type":"string"}},"required":["customer_id","ticket_id"]}},
    # ── Legal tools (task_33) ─────────────────────────────────────────────────
    "get_contract_details": {"name":"get_contract_details","description":"Retrieve contract details including all clauses","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"}},"required":["contract_id"]}},
    "get_policy_document": {"name":"get_policy_document","description":"Retrieve a company policy document by policy ID","input_schema":{"type":"object","properties":{"policy_id":{"type":"string"}},"required":["policy_id"]}},
    "identify_policy_violation": {"name":"identify_policy_violation","description":"Identify which contract clauses violate specified policy","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"},"policy_id":{"type":"string"},"clause_id":{"type":"string"}},"required":["contract_id","policy_id"]}},
    "flag_contract_clause": {"name":"flag_contract_clause","description":"Flag a specific contract clause as non-compliant","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"},"clause_id":{"type":"string"},"reason":{"type":"string"}},"required":["contract_id","clause_id","reason"]}},
    "request_amendment_from_vendor": {"name":"request_amendment_from_vendor","description":"Request contract amendment from vendor to address non-compliant clause","input_schema":{"type":"object","properties":{"vendor_id":{"type":"string"},"contract_id":{"type":"string"},"clause_id":{"type":"string"},"requested_change":{"type":"string"}},"required":["vendor_id","contract_id","clause_id"]}},
    "notify_legal_team": {"name":"notify_legal_team","description":"Notify legal team of contract compliance issue","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"},"issue_summary":{"type":"string"},"clause_id":{"type":"string"}},"required":["contract_id","issue_summary"]}},
    "approve_contract": {"name":"approve_contract","description":"Approve contract for signing (only if all compliance issues resolved)","input_schema":{"type":"object","properties":{"contract_id":{"type":"string"},"approved_by":{"type":"string"}},"required":["contract_id"]}},
    # ── Finance AP tools (task_34) ────────────────────────────────────────────
    "match_invoice_to_po": {"name":"match_invoice_to_po","description":"Perform 3-way match of invoice against PO and goods receipt","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"po_id":{"type":"string"},"receipt_id":{"type":"string"}},"required":["invoice_id","po_id"]}},
    "validate_payment_terms": {"name":"validate_payment_terms","description":"Validate invoice payment terms and calculate due date","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"terms":{"type":"string"},"invoice_date":{"type":"string"}},"required":["invoice_id"]}},
    "approve_invoice_for_payment": {"name":"approve_invoice_for_payment","description":"Approve a matched invoice for payment processing","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"approved_by":{"type":"string"}},"required":["invoice_id"]}},
    "schedule_payment": {"name":"schedule_payment","description":"Schedule ACH or check payment for an approved invoice","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"amount":{"type":"number"},"payment_date":{"type":"string"},"payment_method":{"type":"string"},"bank_ref":{"type":"string"}},"required":["invoice_id","amount","payment_date"]}},
    "update_accounts_payable": {"name":"update_accounts_payable","description":"Update accounts payable ledger to reflect scheduled payment","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"payment_date":{"type":"string"},"amount":{"type":"number"}},"required":["invoice_id"]}},
    "reject_invoice": {"name":"reject_invoice","description":"Reject an invoice with reason (mismatched amount, missing receipt, etc.)","input_schema":{"type":"object","properties":{"invoice_id":{"type":"string"},"reason":{"type":"string"}},"required":["invoice_id","reason"]}},
    # ── IT Helpdesk tools (task_35) ───────────────────────────────────────────
    "verify_employee_identity": {"name":"verify_employee_identity","description":"Verify employee identity and confirm manager authorization on file","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"manager_id":{"type":"string"}},"required":["employee_id"]}},
    "get_account_status": {"name":"get_account_status","description":"Get IT account status including lock status and lock reason","input_schema":{"type":"object","properties":{"user_id":{"type":"string"}},"required":["user_id"]}},
    "unlock_account": {"name":"unlock_account","description":"Unlock a locked IT user account","input_schema":{"type":"object","properties":{"user_id":{"type":"string"},"reason":{"type":"string"}},"required":["user_id"]}},
    "reset_password": {"name":"reset_password","description":"Reset user account password with temporary password and optional force-change flag","input_schema":{"type":"object","properties":{"user_id":{"type":"string"},"force_change_on_login":{"type":"boolean"}},"required":["user_id","force_change_on_login"]}},
    "reset_mfa_token": {"name":"reset_mfa_token","description":"Reset multi-factor authentication token for a user account","input_schema":{"type":"object","properties":{"user_id":{"type":"string"}},"required":["user_id"]}},
    "notify_employee_via_email": {"name":"notify_employee_via_email","description":"Send email to employee with account reset instructions","input_schema":{"type":"object","properties":{"employee_id":{"type":"string"},"email":{"type":"string"},"message":{"type":"string"}},"required":["employee_id","email"]}},
    "escalate_to_security_team": {"name":"escalate_to_security_team","description":"Escalate account issue to security team for investigation","input_schema":{"type":"object","properties":{"user_id":{"type":"string"},"reason":{"type":"string"}},"required":["user_id","reason"]}},
    # ── Marketing tools (task_36) ─────────────────────────────────────────────
    "get_campaign_details": {"name":"get_campaign_details","description":"Retrieve marketing campaign details including budget request and start date","input_schema":{"type":"object","properties":{"campaign_id":{"type":"string"}},"required":["campaign_id"]}},
    "check_department_budget": {"name":"check_department_budget","description":"Check department budget totals, spent, and remaining for a quarter","input_schema":{"type":"object","properties":{"department":{"type":"string"},"quarter":{"type":"string"}},"required":["department","quarter"]}},
    "verify_budget_available": {"name":"verify_budget_available","description":"Verify that requested budget amount is available within department remaining budget","input_schema":{"type":"object","properties":{"department":{"type":"string"},"quarter":{"type":"string"},"amount_requested":{"type":"number"}},"required":["department","amount_requested"]}},
    "approve_campaign_budget": {"name":"approve_campaign_budget","description":"Approve campaign budget request","input_schema":{"type":"object","properties":{"campaign_id":{"type":"string"},"amount":{"type":"number"},"approved_by":{"type":"string"}},"required":["campaign_id","amount"]}},
    "deny_campaign_budget": {"name":"deny_campaign_budget","description":"Deny campaign budget request with reason","input_schema":{"type":"object","properties":{"campaign_id":{"type":"string"},"reason":{"type":"string"}},"required":["campaign_id","reason"]}},
    "allocate_budget_funds": {"name":"allocate_budget_funds","description":"Allocate approved budget funds and update department spending","input_schema":{"type":"object","properties":{"department":{"type":"string"},"quarter":{"type":"string"},"campaign_id":{"type":"string"},"amount":{"type":"number"}},"required":["department","quarter","campaign_id","amount"]}},
    "notify_campaign_manager": {"name":"notify_campaign_manager","description":"Notify campaign manager of budget decision","input_schema":{"type":"object","properties":{"campaign_id":{"type":"string"},"manager_id":{"type":"string"},"status":{"type":"string"},"message":{"type":"string"}},"required":["campaign_id","manager_id","status"]}},
    # ── Real Estate tools (task_37) ───────────────────────────────────────────
    "get_lease_details": {"name":"get_lease_details","description":"Retrieve lease details including tenant, unit, rates, and expiry date","input_schema":{"type":"object","properties":{"lease_id":{"type":"string"}},"required":["lease_id"]}},
    "check_renewal_policy": {"name":"check_renewal_policy","description":"Retrieve lease renewal policy including maximum increase percentages","input_schema":{"type":"object","properties":{"property_id":{"type":"string"}},"required":[]}},
    "calculate_increase": {"name":"calculate_increase","description":"Calculate percentage rent increase from old to new rate","input_schema":{"type":"object","properties":{"old_rate":{"type":"number"},"new_rate":{"type":"number"}},"required":["old_rate","new_rate"]}},
    "request_vp_approval": {"name":"request_vp_approval","description":"Request VP approval for lease renewal that exceeds policy maximum increase","input_schema":{"type":"object","properties":{"lease_id":{"type":"string"},"old_rate":{"type":"number"},"new_rate":{"type":"number"},"pct_increase":{"type":"number"},"tenant_id":{"type":"string"}},"required":["lease_id","old_rate","new_rate","pct_increase"]}},
    "sign_lease_renewal": {"name":"sign_lease_renewal","description":"Execute and sign a lease renewal (requires VP approval if increase > 7%)","input_schema":{"type":"object","properties":{"lease_id":{"type":"string"},"new_rate":{"type":"number"},"term_months":{"type":"integer"},"vp_approval_id":{"type":"string"}},"required":["lease_id","new_rate","term_months"]}},
    "notify_tenant_pending_approval": {"name":"notify_tenant_pending_approval","description":"Notify tenant that renewal is pending VP approval","input_schema":{"type":"object","properties":{"lease_id":{"type":"string"},"tenant_email":{"type":"string"},"message":{"type":"string"}},"required":["lease_id","tenant_email"]}},
    "notify_tenant_approved": {"name":"notify_tenant_approved","description":"Notify tenant that lease renewal has been approved and signed","input_schema":{"type":"object","properties":{"lease_id":{"type":"string"},"tenant_email":{"type":"string"},"new_rate":{"type":"number"}},"required":["lease_id","tenant_email"]}},
    # ── E-commerce Chargeback tools (task_38) ────────────────────────────────
    "get_chargeback_details": {"name":"get_chargeback_details","description":"Retrieve chargeback details including reason, order, and amount","input_schema":{"type":"object","properties":{"chargeback_id":{"type":"string"}},"required":["chargeback_id"]}},
    "get_tracking_info": {"name":"get_tracking_info","description":"Retrieve shipping tracking information and delivery status for an order","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"tracking_number":{"type":"string"}},"required":["order_id"]}},
    "dispute_chargeback": {"name":"dispute_chargeback","description":"File a dispute against a chargeback with supporting evidence","input_schema":{"type":"object","properties":{"chargeback_id":{"type":"string"},"reason":{"type":"string"},"evidence_summary":{"type":"string"}},"required":["chargeback_id","reason"]}},
    "submit_chargeback_evidence": {"name":"submit_chargeback_evidence","description":"Submit formal evidence package to card network to dispute chargeback","input_schema":{"type":"object","properties":{"chargeback_id":{"type":"string"},"evidence_type":{"type":"string"},"tracking_number":{"type":"string"},"delivery_date":{"type":"string"},"delivery_address":{"type":"string"}},"required":["chargeback_id","evidence_type"]}},
    "process_refund": {"name":"process_refund","description":"Process a refund to customer if chargeback is valid","input_schema":{"type":"object","properties":{"order_id":{"type":"string"},"amount":{"type":"number"},"reason":{"type":"string"}},"required":["order_id","amount","reason"]}},
    "get_account_details": {"name":"get_account_details","description":"Retrieve bank account or card account details","input_schema":{"type":"object","properties":{"account_id":{"type":"string"},"card_id":{"type":"string"}},"required":[]}},
    "get_purchase_order": {"name":"get_purchase_order","description":"Retrieve purchase order details by PO ID","input_schema":{"type":"object","properties":{"po_id":{"type":"string"}},"required":["po_id"]}},
    "get_customer_details": {"name":"get_customer_details","description":"Retrieve customer details including contact info and order history","input_schema":{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}},

}


class ToolError(Exception):
    pass


def _get_db(session_id: str) -> sqlite3.Connection:
    db_path = DB_DIR / f"session_{session_id}.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            task_id TEXT,
            tool_name TEXT NOT NULL,
            params_json TEXT,
            result_json TEXT,
            called_at REAL,
            is_violation INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_meta (
            session_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at REAL
        )
    """)
    conn.commit()
    return conn


def _get_task_id(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT task_id FROM session_meta WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row[0] if row else None


def _set_task_id(conn: sqlite3.Connection, session_id: str, task_id: str):
    conn.execute(
        "INSERT OR REPLACE INTO session_meta (session_id, task_id, created_at) VALUES (?, ?, ?)",
        (session_id, task_id, time.time())
    )
    conn.commit()


def _count_tool_calls(conn: sqlite3.Connection, session_id: str, tool_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM session_tool_calls WHERE session_id = ? AND tool_name = ? AND is_violation = 0",
        (session_id, tool_name)
    ).fetchone()
    return row[0] if row else 0


def _record_call(conn: sqlite3.Connection, session_id: str, task_id: str | None,
                 tool_name: str, params: dict, result: Any, is_violation: bool = False):
    conn.execute(
        "INSERT INTO session_tool_calls (session_id, task_id, tool_name, params_json, result_json, called_at, is_violation) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, task_id, tool_name, json.dumps(params), json.dumps(result), time.time(), int(is_violation))
    )
    conn.commit()


def get_tools_for_session(session_id: str, task_id: str | None = None) -> list[dict]:
    """Return Anthropic-format tool schemas for the active task."""
    if task_id is None:
        conn = _get_db(session_id)
        task_id = _get_task_id(conn, session_id)
        conn.close()
    if task_id is None:
        return list(TOOL_SCHEMAS.values())
    tool_names = TASK_TOOL_MAP.get(task_id, [])
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


def invoke_tool(session_id: str, tool_name: str, params: dict, task_id: str | None = None) -> Any:
    """
    Invoke a tool for the given session.

    Raises ToolError('CONSTRAINT_VIOLATION') if a single-call tool is called more than once.
    """
    conn = _get_db(session_id)

    # Set or retrieve task_id for session
    stored_task_id = _get_task_id(conn, session_id)
    if stored_task_id is None and task_id:
        _set_task_id(conn, session_id, task_id)
        stored_task_id = task_id
    effective_task_id = stored_task_id or task_id

    # Enforce single-call constraint
    if tool_name in SINGLE_CALL_TOOLS:
        call_count = _count_tool_calls(conn, session_id, tool_name)
        if call_count >= 1:
            _record_call(conn, session_id, effective_task_id, tool_name, params,
                        {"error": "CONSTRAINT_VIOLATION"}, is_violation=True)
            conn.close()
            raise ToolError("CONSTRAINT_VIOLATION")

    # Route to tool implementation
    result = _dispatch_tool(tool_name, params, session_id, effective_task_id)
    _record_call(conn, session_id, effective_task_id, tool_name, params, result)
    conn.close()
    return result


def _dispatch_tool(tool_name: str, params: dict, session_id: str, task_id: str | None) -> Any:
    """Dispatch to tool implementation. Returns result dict."""
    # Load fixture data for the session
    fixture_data = _load_fixture(task_id) if task_id else {}

    # Generic read tools — return data from fixture
    read_tools = {
        "get_order": ("orders", "id", params.get("order_id")),
        "get_order_items": ("order_items", "order_id", params.get("order_id")),
        "get_product_variants": ("products", "id", params.get("product_id")),
        "get_gift_card_balance": ("gift_cards", "id", params.get("gift_card_id")),
        "get_purchase_request": ("purchase_requests", "id", params.get("request_id")),
        "get_employee": ("employees", "id", params.get("employee_id")),
        "get_claim": ("claims", "id", params.get("claim_id")),
        "get_policy": ("policies", "id", params.get("policy_id")),
        "get_rider": ("riders", "policy_id", params.get("policy_id")),
        "get_invoice": ("invoices", "id", params.get("invoice_id")),
        "get_vendor": ("vendors", "id", params.get("vendor_id")),
        "get_dispute": ("disputes", "id", params.get("dispute_id")),
        "get_change_orders": ("change_orders", "dispute_id", params.get("dispute_id")),
        "get_retention": ("retention", "dispute_id", params.get("dispute_id")),
        "get_incident": ("incident", None, None),
        "get_deployments": ("deployments", None, None),
        "get_logs": ("logs", None, None),
        "get_product_history": ("product_history", None, None),
        "get_deck_versions": ("deck_versions", None, None),
        "get_internal_data": ("internal_data", None, None),
        "get_backlog": ("product_backlog", None, None),
        "get_ar_aging": ("ar_aging", None, None),
        "get_sla_config": ("sla_configs", None, None),
        "get_incidents": ("incidents", None, None),
        "get_deferred_revenue": ("deferred_revenue", None, None),
        "get_fixed_assets": ("fixed_assets", None, None),
        "get_fx_transactions": ("fx_transactions", None, None),
        "get_accruals": ("accruals_pending", None, None),
        "get_subscription": ("subscriptions", "id", params.get("subscription_id")),
        "get_current_features": ("current_features", None, None),
        "get_new_plan_features": ("new_plan_features", None, None),
        # Retail tau-bench tools (task_16-task_20)
        "get_order_details": ("orders", "id", params.get("order_id")),
        "get_product_details": ("products", "id", params.get("product_id")),
        "get_user_details": ("users", "id", params.get("user_id")),
        "list_product_types": ("products", None, None),
        "list_airports": ("airports", None, None),
        # Airline tau-bench tools (task_21-task_23)
        "search_direct_flights": ("flights", None, None),
        "search_onestop_flights": ("flights", None, None),
        "get_flight_details": ("flights", "flight_id", params.get("flight_id")),
        "get_reservation_details": ("reservations", "id", params.get("reservation_id")),
        # Banking tools (task_24-task_25)
        "get_account_details": ("accounts", "account_id", params.get("account_id") or params.get("card_id")),
        "get_flagged_transactions": ("transactions", "card_id", params.get("card_id") or params.get("account_id")),
        # HR tools (task_26-task_27)
        "get_employee_details": ("employees", "employee_id", params.get("employee_id")),
        "get_expense_report": ("expense_reports", "report_id", params.get("report_id")),
        "check_team_calendar": ("team_calendar", None, None),
        # Healthcare tools (task_28-task_29)
        "get_patient_details": ("patients", "patient_id", params.get("patient_id")),
        "get_prescription_details": ("prescriptions", "rx_id", params.get("rx_id")),
        # Supply Chain tools (task_30-task_31)
        "get_vendor_details": ("vendors", "vendor_id", params.get("vendor_id")),
        "get_received_goods_record": ("received_goods", "po_id", params.get("po_id")),
        "get_purchase_order": ("purchase_orders", "po_id", params.get("po_id")),
        # Customer Success tools (task_32)
        "get_customer_contract": ("customers", "customer_id", params.get("customer_id")),
        "get_ticket_details": ("tickets", "ticket_id", params.get("ticket_id")),
        # Legal tools (task_33)
        "get_contract_details": ("contracts", "contract_id", params.get("contract_id")),
        "get_policy_document": ("policies", "policy_id", params.get("policy_id")),
        # Finance AP tools (task_34)
        "get_invoice": ("invoices", "invoice_id", params.get("invoice_id")),
        # IT tools (task_35)
        "get_account_status": ("accounts", "user_id", params.get("user_id")),
        # Marketing tools (task_36)
        "get_campaign_details": ("campaigns", "campaign_id", params.get("campaign_id")),
        "check_department_budget": ("department_budgets", "department", params.get("department")),
        # Real Estate tools (task_37)
        "get_lease_details": ("leases", "lease_id", params.get("lease_id")),
        "check_renewal_policy": ("renewal_policy", None, None),
        # E-commerce Chargeback tools (task_38)
        "get_customer_details": ("customers", "customer_id", params.get("customer_id")),
        "get_chargeback_details": ("chargebacks", "chargeback_id", params.get("chargeback_id")),
        "get_tracking_info": ("tracking", "order_id", params.get("order_id")),
    }

    if tool_name in read_tools:
        table, key, val = read_tools[tool_name]
        data = fixture_data.get(table, [])
        if key and val:
            if isinstance(data, list):
                matches = [r for r in data if r.get(key) == val]
                return matches[0] if len(matches) == 1 else matches
            return data
        return data

    # Special read tools
    if tool_name == "get_approval_chain":
        dept = params.get("department", "")
        chains = fixture_data.get("approval_chains", [])
        for c in chains:
            if c.get("department", "").lower() == dept.lower():
                return c
        return chains[0] if chains else {}

    if tool_name == "get_budget":
        dept = params.get("department", "")
        budgets = fixture_data.get("budgets", [])
        for b in budgets:
            if b.get("department", "").lower() == dept.lower():
                return b
        return budgets[0] if budgets else {}

    if tool_name == "check_employee_pto":
        emp_id = params.get("employee_id", "")
        employees = fixture_data.get("employees", [])
        pto_records = fixture_data.get("pto_records", [])
        emp = next((e for e in employees if e.get("id") == emp_id or emp_id.lower() in e.get("name","").lower()), None)
        pto = next((p for p in pto_records if p.get("employee_id") == (emp.get("id") if emp else emp_id)), None)
        return {"employee": emp, "pto_active": emp.get("pto_active", False) if emp else False, "pto_record": pto}

    if tool_name == "check_fraud_flag":
        cust_id = params.get("customer_id", "")
        history = fixture_data.get("claim_history", [])
        policy = fixture_data.get("fraud_policy", {})
        claims_count = len([c for c in history if c.get("customer_id") == cust_id])
        threshold = policy.get("claims_threshold", 3)
        flagged = claims_count >= threshold
        return {"customer_id": cust_id, "claims_count": claims_count, "threshold": threshold, "flagged": flagged, "action": policy.get("action", "")}

    if tool_name == "get_fx_rate":
        date = params.get("date", "")
        rates = fixture_data.get("fx_rates", [])
        rate_rec = next((r for r in rates if r.get("date") == date), rates[0] if rates else {})
        from_c = params.get("from_currency", "EUR")
        to_c = params.get("to_currency", "USD")
        key = f"{from_c}_{to_c}"
        return {"date": date, "rate": rate_rec.get(key), "from": from_c, "to": to_c}

    if tool_name == "get_pto_balance":
        emp_id = params.get("employee_id", "")
        employees = fixture_data.get("employees", [])
        emp = next((e for e in employees if e.get("id") == emp_id), None)
        policies = fixture_data.get("hr_policies", {})
        return {"employee": emp, "pto_balance": emp.get("pto_balance_days") if emp else None, "policies": policies}

    if tool_name in ["get_team_capacity", "calculate_sprint_capacity"]:
        team = fixture_data.get("team", [])
        velocity = fixture_data.get("velocity_avg", 39.5)
        return {"team": team, "velocity_avg": velocity, "sprints": fixture_data.get("sprints", [])}

    if tool_name == "run_pep_check":
        return fixture_data.get("pep_check", {"confidence": 0, "match": "No match"})

    if tool_name == "get_transaction_history":
        return fixture_data.get("transactions", [])

    if tool_name in ["check_oncall_availability", "get_oncall"]:
        return fixture_data.get("oncall", [])

    # Retail: find user by email
    if tool_name == "find_user_by_email":
        email = params.get("email", "").lower()
        users = fixture_data.get("users", [])
        user = next((u for u in users if u.get("email", "").lower() == email), None)
        if user:
            return {"user_id": user["id"], "name": user["name"], "found": True}
        return {"found": False, "error": "No user found with that email"}

    # Retail: find user by name+zip
    if tool_name == "find_user_by_name_zip":
        name = params.get("name", "").lower()
        zip_code = params.get("zip", "")
        users = fixture_data.get("users", [])
        user = next((u for u in users if u.get("name", "").lower() == name and u.get("zip") == zip_code), None)
        if user:
            return {"user_id": user["id"], "name": user["name"], "found": True}
        return {"found": False, "error": "No user found with that name and zip"}

    # Airline: search direct flights (filter by date and after departure time if given)
    if tool_name in ("search_direct_flights", "search_onestop_flights"):
        flights = fixture_data.get("flights", [])
        origin = params.get("origin", "")
        dest = params.get("destination", "")
        date = params.get("date", "")
        results = [f for f in flights if f.get("origin") == origin and f.get("destination") == dest]
        if date:
            results = [f for f in results if f.get("date") == date]
        if tool_name == "search_direct_flights":
            results = [f for f in results if f.get("stops", 0) == 0]
        else:
            results = [f for f in results if f.get("stops", 0) > 0]
        return results

    # Airline: calculate fare
    if tool_name == "calculate_fare":
        flight_id = params.get("flight_id", "")
        bags = params.get("bags", 1)
        insurance = params.get("insurance", False)
        passengers = params.get("passengers", 1)
        flights = fixture_data.get("flights", [])
        flight = next((f for f in flights if f.get("flight_id") == flight_id), flights[0] if flights else {})
        base_fare = flight.get("base_fare", 0)
        baggage_policy = fixture_data.get("baggage_policy", {})
        free_bags = baggage_policy.get("economy_free_bags", 1)
        extra_bag_fee = baggage_policy.get("extra_bag_fee", 50)
        extra_bags = max(0, bags - free_bags)
        baggage_fee = extra_bags * extra_bag_fee
        insurance_fee = 30 * passengers if insurance else 0
        total = base_fare + baggage_fee + insurance_fee
        return {"base_fare": base_fare, "baggage_fee": baggage_fee, "insurance_fee": insurance_fee, "total": total}

    # Write/action tools — acknowledge and return success
    return {"status": "ok", "tool": tool_name, "params": params}


def _load_fixture(task_id: str) -> dict:
    """Load fixture JSON for a task_id."""
    fixture_path = Path(__file__).parent / "fixtures" / f"{task_id}_fixture.json"
    if fixture_path.exists():
        return json.loads(fixture_path.read_text())
    return {}


def get_session_actions_log(session_id: str) -> list[dict]:
    """Return ordered list of all non-violation tool calls for scoring."""
    conn = _get_db(session_id)
    rows = conn.execute(
        "SELECT tool_name, params_json, result_json, called_at FROM session_tool_calls WHERE session_id = ? ORDER BY id",
        (session_id,)
    ).fetchall()
    conn.close()
    return [
        {"tool": row[0], "params": json.loads(row[1]), "result": json.loads(row[2]) if row[2] else None, "called_at": row[3]}
        for row in rows
    ]


def get_constraint_violations(session_id: str) -> list[str]:
    """Return list of tool names that had CONSTRAINT_VIOLATION."""
    conn = _get_db(session_id)
    rows = conn.execute(
        "SELECT tool_name FROM session_tool_calls WHERE session_id = ? AND is_violation = 1",
        (session_id,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── Async shims — used by server.py and task_manager.py ─────────────────────

async def seed_session_db(session_id: str, fixture: dict, task_id: str = "", difficulty: str = "none") -> None:
    """Initialise session DB and associate task_id so tools load the right fixture."""
    fixture = DifficultyEngine().apply(fixture, task_id, difficulty)
    conn = _get_db(session_id)
    if task_id:
        _set_task_id(conn, session_id, task_id)
    conn.close()


async def call_tool(tool_name: str, params: dict, session_id: str) -> Any:
    """Async wrapper around invoke_tool. Returns error dict on CONSTRAINT_VIOLATION."""
    try:
        return invoke_tool(session_id, tool_name, params)
    except ToolError as e:
        return {"error": str(e), "type": "CONSTRAINT_VIOLATION"}


async def get_tool_calls(session_id: str) -> list[dict]:
    """Async wrapper around get_session_actions_log."""
    return get_session_actions_log(session_id)

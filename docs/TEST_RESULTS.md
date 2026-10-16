# Test results

Recorded on 2026-10-16 (Windows 11, Docker Desktop 29.6, PostgreSQL 16 in Docker, Python 3.14).

## Runs

| Where | Command | Result |
|---|---|---|
| Development checkout | `pytest -q` | **151 passed**, 0 failed (~15 s) |
| **Fresh `git clone`** + new virtualenv + `pip install -r requirements.txt` | `pytest -q` | **151 passed** (~16 s); fixtures verified byte-identical to the supplied originals (sha256) |
| **Docker image** (`docker compose build api`) | `docker compose run --rm api pytest -q` | **151 passed** (~12 s) |
| `docker compose up -d api` | `GET /health`, `GET /exceptions` | migrations ran (nothing pending), API served PO-501 / 505 / 506 = 8 / 24 / 10 EA |

## Extra concurrency evidence (not part of the suite)

| Check | Result |
|---|---|
| `test_concurrent_imports_of_same_file_store_each_fact_once` repeated 5× | 5/5 passed |
| Stress: 25 rounds × 6 simultaneous detection runs on fresh data | 0 failed runs; every round exactly 3 exceptions, 3 tasks, 3 `ASSIGN` events |
| Same stress with the earlier *targeted* `ON CONFLICT (business key)` | 1 of 150 runs crashed with `UniqueViolation` on `exceptions_pkey` — the bug that motivated the fix |
| `test_two_simultaneous_completions_one_wins_one_is_told` repeated 10× | 10/10 passed |

## All 151 tests

**`tests/integration/test_data_quality.py`** (6)

- ✅ `test_clean_fixtures_have_no_data_quality_issues`
- ✅ `test_unknown_product_is_a_data_quality_issue_not_an_exception`
- ✅ `test_conflicting_duplicate_blocks_evaluation`
- ✅ `test_two_source_records_for_one_po_line_are_ambiguous`
- ✅ `test_rejected_rows_are_listed`
- ✅ `test_unmapped_employee_location_is_listed`

**`tests/integration/test_exceptions_api.py`** (5)

- ✅ `test_list_exceptions_with_filters`
- ✅ `test_exception_detail_shows_source_records_down_to_the_csv_line`
- ✅ `test_unknown_exception_is_404`
- ✅ `test_ai_summary_is_unavailable_by_default`
- ✅ `test_ai_summary_with_a_provider_is_validated_against_the_evidence`

**`tests/integration/test_idempotency.py`** (2)

- ✅ `test_importing_and_detecting_twice_changes_nothing`
- ✅ `test_concurrent_detection_creates_each_exception_once`

**`tests/integration/test_imports.py`** (10)

- ✅ `test_fixture_pack_counts`
- ✅ `test_reimport_is_all_duplicates_and_changes_nothing`
- ✅ `test_bad_rows_are_rejected_with_reasons_and_good_rows_load`
- ✅ `test_missing_column_fails_the_import_visibly`
- ✅ `test_import_report_via_api`
- ✅ `test_conflicting_duplicate_keeps_original_and_records_conflict`
- ✅ `test_concurrent_imports_of_same_file_store_each_fact_once`
- ✅ `test_crash_mid_import_rolls_back_rows_and_marks_failed`
- ✅ `test_unknown_dataset_is_404`
- ✅ `test_non_utf8_file_is_a_bad_csv`

**`tests/integration/test_mapping.py`** (4)

- ✅ `test_alias_index_loads_from_the_database`
- ✅ `test_every_supplied_line_maps_cleanly`
- ✅ `test_alias_index_is_tenant_scoped`
- ✅ `test_open_alias_conflict_is_visible_to_the_resolver`

**`tests/integration/test_scenarios.py`** (10)

- ✅ `test_six_required_scenarios`
- ✅ `test_every_finding_traces_to_source_rows`
- ✅ `test_detection_run_is_recorded_with_every_line`
- ✅ `test_late_receipt_closes_the_finding_without_duplicating`
- ✅ `test_shortfall_that_reappears_reopens_the_same_finding`
- ✅ `test_detection_over_http`
- ✅ `test_tasks_are_routed_to_eligible_people`
- ✅ `test_routing_reasoning_is_persisted`
- ✅ `test_no_eligible_person_leaves_task_for_manual_assignment`
- ✅ `test_late_receipt_cancels_the_open_task`

**`tests/integration/test_schema.py`** (9)

- ✅ `test_source_identity_is_unique_per_tenant`
- ✅ `test_ordered_qty_must_be_positive`
- ✅ `test_one_exception_per_business_key`
- ✅ `test_one_task_per_exception_and_type`
- ✅ `test_task_past_created_needs_assignee[ASSIGNED]`
- ✅ `test_task_past_created_needs_assignee[IN_PROGRESS]`
- ✅ `test_task_past_created_needs_assignee[VERIFIED]`
- ✅ `test_unknown_task_status_is_rejected`
- ✅ `test_conflict_for_same_incoming_version_is_recorded_once`

**`tests/integration/test_smoke.py`** (5)

- ✅ `test_health`
- ✅ `test_settings_defaults_use_frozen_time`
- ✅ `test_settings_reject_naive_evaluation_time`
- ✅ `test_migrate_is_idempotent`
- ✅ `test_unknown_route_uses_standard_error_shape`

**`tests/integration/test_task_workflow.py`** (14)

- ✅ `test_list_tasks_by_assignee_and_location`
- ✅ `test_task_detail_and_unknown_task`
- ✅ `test_only_assignee_can_work_the_task`
- ✅ `test_unknown_or_inactive_actor_is_forbidden`
- ✅ `test_actor_header_is_required`
- ✅ `test_cannot_skip_states`
- ✅ `test_full_lifecycle_with_retry_safety_and_verification`
- ✅ `test_repeated_acknowledge_is_a_safe_replay`
- ✅ `test_complete_requires_known_code_and_real_evidence`
- ✅ `test_block_unblock_and_reject_paths`
- ✅ `test_manager_cannot_verify_own_task_but_regional_can`
- ✅ `test_reassign_records_reason_and_rejects_ineligible`
- ✅ `test_manual_assignment_cannot_bypass_eligibility`
- ✅ `test_two_simultaneous_completions_one_wins_one_is_told`

**`tests/unit/test_ai_summary.py`** (6)

- ✅ `test_valid_output_is_returned`
- ✅ `test_invented_record_id_is_rejected`
- ✅ `test_schema_invalid_output_is_rejected`
- ✅ `test_model_cannot_smuggle_decisions_in_extra_fields`
- ✅ `test_provider_failure_and_timeout_degrade_gracefully`
- ✅ `test_package_contains_only_case_evidence`

**`tests/unit/test_eligibility.py`** (12)

- ✅ `test_store_014_goes_to_the_active_on_shift_associate`
- ✅ `test_store_015_falls_back_to_the_store_manager`
- ✅ `test_nobody_eligible_means_no_assignee`
- ✅ `test_stale_missing_or_future_samples_are_not_trusted`
- ✅ `test_sample_exactly_at_the_age_limit_is_still_fresh`
- ✅ `test_freshness_limit_comes_from_the_policy`
- ✅ `test_inactive_and_off_shift_reasons_accumulate`
- ✅ `test_tie_breaks_on_lowest_employee_id`
- ✅ `test_regional_role_is_never_auto_assigned_store_work`
- ✅ `test_employee_with_unmapped_store_is_not_a_candidate`
- ✅ `test_decision_serialises_its_reasoning`
- ✅ `test_supervisors`

**`tests/unit/test_findings.py`** (5)

- ✅ `test_identity_is_stable_and_tenant_scoped`
- ✅ `test_finding_carries_numbers_and_identity`
- ✅ `test_finding_is_traceable_to_sources_and_mappings`
- ✅ `test_explanation_states_facts_only_and_unknowns_name_missing_evidence`
- ✅ `test_same_inputs_give_the_same_finding`

**`tests/unit/test_resolver.py`** (9)

- ✅ `test_box_converts_to_base_units`
- ✅ `test_source_facts_are_carried_through_unchanged`
- ✅ `test_receipt_maps_through_wms_aliases`
- ✅ `test_uom_mismatch_is_an_issue_not_a_guess`
- ✅ `test_unmapped_product_and_location`
- ✅ `test_issue_leaves_canonical_fields_empty`
- ✅ `test_ids_from_another_system_do_not_match`
- ✅ `test_conflicted_alias_blocks_mapping`
- ✅ `test_hrms_location_resolution`

**`tests/unit/test_rows.py`** (16)

- ✅ `test_valid_po_row_parses_types`
- ✅ `test_naive_timestamp_is_rejected`
- ✅ `test_bad_quantity_is_rejected[0]`
- ✅ `test_bad_quantity_is_rejected[-5]`
- ✅ `test_bad_quantity_is_rejected[abc]`
- ✅ `test_bad_quantity_is_rejected[]`
- ✅ `test_bad_quantity_is_rejected[NaN]`
- ✅ `test_blank_required_text_is_rejected[po_number]`
- ✅ `test_blank_required_text_is_rejected[source_record_id]`
- ✅ `test_status_is_free_text_so_unknown_values_reach_the_rule`
- ✅ `test_blank_optional_employee_location_becomes_none`
- ✅ `test_extra_columns_are_ignored`
- ✅ `test_hash_ignores_formatting_noise`
- ✅ `test_hash_changes_when_business_content_changes`
- ✅ `test_registry_keys_and_required_columns`
- ✅ `test_time_in_a_source_key_is_the_instant_not_the_spelling`

**`tests/unit/test_shortfall_rule.py`** (24)

- ✅ `test_rule_is_versioned`
- ✅ `test_partial_receipts_are_summed`
- ✅ `test_repeated_receipt_counts_once`
- ✅ `test_exact_match_is_not_a_shortfall`
- ✅ `test_deadline_boundary_is_strict[offset0-NOT_DUE]`
- ✅ `test_deadline_boundary_is_strict[offset1-NOT_DUE]`
- ✅ `test_deadline_boundary_is_strict[offset2-SHORTFALL]`
- ✅ `test_future_deadline_is_not_due`
- ✅ `test_cancelled_line_never_raises`
- ✅ `test_cancelled_wins_even_when_mapping_is_broken`
- ✅ `test_unknown_status_is_not_evaluable`
- ✅ `test_unposted_and_future_receipts_are_ignored_and_listed`
- ✅ `test_receipt_at_exactly_as_of_counts`
- ✅ `test_late_receipt_after_deadline_still_counts`
- ✅ `test_receipt_that_maps_elsewhere_makes_line_not_evaluable[override0]`
- ✅ `test_receipt_that_maps_elsewhere_makes_line_not_evaluable[override1]`
- ✅ `test_receipt_that_maps_elsewhere_makes_line_not_evaluable[override2]`
- ✅ `test_unmapped_counted_receipt_makes_line_not_evaluable`
- ✅ `test_unmapped_receipt_that_would_be_ignored_does_not_block`
- ✅ `test_unmapped_po_line_is_not_evaluable`
- ✅ `test_blocked_line_is_not_evaluable`
- ✅ `test_tolerance_is_respected`
- ✅ `test_over_receipt_is_not_a_shortfall`
- ✅ `test_result_does_not_depend_on_receipt_order`

**`tests/unit/test_state_machine.py`** (14)

- ✅ `test_happy_path_chain`
- ✅ `test_invalid_transitions_raise[ASSIGNED-complete]`
- ✅ `test_invalid_transitions_raise[CREATED-acknowledge]`
- ✅ `test_invalid_transitions_raise[COMPLETED-verify]`
- ✅ `test_invalid_transitions_raise[VERIFIED-reassign]`
- ✅ `test_invalid_transitions_raise[PENDING_VERIFICATION-complete]`
- ✅ `test_invalid_transitions_raise[IN_PROGRESS-fly]`
- ✅ `test_invalid_transitions_raise[PENDING_VERIFICATION-cancel]`
- ✅ `test_invalid_transitions_raise[CANCELLED-start]`
- ✅ `test_invalid_transitions_raise[ASSIGNED-assign]`
- ✅ `test_invalid_transitions_raise[VERIFIED-cancel]`
- ✅ `test_block_reject_cancel_reopen_paths`
- ✅ `test_reassign_is_allowed_from_every_open_state_only`
- ✅ `test_status_strings_are_accepted`

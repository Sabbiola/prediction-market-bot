# Model Card Template

## 1. Model identity

- model_name:
- model_version:
- owner:
- created_at_utc:
- code_commit_sha:

## 2. Intended use

- primary_use_case:
- supported_runtime_modes:
- unsupported_use_cases:
- safety_constraints:

## 3. Training data

- dataset_id:
- dataset_manifest_path:
- label_definition:
- split_definition:
- leakage_checks:

## 4. Features

- feature_groups:
- feature_freeze_time_rule:
- prohibited_features:

## 5. Benchmarks

- benchmark_market_implied:
- benchmark_fifty_fifty:
- benchmark_category_prior:
- benchmark_heuristic_prediction_agent:
- benchmark_research_only:
- benchmark_momentum_structure:

## 6. Evaluation metrics

- brier_score:
- log_loss:
- calibration_error:
- calibration_method:
- calibration_fit_split:
- calibration_eval_split:
- calibration_curve_before:
- calibration_curve_after:
- approval_rate:
- realized_edge_vs_expected_edge:
- paper_roi_after_fee_slippage:
- sandbox_roi_after_fee_slippage:

## 7. Promotion evidence

- stage_reached:
- gate_results:
- regression_checks:
- operational_impact_summary:

## 8. Risks and limitations

- known_failure_modes:
- market_regimes_where_model_is_weak:
- data_quality_limits:
- monitoring_requirements:

## 9. Deployment decision

- decision: [approved | rejected | needs_more_evidence]
- decision_date_utc:
- approvers:
- rationale:
- rollback_triggers:

## 10. Change log

- previous_version:
- what_changed:
- expected_effect:
- validation_done:

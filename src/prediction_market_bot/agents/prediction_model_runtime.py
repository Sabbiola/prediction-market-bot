from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


def _clamp_probability(value: float) -> float:
    return min(max(value, 1e-6), 1.0 - 1e-6)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    payload: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            payload.append(text)
    return tuple(payload)


class PredictionModelArtifactError(ValueError):
    """Raised when prediction artifact contracts are invalid."""


class _RuntimeBinaryModel(Protocol):
    @property
    def feature_names(self) -> tuple[str, ...]:
        ...

    def predict_proba(self, features: Mapping[str, float]) -> float:
        ...


@dataclass(slots=True, frozen=True)
class _LogisticRegressionRuntimeModel:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float

    def predict_proba(self, features: Mapping[str, float]) -> float:
        total = self.bias
        for index, name in enumerate(self.feature_names):
            raw = float(features.get(name, 0.0))
            centered = (raw - self.means[index]) / self.stds[index]
            total += self.weights[index] * centered
        if total >= 0.0:
            exp = math.exp(-total)
            return _clamp_probability(1.0 / (1.0 + exp))
        exp = math.exp(total)
        return _clamp_probability(exp / (1.0 + exp))


@dataclass(slots=True, frozen=True)
class _DecisionStumpRuntimeModel:
    feature_names: tuple[str, ...]
    threshold: float
    left_probability: float
    right_probability: float
    global_probability: float

    def predict_proba(self, features: Mapping[str, float]) -> float:
        if not self.feature_names:
            return _clamp_probability(self.global_probability)
        value = float(features.get(self.feature_names[0], 0.0))
        if value <= self.threshold:
            return _clamp_probability(self.left_probability)
        return _clamp_probability(self.right_probability)


@dataclass(slots=True, frozen=True)
class RuntimeCalibrationContract:
    calibration_version: str
    method: str
    parameters: Mapping[str, float | tuple[float, ...]]

    @classmethod
    def none(cls) -> "RuntimeCalibrationContract":
        return cls(
            calibration_version="none",
            method="none",
            parameters={},
        )

    def apply(self, probability: float) -> float:
        base = _clamp_probability(probability)
        method = self.method.strip().lower()
        if method in {"", "none"}:
            return base
        if method == "platt":
            a = _as_float(self.parameters.get("a"), default=1.0)
            b = _as_float(self.parameters.get("b"), default=0.0)
            logit = math.log(base / (1.0 - base))
            value = (a * logit) + b
            if value >= 0.0:
                exp = math.exp(-value)
                return _clamp_probability(1.0 / (1.0 + exp))
            exp = math.exp(value)
            return _clamp_probability(exp / (1.0 + exp))
        if method == "isotonic":
            boundaries_raw = self.parameters.get("boundaries")
            values_raw = self.parameters.get("values")
            if not isinstance(boundaries_raw, tuple) or not isinstance(values_raw, tuple):
                raise PredictionModelArtifactError("invalid_isotonic_calibration_parameters")
            if not boundaries_raw or not values_raw or len(boundaries_raw) != len(values_raw):
                raise PredictionModelArtifactError("invalid_isotonic_calibration_shape")
            for boundary, value in zip(boundaries_raw, values_raw, strict=False):
                if base <= boundary:
                    return _clamp_probability(value)
            return _clamp_probability(values_raw[-1])
        raise PredictionModelArtifactError(f"unsupported_calibration_method={self.method}")


@dataclass(slots=True, frozen=True)
class RuntimePredictionModelContract:
    artifact_path: Path
    model_name: str
    model_version: str
    feature_schema_version: str
    feature_columns: tuple[str, ...]
    required_features: tuple[str, ...]
    runtime_model: _RuntimeBinaryModel
    calibration: RuntimeCalibrationContract

    def parity_errors(
        self,
        *,
        runtime_feature_schema_version: str,
        runtime_features: Mapping[str, float],
    ) -> tuple[str, ...]:
        issues: list[str] = []
        if self.feature_schema_version != runtime_feature_schema_version:
            issues.append(
                "feature_schema_version_mismatch "
                f"artifact={self.feature_schema_version} runtime={runtime_feature_schema_version}"
            )
        missing = [name for name in self.required_features if name not in runtime_features]
        if missing:
            issues.append(f"missing_required_features={','.join(sorted(missing))}")
        invalid = [
            name
            for name in self.required_features
            if name in runtime_features and not math.isfinite(float(runtime_features[name]))
        ]
        if invalid:
            issues.append(f"invalid_non_finite_features={','.join(sorted(invalid))}")
        return tuple(issues)

    def predict_yes_probability(self, runtime_features: Mapping[str, float]) -> tuple[float, float]:
        raw_probability = self.runtime_model.predict_proba(runtime_features)
        calibrated_probability = self.calibration.apply(raw_probability)
        return _clamp_probability(raw_probability), _clamp_probability(calibrated_probability)


class PredictionModelArtifactLoader:
    MODEL_ARTIFACT_TYPE = "prediction_model_v2"
    CALIBRATION_ARTIFACT_TYPE = "prediction_calibration_v2"

    def load(
        self,
        artifact_path: str | Path,
        *,
        calibration_artifact_path: str | Path | None = None,
    ) -> RuntimePredictionModelContract:
        path = Path(artifact_path)
        payload = self._read_json(path)
        model_name = str(payload.get("model_name") or "").strip()
        if not model_name:
            raise PredictionModelArtifactError(f"missing_model_name path={path}")

        model_version = str(payload.get("model_version") or "").strip()
        if not model_version:
            model_version = "legacy"

        feature_schema_version = str(payload.get("feature_schema_version") or "").strip() or "v1"
        feature_columns = _as_str_tuple(payload.get("feature_columns"))
        if not feature_columns:
            raise PredictionModelArtifactError(f"missing_feature_columns path={path}")

        required_features = _as_str_tuple(payload.get("required_features")) or feature_columns
        algorithm_payload = payload.get("algorithm_payload")
        if not isinstance(algorithm_payload, Mapping):
            raise PredictionModelArtifactError(f"missing_algorithm_payload path={path}")

        runtime_model = self._build_runtime_model(algorithm_payload, feature_columns=feature_columns, path=path)
        calibration = RuntimeCalibrationContract.none()

        if calibration_artifact_path is not None and str(calibration_artifact_path).strip():
            calibration_payload = self._read_json(Path(calibration_artifact_path))
            calibration = self._parse_calibration(calibration_payload, source_path=Path(calibration_artifact_path))
        else:
            inline_calibration = payload.get("calibration")
            if isinstance(inline_calibration, Mapping):
                calibration = self._parse_calibration(inline_calibration, source_path=path)

        artifact_type = str(payload.get("artifact_type") or "").strip()
        if artifact_type and artifact_type != self.MODEL_ARTIFACT_TYPE:
            raise PredictionModelArtifactError(
                f"unsupported_model_artifact_type path={path} artifact_type={artifact_type}"
            )

        return RuntimePredictionModelContract(
            artifact_path=path,
            model_name=model_name,
            model_version=model_version,
            feature_schema_version=feature_schema_version,
            feature_columns=feature_columns,
            required_features=required_features,
            runtime_model=runtime_model,
            calibration=calibration,
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise PredictionModelArtifactError(f"artifact_not_found path={path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PredictionModelArtifactError(f"artifact_invalid_json path={path} error={exc}") from exc
        if not isinstance(raw, dict):
            raise PredictionModelArtifactError(f"artifact_root_not_mapping path={path}")
        return raw

    @staticmethod
    def _coerce_float_tuple(values: Any, *, name: str, path: Path) -> tuple[float, ...]:
        if not isinstance(values, list):
            raise PredictionModelArtifactError(f"invalid_{name}_type path={path}")
        payload: list[float] = []
        for item in values:
            payload.append(_as_float(item))
        return tuple(payload)

    def _build_runtime_model(
        self,
        payload: Mapping[str, Any],
        *,
        feature_columns: tuple[str, ...],
        path: Path,
    ) -> _RuntimeBinaryModel:
        algorithm = str(payload.get("algorithm") or "").strip().lower()
        if algorithm == "logistic_regression":
            feature_names = _as_str_tuple(payload.get("feature_names")) or feature_columns
            means = self._coerce_float_tuple(payload.get("means"), name="means", path=path)
            stds = self._coerce_float_tuple(payload.get("stds"), name="stds", path=path)
            weights = self._coerce_float_tuple(payload.get("weights"), name="weights", path=path)
            if len(feature_names) != len(means) or len(feature_names) != len(stds) or len(feature_names) != len(weights):
                raise PredictionModelArtifactError(
                    "invalid_logistic_shape "
                    f"path={path} features={len(feature_names)} means={len(means)} stds={len(stds)} weights={len(weights)}"
                )
            safe_stds = tuple(std if abs(std) > 1e-12 else 1.0 for std in stds)
            return _LogisticRegressionRuntimeModel(
                feature_names=feature_names,
                means=means,
                stds=safe_stds,
                weights=weights,
                bias=_as_float(payload.get("bias")),
            )
        if algorithm == "tree_baseline":
            feature_name = str(payload.get("feature_name") or "").strip()
            feature_names = (feature_name,) if feature_name else ()
            return _DecisionStumpRuntimeModel(
                feature_names=feature_names,
                threshold=_as_float(payload.get("threshold")),
                left_probability=_as_float(payload.get("left_probability"), default=0.5),
                right_probability=_as_float(payload.get("right_probability"), default=0.5),
                global_probability=_as_float(payload.get("global_probability"), default=0.5),
            )
        if algorithm == "xgboost_candidate":
            raise PredictionModelArtifactError(
                "unsupported_runtime_algorithm=xgboost_candidate "
                "export a logistic_regression or tree_baseline promoted artifact for runtime."
            )
        raise PredictionModelArtifactError(f"unsupported_runtime_algorithm={algorithm or 'unset'} path={path}")

    def _parse_calibration(self, payload: Mapping[str, Any], *, source_path: Path) -> RuntimeCalibrationContract:
        artifact_type = str(payload.get("artifact_type") or "").strip()
        if artifact_type and artifact_type != self.CALIBRATION_ARTIFACT_TYPE:
            raise PredictionModelArtifactError(
                f"unsupported_calibration_artifact_type path={source_path} artifact_type={artifact_type}"
            )
        method = str(payload.get("method") or "none").strip().lower()
        calibration_version = str(payload.get("calibration_version") or payload.get("run_id") or "").strip() or "unknown"
        parameters_raw = payload.get("parameters")
        if method in {"", "none"}:
            return RuntimeCalibrationContract.none()
        if method == "platt":
            if not isinstance(parameters_raw, Mapping):
                raise PredictionModelArtifactError(f"invalid_platt_parameters path={source_path}")
            return RuntimeCalibrationContract(
                calibration_version=calibration_version,
                method="platt",
                parameters={
                    "a": _as_float(parameters_raw.get("a"), default=1.0),
                    "b": _as_float(parameters_raw.get("b"), default=0.0),
                },
            )
        if method == "isotonic":
            if not isinstance(parameters_raw, Mapping):
                raise PredictionModelArtifactError(f"invalid_isotonic_parameters path={source_path}")
            boundaries = self._coerce_float_tuple(parameters_raw.get("boundaries"), name="boundaries", path=source_path)
            values = self._coerce_float_tuple(parameters_raw.get("values"), name="values", path=source_path)
            if len(boundaries) != len(values):
                raise PredictionModelArtifactError(f"invalid_isotonic_shape path={source_path}")
            return RuntimeCalibrationContract(
                calibration_version=calibration_version,
                method="isotonic",
                parameters={"boundaries": boundaries, "values": values},
            )
        raise PredictionModelArtifactError(
            f"unsupported_calibration_method path={source_path} method={method or 'unset'}"
        )

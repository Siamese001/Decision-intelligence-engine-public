"""Data contracts and schemas for Project LENS Single-Turbine Evaluation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PreFlightQualityResult:
    """Stage 0 pre-flight frame quality outcome."""
    is_legible: bool
    sharpness_score: float
    luminance_score: float
    has_text_tokens: bool
    defect_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelExtraction:
    """Extraction output from a single vision model."""
    manufacturer: str
    model_number: str
    serial_number: str
    confidence: float
    bounding_box_area: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


ModelExtractionResult = ModelExtraction


@dataclass(frozen=True)
class TurbinePhotoRecord:
    """Input record representing a component photograph with dual-model predictions."""
    photo_id: str
    turbine_id: str
    component_type: str
    mean_luminance: float
    laplacian_variance: float
    detected_token_count: int
    model_a: ModelExtraction
    model_b: ModelExtraction
    ground_truth_manufacturer: str
    ground_truth_model_number: str
    ground_truth_serial_number: str
    has_factory_serial: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TurbinePhotoRecord:
        return cls(
            photo_id=data["photo_id"],
            turbine_id=data["turbine_id"],
            component_type=data["component_type"],
            mean_luminance=float(data["mean_luminance"]),
            laplacian_variance=float(data["laplacian_variance"]),
            detected_token_count=int(data["detected_token_count"]),
            model_a=ModelExtraction(**data["model_a"]),
            model_b=ModelExtraction(**data["model_b"]),
            ground_truth_manufacturer=data["ground_truth_manufacturer"],
            ground_truth_model_number=data["ground_truth_model_number"],
            ground_truth_serial_number=data["ground_truth_serial_number"],
            has_factory_serial=bool(data.get("has_factory_serial", True)),
        )


@dataclass(frozen=True)
class ThresholdConfig:
    """Configuration governing Stage 0 and Stage 1/2 consensus rules."""
    name: str
    description: str
    min_laplacian_variance: float
    min_luminance: float
    max_luminance: float
    min_prefix_match_len: int
    min_serial_levenshtein: float
    require_exact_manufacturer: bool
    use_bounding_box_dominance: bool
    allow_zero_serial_exception: bool
    stp_confidence_threshold: float
    assisted_confidence_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ErrorSample:
    """Itemized error record detailing an extraction failure."""
    photo_id: str
    component_type: str
    field_name: str
    extracted_value: str
    ground_truth_value: str
    model_a_value: str
    model_b_value: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationOutcome:
    """Outcome of Stage 0 pre-flight check and Stage 1/2 dual-model reconciliation."""
    photo_id: str
    routing_tier: str
    resolved_manufacturer: Optional[str]
    resolved_model_number: Optional[str]
    resolved_serial_number: Optional[str]
    confidence_score: float
    is_auto_accepted: bool
    is_correct: bool
    is_capture_defect: bool
    defect_reason: Optional[str] = None
    error_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonMetrics:
    """Aggregated evaluation metrics and explicit denominators."""
    profile_name: str
    total_photos: int
    capture_defects: int
    legible_photos: int
    auto_accepted: int
    human_review: int
    correct_auto_accepted: int
    false_acceptances: int
    stp_rate: float
    checked_correctness_rate: float
    false_acceptance_rate: float
    review_volume_rate: float
    defect_rate: float
    error_samples: List[ErrorSample] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

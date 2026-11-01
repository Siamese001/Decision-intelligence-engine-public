"""Stage 1 & 2 Dual-Model Reconciliation Mesh for Project LENS."""
from __future__ import annotations

from typing import List, Optional, Tuple

from rapidfuzz.distance import Levenshtein

from dual_pipeline.lens_evaluation.models import (
    ReconciliationOutcome,
    ThresholdConfig,
    TurbinePhotoRecord,
)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes Levenshtein edit distance between two strings using rapidfuzz."""
    return int(Levenshtein.distance(s1, s2))


def string_similarity(s1: str, s2: str) -> float:
    """Computes normalized similarity [0.0, 1.0] based on Levenshtein distance."""
    s1_clean = s1.strip().upper()
    s2_clean = s2.strip().upper()
    if not s1_clean and not s2_clean:
        return 1.0
    return float(Levenshtein.normalized_similarity(s1_clean, s2_clean))


def common_prefix_length(s1: str, s2: str) -> int:
    """Calculates length of identical prefix between two strings."""
    length = 0
    for c1, c2 in zip(s1.strip().upper(), s2.strip().upper()):
        if c1 == c2:
            length += 1
        else:
            break
    return length


class DualModelReconciler:
    """Executes governed reconciliation between Model A (OCR) and Model B (Vision LLM)."""

    @classmethod
    def reconcile(
        cls,
        record: TurbinePhotoRecord,
        config: ThresholdConfig,
    ) -> ReconciliationOutcome:
        """Reconciles extraction predictions under given threshold profile."""
        mA = record.model_a
        mB = record.model_b

        # 1. Manufacturer Resolution
        mfg_resolved, mfg_agree = cls._resolve_manufacturer(mA, mB, config)

        # 2. Model Number Resolution
        model_resolved, model_agree = cls._resolve_model_number(mA, mB, config)

        # 3. Serial Number Resolution
        serial_resolved, serial_agree = cls._resolve_serial_number(
            mA, mB, record.has_factory_serial, config
        )

        # Calculate joint confidence
        base_conf = (mA.confidence + mB.confidence) / 2.0
        agreement_factor = (
            (1.0 if mfg_agree else 0.4)
            + (1.0 if model_agree else 0.4)
            + (1.0 if serial_agree else 0.4)
        ) / 3.0
        joint_confidence = base_conf * agreement_factor

        all_fields_resolved = (
            mfg_resolved is not None
            and model_resolved is not None
            and serial_resolved is not None
        )

        # Routing Decision
        if all_fields_resolved and joint_confidence >= config.stp_confidence_threshold:
            routing_tier = "ZONE_5A_STP"
            is_auto_accepted = True
        elif all_fields_resolved and joint_confidence >= config.assisted_confidence_threshold:
            routing_tier = "ZONE_5B_ASSISTED"
            is_auto_accepted = False
        else:
            routing_tier = "ZONE_5C_ESCALATED"
            is_auto_accepted = False

        # Correctness check against Ground Truth
        is_correct, error_fields = cls._check_correctness(
            mfg_resolved,
            model_resolved,
            serial_resolved,
            record,
            config,
        )

        return ReconciliationOutcome(
            photo_id=record.photo_id,
            routing_tier=routing_tier,
            resolved_manufacturer=mfg_resolved,
            resolved_model_number=model_resolved,
            resolved_serial_number=serial_resolved,
            confidence_score=round(joint_confidence, 4),
            is_auto_accepted=is_auto_accepted,
            is_correct=is_correct,
            is_capture_defect=False,
            defect_reason=None,
            error_fields=error_fields,
        )

    @classmethod
    def _resolve_manufacturer(cls, mA, mB, config: ThresholdConfig) -> Tuple[Optional[str], bool]:
        mA_mfg = mA.manufacturer.strip().upper()
        mB_mfg = mB.manufacturer.strip().upper()

        if mA_mfg == mB_mfg:
            return mA.manufacturer, True

        if config.require_exact_manufacturer:
            return None, False

        if config.use_bounding_box_dominance:
            # Dominant bounding box breaks tie between primary asset and peripheral label
            if mA.bounding_box_area >= 1.25 * mB.bounding_box_area:
                return mA.manufacturer, True
            elif mB.bounding_box_area >= 1.25 * mA.bounding_box_area:
                return mB.manufacturer, True
            # Higher confidence fallback
            if mA.confidence >= mB.confidence:
                return mA.manufacturer, False
            return mB.manufacturer, False

        return None, False

    @classmethod
    def _resolve_model_number(cls, mA, mB, config: ThresholdConfig) -> Tuple[Optional[str], bool]:
        mA_mod = mA.model_number.strip().upper()
        mB_mod = mB.model_number.strip().upper()

        if mA_mod == mB_mod:
            return mA.model_number, True

        if config.min_prefix_match_len > 50:
            return None, False

        prefix_len = common_prefix_length(mA_mod, mB_mod)
        if prefix_len >= config.min_prefix_match_len:
            # Select the more complete / longer model string
            resolved = mA.model_number if len(mA_mod) >= len(mB_mod) else mB.model_number
            return resolved, True

        return None, False

    @classmethod
    def _resolve_serial_number(
        cls, mA, mB, has_factory_serial: bool, config: ThresholdConfig
    ) -> Tuple[Optional[str], bool]:
        if not has_factory_serial and config.allow_zero_serial_exception:
            # Zero-serial asset class tagged by design
            return "[N/A - BY DESIGN]", True

        mA_ser = mA.serial_number.strip().upper()
        mB_ser = mB.serial_number.strip().upper()

        if mA_ser == mB_ser:
            return mA.serial_number, True

        sim = string_similarity(mA_ser, mB_ser)
        if sim >= config.min_serial_levenshtein:
            # High fuzzy agreement, choose higher confidence model
            resolved = mA.serial_number if mA.confidence >= mB.confidence else mB.serial_number
            return resolved, True

        return None, False

    @staticmethod
    def _check_correctness(
        mfg: Optional[str],
        model: Optional[str],
        serial: Optional[str],
        record: TurbinePhotoRecord,
        config: ThresholdConfig,
    ) -> Tuple[bool, List[str]]:
        errors = []
        gt_mfg = record.ground_truth_manufacturer.strip().upper()
        gt_model = record.ground_truth_model_number.strip().upper()
        gt_serial = record.ground_truth_serial_number.strip().upper()

        if mfg is None or mfg.strip().upper() != gt_mfg:
            errors.append("manufacturer")

        if model is None or model.strip().upper() != gt_model:
            errors.append("model_number")

        if not record.has_factory_serial and config.allow_zero_serial_exception:
            if serial != "[N/A - BY DESIGN]":
                errors.append("serial_number")
        else:
            if serial is None or string_similarity(serial, gt_serial) < 0.95:
                errors.append("serial_number")

        return len(errors) == 0, errors

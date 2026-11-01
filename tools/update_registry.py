import json
from pathlib import Path

registry_path = Path("config/runtime/model_registry.json")
with open(registry_path, "r") as f:
    data = json.load(f)

with open("reports/acoustic_pin_capture_receipt.json", "r") as f:
    pins = json.load(f)

# Update ACOUSTIC_MLX_WHISPER
mlx_pin = pins["mlx-community/whisper-large-v3-mlx"]  # noqa: SSOT_EXEMPT
data["model_tiers"]["ACOUSTIC_MLX_WHISPER"]["default_model"] = "mlx-community/whisper-large-v3-mlx"  # noqa: SSOT_EXEMPT
data["model_tiers"]["ACOUSTIC_MLX_WHISPER"]["model_revision"] = mlx_pin["revision"]
data["model_tiers"]["ACOUSTIC_MLX_WHISPER"]["manifest_sha256"] = mlx_pin["snapshot_sha256"]
data["model_tiers"]["ACOUSTIC_MLX_WHISPER"]["allowed_fallbacks"] = []

# Update ACOUSTIC_DIARIZATION
dia_pin = pins["pyannote/speaker-diarization-3.1"]  # noqa: SSOT_EXEMPT
seg_pin = pins["pyannote/segmentation-3.0"]

data["model_tiers"]["ACOUSTIC_DIARIZATION"]["default_model"] = "pyannote/speaker-diarization-3.1"  # noqa: SSOT_EXEMPT
data["model_tiers"]["ACOUSTIC_DIARIZATION"]["model_revision"] = dia_pin["revision"]
data["model_tiers"]["ACOUSTIC_DIARIZATION"]["manifest_sha256"] = dia_pin["snapshot_sha256"]
data["model_tiers"]["ACOUSTIC_DIARIZATION"]["allowed_fallbacks"] = []
data["model_tiers"]["ACOUSTIC_DIARIZATION"]["sub_models"] = [
    {
        "model_id": "pyannote/segmentation-3.0",
        "revision": seg_pin["revision"],
        "snapshot_sha256": seg_pin["snapshot_sha256"]
    }
]

# Remove others
for key in ["ACOUSTIC_FASTER_WHISPER", "ACOUSTIC_WHISPERX"]:
    data["model_tiers"].pop(key, None)

# Also remove them from approved_models if present
if "large-v3" in data["approved_models"]["local"]:
    data["approved_models"]["local"].remove("large-v3")

with open(registry_path, "w") as f:
    json.dump(data, f, indent=2)
print("Updated config/runtime/model_registry.json")

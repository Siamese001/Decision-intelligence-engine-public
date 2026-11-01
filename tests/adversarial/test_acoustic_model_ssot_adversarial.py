import ast
import unittest
from pathlib import Path


class AcousticModelSSOTAdversarialTest(unittest.TestCase):
    def test_no_hardcoded_acoustic_models_outside_registry(self):
        repo_root = Path(__file__).parent.parent.parent
        check_dirs = [repo_root / "dual_pipeline", repo_root / "pipeline_a_intake", repo_root / "scripts"]

        forbidden_literals = {
            "whisper-tiny", "large-v3-turbo", "whisper-large-v3", "faster-whisper",
            "pyannote/speaker-diarization", "pyannote/segmentation-3.0", "mlx-community/whisper-large-v3-mlx"
        }
        forbidden_envs = {
            "ACOUSTIC_MLX_WHISPER_MODEL", "ACOUSTIC_FASTER_WHISPER_MODEL",
            "ACOUSTIC_WHISPERX_MODEL", "ACOUSTIC_DIARIZATION_MODEL"
        }

        allowed_files = {
            "dual_pipeline/model_registry.py",
            "dual_pipeline/config/local_models.yaml",
            "tests/adversarial/test_acoustic_model_ssot_adversarial.py",
            "tools/capture_acoustic_pins.py"
        }

        violations = []

        for d in check_dirs:
            if not d.exists(): continue
            for py_file in d.rglob("*.py"):
                rel_path = py_file.relative_to(repo_root).as_posix()
                if rel_path in allowed_files:
                    continue

                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for forbidden in forbidden_literals:
                            if forbidden in node.value:
                                violations.append(f"{rel_path}:{node.lineno} literal '{forbidden}'")

                    if isinstance(node, ast.Call):
                        is_env_lookup = False
                        if isinstance(node.func, ast.Attribute) and getattr(node.func.value, "id", "") == "environ" and node.func.attr == "get" or isinstance(node.func, ast.Attribute) and getattr(node.func.value, "id", "") == "os" and node.func.attr == "getenv":
                            is_env_lookup = True

                        if is_env_lookup and node.args and isinstance(node.args[0], ast.Constant):
                            env_key = node.args[0].value
                            if env_key in forbidden_envs:
                                violations.append(f"{rel_path}:{node.lineno} env read '{env_key}'")

        if violations:
            self.fail(f"Found {len(violations)} SSOT violations:\n" + "\n".join(violations))

if __name__ == "__main__":
    unittest.main()

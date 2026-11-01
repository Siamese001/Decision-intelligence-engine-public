"""
Constants for raw transcript pipeline.
"""

# Supported audio extensions (aligned with OpenAI Transcription API)
AUDIO_EXTENSIONS = frozenset([
    ".mp3", ".wav", ".m4a", ".mp4a", ".webm", ".mp4",
    ".mpga", ".mpeg", ".ogg", ".flac",
])

# Supported text extensions (including raw Teams docx, WebVTT, SRT, and CoPilot JSON)
TEXT_EXTENSIONS = frozenset([
    ".txt", ".md", ".text", ".markdown", ".docx",
    ".vtt", ".srt", ".json", ".csv", ".tsv",
])

# Total pipeline progress steps (displayed in tqdm)
_PIPELINE_STEPS = 9

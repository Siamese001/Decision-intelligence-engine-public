import re
from typing import Any, Dict, List


def count_tokens(text: str) -> int:
    # Simple whitespace tokenization as approximation
    return len(text.split())

def parse_transcript_turns(transcript_text: str) -> List[Dict[str, Any]]:
    # Reuse the pattern or write a robust one
    turn_pattern = re.compile(
        r"(?:^\s*\*\*(?P<speaker_b>[^*]+)\*\*:?|^\s*(?P<speaker_p>[A-Z][a-zA-Z\s.,-]+):)\s*(?:\[(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\])?\s*(?P<text>(?:.(?!\n\s*\*\*|\n\s*[A-Z][a-zA-Z\s.,-]+:))*.)",
        re.MULTILINE | re.DOTALL,
    )
    turns = []
    idx = 1
    for match in turn_pattern.finditer(transcript_text):
        spk = match.group("speaker_b") or match.group("speaker_p") or "Unknown"
        spk = spk.rstrip(":").strip()
        time = match.group("time") or ""
        text = match.group("text").strip()
        turns.append({
            "turn_id": idx,
            "speaker": spk,
            "time": time,
            "text": text,
            "token_count": count_tokens(text)
        })
        idx += 1

    # Fallback if no turns matched, treat as single turn
    if not turns:
        turns.append({
            "turn_id": 1,
            "speaker": "Unknown",
            "time": "",
            "text": transcript_text.strip(),
            "token_count": count_tokens(transcript_text)
        })
    return turns

class ChunkingEngine:
    """Speaker-bounded sliding window segmenter for transcript grounding."""

    def __init__(self, fine_min=64, fine_max=128, coarse_min=256, coarse_max=512):
        self.fine_min = fine_min
        self.fine_max = fine_max
        self.coarse_min = coarse_min
        self.coarse_max = coarse_max

    def _chunk_turns(self, turns: List[Dict[str, Any]], min_tokens: int, max_tokens: int) -> List[Dict[str, Any]]:
        chunks = []
        current_chunk_turns = []
        current_tokens = 0
        chunk_idx = 1

        for turn in turns:
            turn_tokens = turn["token_count"]
            if current_tokens + turn_tokens > max_tokens and current_chunk_turns:
                # If adding this turn exceeds max and we have something, emit the chunk
                text = "\n".join(f"{t['speaker']}: {t['text']}" for t in current_chunk_turns)
                chunks.append({
                    "chunk_id": f"chunk-{chunk_idx}",
                    "text": text,
                    "start_turn": current_chunk_turns[0]["turn_id"],
                    "end_turn": current_chunk_turns[-1]["turn_id"],
                    "token_count": count_tokens(text)
                })
                chunk_idx += 1
                current_chunk_turns = []
                current_tokens = 0

            current_chunk_turns.append(turn)
            current_tokens += turn_tokens

            # If a single turn is larger than max_tokens, it will be emitted next iteration or at the end

        if current_chunk_turns:
            text = "\n".join(f"{t['speaker']}: {t['text']}" for t in current_chunk_turns)
            chunks.append({
                "chunk_id": f"chunk-{chunk_idx}",
                "text": text,
                "start_turn": current_chunk_turns[0]["turn_id"],
                "end_turn": current_chunk_turns[-1]["turn_id"],
                "token_count": count_tokens(text)
            })

        return chunks

    def chunk(self, transcript_text: str) -> Dict[str, List[Dict[str, Any]]]:
        turns = parse_transcript_turns(transcript_text)
        fine_chunks = self._chunk_turns(turns, self.fine_min, self.fine_max)
        coarse_chunks = self._chunk_turns(turns, self.coarse_min, self.coarse_max)

        return {
            "fine_chunks": fine_chunks,
            "coarse_chunks": coarse_chunks,
            "turns": turns
        }

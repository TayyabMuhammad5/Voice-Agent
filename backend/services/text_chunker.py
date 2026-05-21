"""
Text Chunker — Fine-grained phrase splitting for streaming TTS.

Splits incoming LLM tokens into short, speakable phrases optimized
for edge-tts (which generates audio for the entire chunk before
returning bytes). Shorter chunks = lower latency.

Split points (in priority order):
  1. Sentence endings: . ! ?
  2. Clause breaks:    , ; : —
  3. Max length:       if a chunk exceeds MAX_CHUNK_LEN, force-split
                       at the last space.
"""


class TextChunker:
    """Accumulates streaming text tokens and yields short speakable phrases."""

    # Primary sentence endings (highest priority split)
    SENTENCE_ENDINGS = (".", "!", "?")

    # Secondary clause breaks (split here if chunk is getting long)
    CLAUSE_BREAKS = (",", ";", ":", "—", " -")

    # Force-split if a chunk exceeds this many characters
    MAX_CHUNK_LEN = 80

    # Minimum chunk length before we consider splitting at clause breaks
    MIN_CLAUSE_LEN = 25

    def __init__(self):
        self.buffer = ""

    def add(self, token: str) -> list[str]:
        """Add a token and return any complete phrase chunks.

        Args:
            token: A text token from the LLM stream.

        Returns:
            A list of speakable phrases (may be empty if no
            split point was found yet).
        """
        self.buffer += token
        chunks: list[str] = []

        while True:
            chunk = self._try_split()
            if chunk is None:
                break
            chunks.append(chunk)

        return chunks

    def _try_split(self) -> str | None:
        """Try to extract one chunk from the buffer."""
        buf = self.buffer

        if not buf.strip():
            return None

        # 1. Look for sentence endings (. ! ?)
        for i, char in enumerate(buf):
            if char in self.SENTENCE_ENDINGS:
                # Make sure it's a real sentence end (not "Dr." or "3.5")
                # Simple heuristic: must be followed by space, newline, or end of buffer
                next_pos = i + 1
                if next_pos >= len(buf) or buf[next_pos] in (" ", "\n", "\t", "\r"):
                    sentence = buf[:next_pos].strip()
                    self.buffer = buf[next_pos:].lstrip()
                    if sentence:
                        return sentence

        # 2. If buffer is getting long, split at clause breaks
        if len(buf) >= self.MIN_CLAUSE_LEN:
            # Search from the end backwards for the latest clause break
            # within a reasonable range
            best_pos = -1
            for i in range(len(buf) - 1, self.MIN_CLAUSE_LEN - 2, -1):
                for brk in self.CLAUSE_BREAKS:
                    if buf[i:i+len(brk)] == brk:
                        best_pos = i + len(brk)
                        break
                if best_pos != -1:
                    break

            if best_pos != -1 and len(buf) >= self.MIN_CLAUSE_LEN:
                phrase = buf[:best_pos].strip()
                self.buffer = buf[best_pos:].lstrip()
                if phrase:
                    return phrase

        # 3. Force-split at max length on last space
        if len(buf) >= self.MAX_CHUNK_LEN:
            last_space = buf.rfind(" ", 0, self.MAX_CHUNK_LEN)
            if last_space > 0:
                phrase = buf[:last_space].strip()
                self.buffer = buf[last_space:].lstrip()
                if phrase:
                    return phrase
            else:
                # No space found — just split at max
                phrase = buf[:self.MAX_CHUNK_LEN].strip()
                self.buffer = buf[self.MAX_CHUNK_LEN:]
                if phrase:
                    return phrase

        return None

    def flush(self) -> str | None:
        """Return any remaining buffered text.

        Call this when the LLM stream ends to capture the final
        partial phrase (if any).
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        return remaining if remaining else None

    def reset(self):
        """Clear the buffer."""
        self.buffer = ""

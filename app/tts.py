import threading
import queue
import logging
import subprocess
import os
import re
import tempfile
try:
    import soundfile as sf
except ImportError:
    sf = None
import numpy as np

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except (ImportError, OSError):
    sd = None
    HAS_SOUNDDEVICE = False

from .tts_engines import (
    DEFAULT_ENGINE as _DEFAULT_ENGINE,
    VALID_ENGINES as _VALID_ENGINES,
    KokoroEngine,
    SupertonicEngine,
)

logger = logging.getLogger(__name__)

_TTS_MAX_CHARS = 160
_TTS_MIN_CHARS = 12
_TTS_LIST_GROUP_LINES = 3


def _build_engine():
    """Select the speech backend from the TTS_ENGINE environment variable.

    Falls back to Kokoro when Supertonic is selected but unusable, rather than
    failing to start: translation works without speech, and a partially set up
    checkout should stay usable.
    """
    requested = (os.getenv("TTS_ENGINE") or _DEFAULT_ENGINE).strip().lower()
    if requested not in _VALID_ENGINES:
        logger.warning(
            f"Unknown TTS_ENGINE={requested!r}; valid values: "
            f"{', '.join(_VALID_ENGINES)}. Using {_DEFAULT_ENGINE}."
        )
        requested = _DEFAULT_ENGINE

    if requested == "supertonic":
        try:
            engine = SupertonicEngine()
            if engine.available:
                return engine
            logger.warning("Supertonic unavailable; falling back to Kokoro.")
        except Exception as e:
            logger.error(f"Supertonic init failed ({e}); falling back to Kokoro.")

    return KokoroEngine()


def _is_table_like(lines):
    if not lines:
        return False
    pipe_lines = sum(1 for line in lines if line.count("|") >= 2)
    return pipe_lines >= max(2, len(lines) // 2)

def _is_list_like(lines):
    if len(lines) < 4:
        return False
    short_lines = sum(1 for line in lines if 0 < len(line.strip()) <= 8)
    return short_lines / max(1, len(lines)) >= 0.6

def _split_long_chunk(text, max_chars):
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r'([、，,;；:])', text)
    chunks = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) > max_chars and buf:
            chunks.append(buf)
            buf = part
        else:
            buf += part
    if buf:
        chunks.append(buf)
    if all(len(chunk) <= max_chars for chunk in chunks):
        return chunks
    # Fallback: hard split
    hard = []
    start = 0
    while start < len(text):
        hard.append(text[start:start + max_chars])
        start += max_chars
    return hard

def _merge_short_chunks(chunks, min_chars, max_chars):
    merged = []
    for chunk in chunks:
        if not chunk:
            continue
        if merged and len(chunk) < min_chars and len(merged[-1]) + len(chunk) <= max_chars:
            merged[-1] += chunk
        else:
            merged.append(chunk)
    return merged

def _split_text_for_tts(text):
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in normalized.split("\n") if line.strip()]
    if _is_table_like(lines) or _is_list_like(lines):
        chunks = []
        buf_lines = []
        for line in lines:
            candidate = ("\n".join(buf_lines + [line])).strip()
            if len(candidate) > _TTS_MAX_CHARS and buf_lines:
                chunks.append("\n".join(buf_lines).strip())
                buf_lines = [line]
            else:
                buf_lines.append(line)
            if len(buf_lines) >= _TTS_LIST_GROUP_LINES:
                candidate = "\n".join(buf_lines).strip()
                if len(candidate) >= _TTS_MIN_CHARS:
                    chunks.append(candidate)
                    buf_lines = []
        if buf_lines:
            chunks.append("\n".join(buf_lines).strip())
        return _merge_short_chunks(chunks, _TTS_MIN_CHARS, _TTS_MAX_CHARS)

    # Sentence-based split
    sentences = []
    for para in normalized.split("\n"):
        para = para.strip()
        if not para:
            continue
        parts = re.split(r'([。！？!?]|[.?!])', para)
        buf = ""
        for part in parts:
            if not part:
                continue
            buf += part
            if re.match(r'[。！？!?]|[.?!]$', part):
                sentences.append(buf.strip())
                buf = ""
        if buf:
            sentences.append(buf.strip())

    chunks = []
    for sentence in sentences:
        if len(sentence) <= _TTS_MAX_CHARS:
            chunks.append(sentence)
        else:
            chunks.extend(_split_long_chunk(sentence, _TTS_MAX_CHARS))
    return _merge_short_chunks(chunks, _TTS_MIN_CHARS, _TTS_MAX_CHARS)

class TTSEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TTSEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.engine = _build_engine()
        self.sample_rate = self.engine.sample_rate
        logger.info(f"[TTS] engine={self.engine.describe()} sample_rate={self.sample_rate}")

        self._gen_queue = queue.Queue()
        self._play_queue = queue.Queue()
        self._current_gen_id = 0
        self._current_text = None
        self._stop_event = threading.Event()
        self._is_generating = False
        self._is_playing = False
        
        # Buffer for the "Next" text to speak immediately after current finishes
        self._next_text = None
        self._next_text_lock = threading.Lock()

        self._gen_thread = threading.Thread(target=self._generator_loop, daemon=True)
        self._play_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._gen_thread.start()
        self._play_thread.start()
        
        self._initialized = True

    def is_busy(self) -> bool:
        """Check if TTS is currently generating or playing audio."""
        return (
            self._is_generating
            or self._is_playing
            or not self._gen_queue.empty()
            or not self._play_queue.empty()
        )

    def set_next_text(self, text: str):
        with self._next_text_lock:
            self._next_text = text
            logger.info(f"Buffered next text (len={len(text)}): {text[:20]}...")

    def _generator_loop(self):
        logger.info("TTS Generator thread started.")
        while True:
            try:
                item = self._gen_queue.get()
                if item is None:
                    break
                
                self._is_generating = True
                full_text, gen_id, parent_text, is_last = item
                if parent_text:
                    self._current_text = parent_text
                else:
                    self._current_text = full_text
                
                try:
                    # Check cancellation before starting
                    if gen_id != self._current_gen_id:
                        self._gen_queue.task_done()
                        self._is_generating = False
                        self._current_text = None
                        continue

                    if not self.engine.available:
                         logger.error("TTS engine not loaded. Cannot speak.")
                         continue

                    full_audio = self.engine.synthesize(full_text)

                    # Play generated audio if not interrupted meanwhile
                    if full_audio is not None and gen_id == self._current_gen_id:
                        self._play_queue.put((full_audio, gen_id, is_last))

                finally:
                    self._gen_queue.task_done()
                    self._is_generating = False

            except Exception as e:
                logger.error(f"TTS worker error: {e}")
                self._is_generating = False
                self._current_text = None

    def _playback_loop(self):
        logger.info("TTS Playback thread started.")
        while True:
            try:
                item = self._play_queue.get()
                if item is None:
                    break
                full_audio, gen_id, is_last = item
                if gen_id != self._current_gen_id:
                    self._play_queue.task_done()
                    continue
                self._is_playing = True
                try:
                    if HAS_SOUNDDEVICE:
                        sd.play(full_audio, self.sample_rate)
                        sd.wait()
                    else:
                        try:
                            aplay_proc = subprocess.Popen(
                                ['aplay', '-f', 'S16_LE', '-r', str(self.sample_rate),
                                 '-c', '1', '-q'],
                                stdin=subprocess.PIPE
                            )
                            audio_clipped = np.clip(full_audio, -1.0, 1.0)
                            audio_int16 = (audio_clipped * 32767).astype(np.int16)
                            aplay_proc.stdin.write(audio_int16.tobytes())
                            aplay_proc.stdin.close()
                            aplay_proc.wait()
                        except Exception as e:
                            logger.error(f"Aplay error: {e}")
                finally:
                    self._play_queue.task_done()
                    self._is_playing = False
                    if is_last and gen_id == self._current_gen_id:
                        self._current_text = None
                        next_text_to_speak = None
                        with self._next_text_lock:
                            if self._next_text:
                                logger.info("Found buffered text, grabbing it.")
                                next_text_to_speak = self._next_text
                                self._next_text = None
                        if next_text_to_speak:
                            logger.info("Queuing buffered text immediately.")
                            self.speak(next_text_to_speak, interrupt=False)
            except Exception as e:
                logger.error(f"TTS playback error: {e}")
                self._is_playing = False

    def is_content_active(self, text: str) -> bool:
        """Check if text is currently being spoken or is buffered as next."""
        if not text:
            return False
        
        # Check buffer
        with self._next_text_lock:
            if self._next_text == text:
                return True
        
        # Check current (approximate, no lock needed for simple string check)
        if self._current_text == text:
            return True
            
        return False

    def speak(self, text: str, interrupt: bool = True):
        if not text:
            return
        
        # Clear any pending buffered text if we are speaking explicitly
        with self._next_text_lock:
            self._next_text = None

        if interrupt:
            self._current_gen_id += 1
            # Soft cancel: let current playback finish, discard queued items
            self._drain_queue(self._gen_queue)
            self._drain_queue(self._play_queue)
        
        chunks = _split_text_for_tts(text)
        if not chunks:
            return
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            self._gen_queue.put((chunk, self._current_gen_id, text, is_last))

    @staticmethod
    def _drain_queue(q: queue.Queue) -> None:
        try:
            while True:
                q.get_nowait()
                q.task_done()
        except queue.Empty:
            return

# Global instance
tts_engine = TTSEngine()

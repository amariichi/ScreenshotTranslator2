"""Speech synthesis backends.

This module owns "how text becomes audio". Queueing, cancellation and playback
stay in app/tts.py, which drives whichever engine is selected here.

Must not import from app.tts (circular import).
"""

import asyncio
import logging
import os
import re
from pathlib import Path

import numpy as np

try:
    from misaki import ja
    HAS_MISAKI = True
except ImportError:
    HAS_MISAKI = False

logger = logging.getLogger(__name__)

# Package-compatible Supertonic 3 asset revision. Pinned together with
# supertonic==1.3.1 in pyproject.toml; see that comment for why.
SUPERTONIC_MODEL_REVISION = "724fb5abbf5502583fb520898d45929e62f02c0b"
# Default cache location. Override with SUPERTONIC_CACHE_DIR to share the
# ~386MB asset set with another tool that pins the same revision.
SUPERTONIC_DEFAULT_CACHE = Path.home() / ".cache" / "supertonic3"
SUPERTONIC_VOICES = frozenset(
    f"{prefix}{index}" for prefix in ("M", "F") for index in range(1, 6)
)

# Optional Kokoro engine assets (TTS_ENGINE=kokoro). Single-sourced here so the
# pinned asset version lives in exactly one place; app/scripts/setup_tts.py
# imports these rather than repeating the filenames.
KOKORO_MODEL_FILE = "kokoro-v1.0.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
KOKORO_RELEASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)

# Speech backend used when TTS_ENGINE is unset. Imported by
# app/scripts/setup_tts.py so the default is declared once.
DEFAULT_ENGINE = "supertonic"
VALID_ENGINES = ("supertonic", "kokoro")

# Kana plus the two Han (kanji) blocks.
_JAPANESE_SCRIPT_RE = re.compile(r'[぀-ヿ㐀-䶿一-鿿]')


class SpeechEngine:
    """Interface implemented by every backend.

    synthesize() returns a 1-D float32 array of samples, or None when audio
    could not be produced. Returning None instead of raising keeps the
    generator loop in app/tts.py simple.
    """

    name = "base"
    sample_rate = 24000
    available = False

    def synthesize(self, text: str):
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


def _split_by_language(text):
    """
    Split text into chunks of English (contiguous ASCII characters) and others.
    Returns list of (text_chunk, is_english_bool).

    Kokoro-specific: it synthesizes one language per call, so mixed Japanese and
    English has to be cut apart and glued back together. Supertonic needs none of
    this and reads the whole passage in one call.
    """
    if not text:
        return []
    parts = re.split(r'([\x20-\x7E]+)', text)
    chunks = []
    for part in parts:
        if not part:
            continue
        is_en = bool(re.match(r'^[\x20-\x7E]+$', part))
        chunks.append((part, is_en))
    return chunks


class KokoroEngine(SpeechEngine):
    name = "kokoro"
    sample_rate = 24000

    def __init__(self):
        logger.info("Initializing Kokoro TTS (ONNX)...")
        self.kokoro = None
        self.g2p = None
        self.available = False

        model_path = KOKORO_MODEL_FILE
        voices_path = KOKORO_VOICES_FILE

        if os.path.exists(model_path) and os.path.exists(voices_path):
            try:
                from kokoro_onnx import Kokoro
                self.kokoro = Kokoro(model_path, voices_path)
                self.available = True
                logger.info("Kokoro ONNX loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load Kokoro ONNX: {e}")
        else:
            logger.warning(f"Kokoro model files not found: {model_path}, {voices_path}")
            logger.warning("Please download them to the project root.")

        if HAS_MISAKI:
            try:
                self.g2p = ja.JAG2P()
                logger.info("Misaki G2P loaded.")
            except Exception as e:
                logger.error(f"Failed to load Misaki G2P: {e}")
        else:
            logger.warning("Misaki not found. Install 'misaki' for better Japanese support.")

    def describe(self) -> str:
        return f"kokoro voice=af_heart g2p={'misaki' if self.g2p else 'none'}"

    def synthesize(self, text: str):
        if not self.kokoro:
            return None

        audio_segments = []
        for fragment, is_en in _split_by_language(text):
            try:
                if is_en:
                    # Kokoro's internal tokenizer uses espeak-ng (via espeakng_loader)
                    lang = 'en-us'
                    input_text = fragment
                    is_phonemes = False
                    speed = 1.0
                else:
                    lang = 'j'
                    input_text = fragment
                    is_phonemes = False
                    speed = 1.25  # Japanese 25% faster
                    if self.g2p:
                        try:
                            phonemes, _ = self.g2p(fragment)
                            input_text = phonemes
                            is_phonemes = True
                        except Exception as e:
                            logger.error(f"G2P error: {e}, using raw text")

                stream = self.kokoro.create_stream(
                    input_text,
                    voice='af_heart',
                    speed=speed,
                    lang=lang,
                    is_phonemes=is_phonemes
                )

                fragment_samples = []

                async def consume_stream():
                    async for samples, _ in stream:
                        fragment_samples.append(samples)

                asyncio.run(consume_stream())

                if fragment_samples:
                    audio_segments.extend(fragment_samples)

            except Exception as e:
                logger.error(f"Chunk generation error: {e}")

        if not audio_segments:
            return None
        return np.concatenate(audio_segments)


def _env_number(name: str, default, cast, low, high):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if value < low or value > high:
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


# Supertonic wraps the whole passage in a <lang>...</lang> token; there is no
# phoneme step and no per-word language handling, so this single tag conditions
# every character in the utterance. Tagging Japanese output as 'ja' therefore
# also tells the model that the Latin-script terms left in a translation --
# product names, acronyms -- are Japanese, and they come out mispronounced.
#
# Listening to the same sentences under each tag, 'en' read the embedded terms
# correctly and did not damage the Japanese, including a sample with no Latin
# characters at all. So 'en' is the default rather than the script-derived tag.
DEFAULT_LANGUAGE_TAG = "en"


def detect_language(text: str) -> str:
    """Pick the Supertonic language tag for a passage.

    SUPERTONIC_LANGUAGE overrides this: any Supertonic code forces that tag,
    and 'auto' restores the script-derived choice this used to make by
    default. That path has two limits, which is part of why it is no longer
    the default:
    - Chinese is reported as 'ja' because it shares the Han ranges. Supertonic 3
      does not support Chinese at all, so Chinese source text is out of scope
      either way.
    - Latin-script languages (French, Spanish, ...) are indistinguishable from
      English by script alone.
    """
    override = (os.getenv("SUPERTONIC_LANGUAGE") or "").strip().lower()
    if override and override != "auto":
        return override
    if not override:
        return DEFAULT_LANGUAGE_TAG
    if _JAPANESE_SCRIPT_RE.search(text or ""):
        return "ja"
    return "en"


class SupertonicEngine(SpeechEngine):
    name = "supertonic-3"

    def __init__(self):
        logger.info("Initializing Supertonic 3 TTS (ONNX, CPU)...")
        self.available = False
        self._tts = None
        self._style = None

        self.voice = (os.getenv("SUPERTONIC_VOICE") or "F2").strip()
        if self.voice not in SUPERTONIC_VOICES:
            raise RuntimeError("SUPERTONIC_VOICE must be one of M1-M5 or F1-F5")
        self.speed = _env_number("SUPERTONIC_SPEED", 1.05, float, 0.7, 2.0)
        self.total_steps = _env_number("SUPERTONIC_STEPS", 8, int, 5, 12)
        # Benchmarked on a 20-core host: 4 threads beat 10 (RTF 0.12-0.15 vs
        # 0.14-0.25). ONNX Runtime oversubscribes when left to autodetect.
        self.intra_op_threads = _env_number(
            "SUPERTONIC_INTRA_OP_THREADS", 4, int, 1, 64
        )

        cache_dir = Path(
            os.getenv("SUPERTONIC_CACHE_DIR") or SUPERTONIC_DEFAULT_CACHE
        ).expanduser()
        revision = (
            os.getenv("SUPERTONIC_MODEL_REVISION") or SUPERTONIC_MODEL_REVISION
        ).strip()
        # The supertonic package reads both of these from the environment at
        # import and load time, so they must be set before TTS() is constructed.
        os.environ["SUPERTONIC_CACHE_DIR"] = str(cache_dir)
        os.environ["SUPERTONIC_MODEL_REVISION"] = revision
        self.cache_dir = cache_dir

        try:
            from supertonic import TTS
            # auto_download=False so a normal app start never reaches the
            # network; app/scripts/setup_tts.py fetches the assets instead.
            self._tts = TTS(
                model="supertonic-3",
                auto_download=False,
                intra_op_num_threads=self.intra_op_threads,
                inter_op_num_threads=1,
            )
            self._style = self._tts.get_voice_style(voice_name=self.voice)
            self.sample_rate = int(getattr(self._tts, "sample_rate", 44100))
            self.available = True
            logger.info(f"Supertonic 3 loaded from {cache_dir}")
        except Exception as e:
            logger.error(f"Failed to load Supertonic 3: {e}")
            logger.error("Run 'uv run app/scripts/setup_tts.py' to fetch the model assets.")

    def describe(self) -> str:
        return f"supertonic-3 voice={self.voice} steps={self.total_steps} speed={self.speed}"

    def synthesize(self, text: str):
        if not self._tts or not text:
            return None
        try:
            # One call for the whole passage: Supertonic reads mixed Japanese
            # and English continuously, so no language splitting is needed.
            # Keep this synchronous. Routing this ONNX call through
            # asyncio.to_thread has been observed to stall after synthesis;
            # app/tts.py already calls us from an ordinary worker thread.
            wav, _duration = self._tts.synthesize(
                text=text,
                lang=detect_language(text),
                voice_style=self._style,
                total_steps=self.total_steps,
                speed=self.speed,
                verbose=False,
            )
        except Exception as e:
            logger.error(f"Supertonic synthesis error: {e}")
            return None

        audio = np.asarray(wav, dtype=np.float32).squeeze()
        if audio.ndim == 0:
            return None
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0).astype(np.float32, copy=False)
        return audio.astype(np.float32, copy=False)

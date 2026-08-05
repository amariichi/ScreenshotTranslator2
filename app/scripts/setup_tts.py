#!/usr/bin/env python3
import os
import urllib.request
import subprocess
import sys

def download_file(url, filename):
    if os.path.exists(filename):
        print(f"File already exists: {filename}")
        return
    print(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, filename)
        print("Download complete.")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")
        sys.exit(1)

def setup_kokoro():
    print("Checking Kokoro ONNX models...")
    from app.tts_engines import (
        KOKORO_MODEL_FILE,
        KOKORO_RELEASE_URL,
        KOKORO_VOICES_FILE,
    )
    download_file(f"{KOKORO_RELEASE_URL}/{KOKORO_MODEL_FILE}", KOKORO_MODEL_FILE)
    download_file(f"{KOKORO_RELEASE_URL}/{KOKORO_VOICES_FILE}", KOKORO_VOICES_FILE)

def setup_supertonic():
    """Fetch the Supertonic 3 ONNX assets (~386MB) into the shared cache.

    Runtime uses TTS(auto_download=False), so this is the only step allowed to
    reach the network. Idempotent: returns immediately if the assets are there.
    """
    print("Checking Supertonic 3 models...")
    from app.tts_engines import (
        SUPERTONIC_DEFAULT_CACHE,
        SUPERTONIC_MODEL_REVISION,
        SUPERTONIC_VOICES,
    )

    cache_dir = os.path.expanduser(
        os.getenv("SUPERTONIC_CACHE_DIR") or str(SUPERTONIC_DEFAULT_CACHE)
    )
    revision = os.getenv("SUPERTONIC_MODEL_REVISION") or SUPERTONIC_MODEL_REVISION
    os.environ["SUPERTONIC_CACHE_DIR"] = cache_dir
    os.environ["SUPERTONIC_MODEL_REVISION"] = revision

    voice = (os.getenv("SUPERTONIC_VOICE") or "F2").strip()
    if voice not in SUPERTONIC_VOICES:
        voice = "F2"

    if os.path.exists(os.path.join(cache_dir, "onnx", "vocoder.onnx")):
        print(f"Supertonic assets already present at {cache_dir}")
        return

    print(f"Downloading Supertonic 3 assets (~386MB) to {cache_dir}...")
    try:
        from supertonic import TTS
        tts = TTS(model="supertonic-3", auto_download=True)
        tts.get_voice_style(voice_name=voice)
        print("Supertonic setup complete.")
    except Exception as e:
        print(f"Failed to set up Supertonic: {e}")
        print("Speech will fall back to Kokoro. Set TTS_ENGINE=kokoro to silence this.")
        sys.exit(1)


def setup_unidic():
    print("Checking UniDic...")
    # Check if unidic is usable
    try:
        import unidic
        dic_dir = unidic.DICDIR
        mecabrc = os.path.join(dic_dir, "mecabrc")
        if os.path.exists(mecabrc):
            print(f"UniDic seems installed at {dic_dir}")
            return
    except ImportError:
        pass

    print("Installing UniDic dictionary...")
    try:
        subprocess.check_call([sys.executable, "-m", "unidic", "download"])
        print("UniDic setup complete.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install UniDic: {e}")
        # Dont exit, might work if already installed but check failed

if __name__ == "__main__":
    from app.tts_engines import DEFAULT_ENGINE
    engine = (os.getenv("TTS_ENGINE") or DEFAULT_ENGINE).strip().lower()
    print(f"Provisioning TTS engine: {engine}")
    if engine == "supertonic":
        setup_supertonic()
    else:
        # Kokoro needs its own model files plus a Japanese G2P dictionary.
        setup_kokoro()
        setup_unidic()

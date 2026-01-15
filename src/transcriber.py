"""Transcription module using whisper.cpp"""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Transcriber:
    """Transcribes audio files using whisper.cpp"""

    def __init__(self, whisper_path, model_path):
        self.whisper_path = Path(whisper_path)
        self.model_path = Path(model_path)

        # Validate paths
        if not self.whisper_path.exists():
            raise FileNotFoundError(f"whisper.cpp not found at {self.whisper_path}")
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found at {self.model_path}")

        logger.info(f"Transcriber initialized with model: {self.model_path.name}")

    def transcribe(self, audio_path, language=None):
        """
        Transcribe audio file using whisper.cpp

        Args:
            audio_path: Path to WAV file
            language: Language code (e.g., 'bs' for Bosnian), None for auto-detect

        Returns:
            Transcribed text string, or None if failed
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None

        # Build whisper.cpp command
        cmd = [
            str(self.whisper_path),
            "-m", str(self.model_path),
            "-f", str(audio_path),
            "-otxt"  # Output as .txt file
        ]

        # Add language parameter if specified
        if language:
            cmd.extend(["-l", language])
            logger.info(f"Transcribing with language: {language}")
        else:
            logger.info("Transcribing with auto-detect")

        try:
            # Run whisper.cpp
            logger.info(f"Running: {' '.join(cmd)}")
            print(f"DEBUG: Transcriber running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60  # 60 second timeout
            )
            
            print(f"DEBUG: Whisper return code: {result.returncode}")
            if result.stdout:
                print(f"DEBUG: Whisper stdout: {result.stdout[:200]}...")
            if result.stderr:
                print(f"DEBUG: Whisper stderr: {result.stderr}")

            if result.returncode != 0:
                logger.error(f"whisper.cpp failed: {result.stderr}")
                print(f"ERROR: Whisper failed: {result.stderr}")
                return None

            # Read the generated .txt file
            txt_path = audio_path.with_suffix('.wav.txt')
            if not txt_path.exists():
                logger.error(f"Output text file not found: {txt_path}")
                return None

            with open(txt_path, 'r', encoding='utf-8') as f:
                transcription = f.read().strip()

            # Clean up the .txt file
            txt_path.unlink()

            if not transcription:
                logger.warning("Transcription is empty")
                return None

            logger.info(f"Transcription successful: {len(transcription)} chars")
            return transcription

        except subprocess.TimeoutExpired:
            logger.error("Transcription timeout")
            return None
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return None

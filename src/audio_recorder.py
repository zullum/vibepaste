"""Audio recording module using sounddevice"""

import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Records audio from default microphone and saves as WAV"""

    def __init__(self, sample_rate=16000, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.recording = []
        self.is_recording = False
        self.stream = None

    def _audio_callback(self, indata, frames, time, status):
        """Callback function for audio stream"""
        if status:
            logger.warning(f"Audio callback status: {status}")
        if self.is_recording:
            self.recording.append(indata.copy())

    def start_recording(self):
        """Start recording audio from default microphone"""
        if self.is_recording:
            logger.warning("Already recording")
            return

        logger.info("Starting audio recording")
        self.recording = []
        self.is_recording = True

        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._audio_callback,
                dtype=np.float32
            )
            self.stream.start()
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.is_recording = False
            raise

    def stop_recording(self, output_path):
        """Stop recording and save to WAV file"""
        if not self.is_recording:
            logger.warning("Not currently recording")
            return False

        logger.info("Stopping audio recording")
        self.is_recording = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if not self.recording:
            logger.warning("No audio data recorded")
            return False

        # Concatenate all recorded chunks
        audio_data = np.concatenate(self.recording, axis=0)

        # Convert float32 to int16 for WAV format
        audio_data = np.int16(audio_data * 32767)

        # Save to WAV file
        try:
            write(output_path, self.sample_rate, audio_data)
            logger.info(f"Audio saved to {output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save audio: {e}")
            return False

    def get_default_device(self):
        """Get default input device info"""
        try:
            device_info = sd.query_devices(kind='input')
            logger.info(f"Default input device: {device_info['name']}")
            return device_info
        except Exception as e:
            logger.error(f"Failed to query devices: {e}")
            return None

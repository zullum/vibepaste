"""Clipboard and auto-paste module"""

import pyperclip
import pyautogui
import logging
import time

logger = logging.getLogger(__name__)


class Paster:
    """Handles clipboard operations and auto-pasting"""

    def __init__(self, restore_clipboard=False):
        """
        Initialize paster

        Args:
            restore_clipboard: Whether to restore original clipboard after paste
        """
        self.restore_clipboard = restore_clipboard
        self.original_clipboard = None

    def paste_text(self, text):
        """
        Copy text to clipboard and simulate Cmd+V paste

        Args:
            text: Text to paste

        Returns:
            True if successful, False otherwise
        """
        if not text:
            logger.warning("Empty text, skipping paste")
            return False

        try:
            # Save original clipboard if restore is enabled
            if self.restore_clipboard:
                try:
                    self.original_clipboard = pyperclip.paste()
                except Exception as e:
                    logger.warning(f"Could not save clipboard: {e}")
                    self.original_clipboard = None

            # Copy text to clipboard
            pyperclip.copy(text)
            logger.info(f"Copied to clipboard: {len(text)} chars")

            # Small delay to ensure clipboard is updated
            time.sleep(0.05)

            # Simulate Cmd+V paste
            pyautogui.hotkey('command', 'v')
            logger.info("Simulated paste (Cmd+V)")

            # Restore original clipboard if enabled
            if self.restore_clipboard and self.original_clipboard is not None:
                time.sleep(0.1)
                pyperclip.copy(self.original_clipboard)
                logger.info("Restored original clipboard")

            return True

        except Exception as e:
            logger.error(f"Paste failed: {e}")
            return False

    def copy_only(self, text):
        """
        Copy text to clipboard without pasting

        Args:
            text: Text to copy

        Returns:
            True if successful, False otherwise
        """
        if not text:
            logger.warning("Empty text, skipping copy")
            return False

        try:
            pyperclip.copy(text)
            logger.info(f"Copied to clipboard: {len(text)} chars")
            return True
        except Exception as e:
            logger.error(f"Copy failed: {e}")
            return False

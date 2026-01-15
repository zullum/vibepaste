"""VibePaste Menu Bar App - Launches with menu bar icon"""

import sys
import os
from pathlib import Path
import threading
import logging

# Get the vibepaste directory
VIBEPASTE_DIR = Path(__file__).parent.parent

# Add vibepaste directory to path for imports
sys.path.insert(0, str(VIBEPASTE_DIR))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check Accessibility Permissions
try:
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    
    def check_accessibility(prompt=False):
        options = {kAXTrustedCheckOptionPrompt: prompt}
        trusted = AXIsProcessTrustedWithOptions(options)
        logger.info(f"Accessibility Permissions: {'GRANTED' if trusted else 'DENIED'}")
        return trusted
except ImportError:
    def check_accessibility(prompt=False):
        logger.warning("Could not import ApplicationServices to check permissions")
        return True

try:
    import rumps
    RUMPS_AVAILABLE = True
except ImportError:
    RUMPS_AVAILABLE = False


class VibePasteMenuBar(rumps.App):
    """Menu bar application for VibePaste"""
    
    def __init__(self):
        super().__init__(
            name="VibePaste",
            title="🎙️",
            quit_button="Quit VibePaste"
        )
        self.vibepaste = None
        self.menu = [
            rumps.MenuItem("Start VibePaste", callback=self.start_vibepaste),
            rumps.MenuItem("Stop VibePaste", callback=self.stop_vibepaste),
            None,  # Separator
            rumps.MenuItem("Hotkeys:", callback=None),
            rumps.MenuItem("  ⌥L + Space → English", callback=None),
            rumps.MenuItem("  ⌥R + Space → Bosnian", callback=None),
            rumps.MenuItem("  Space → Stop & Paste", callback=None),
        ]
        # Auto-start VibePaste
        self.start_vibepaste(None)
    
    def start_vibepaste(self, sender):
        """Start the VibePaste main process (in-process, not subprocess)"""
        if self.vibepaste is not None:
            rumps.notification(
                title="🎙️ VibePaste",
                subtitle="Already Running",
                message="VibePaste is already active!"
            )
            return
            
        try:
            # Import and run VibePaste directly in this process
            # This ensures the keyboard listener has the same permissions as the app
            from src.main import VibePaste
            
            self.vibepaste = VibePaste()
            self.vibepaste.run_background()
            
            self.title = "🎙️"
            rumps.notification(
                title="🎙️ VibePaste",
                subtitle="Started",
                message="Ready! Use ⌥+Space to record."
            )
            logger.info("VibePaste started successfully")
        except Exception as e:
            logger.error(f"Failed to start VibePaste: {e}")
            rumps.notification(
                title="🎙️ VibePaste",
                subtitle="Error",
                message=f"Failed to start: {e}"
            )
    
    def stop_vibepaste(self, sender):
        """Stop the VibePaste keyboard listener"""
        if self.vibepaste:
            try:
                self.vibepaste.stop()
            except Exception as e:
                logger.error(f"Error stopping VibePaste: {e}")
            self.vibepaste = None
            self.title = "🎙️ (off)"
            rumps.notification(
                title="🎙️ VibePaste",
                subtitle="Stopped",
                message="VibePaste has been stopped."
            )
        else:
            rumps.notification(
                title="🎙️ VibePaste",
                subtitle="Not Running",
                message="VibePaste is not currently running."
            )


def main():
    # Trigger Accessibility permission prompt if needed
    if not check_accessibility(prompt=True):
        print("⚠️  Accessibility permissions denied. Please grant them in System Settings.")

    if not RUMPS_AVAILABLE:

        print("❌ rumps not installed. Installing...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "rumps"], check=True)
        print("✅ rumps installed. Please restart the app.")
        sys.exit(0)
    
    app = VibePasteMenuBar()
    app.run()


if __name__ == "__main__":
    main()


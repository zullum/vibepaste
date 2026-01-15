"""On-screen overlay and sound effects using native macOS APIs"""

import subprocess
import logging
import time
import os
import sys

logger = logging.getLogger(__name__)

# Check if we're on macOS
IS_MACOS = os.uname().sysname == 'Darwin'


class PulsatingDotsOverlay:
    """
    Native macOS floating window with pulsating dots animation.
    Uses a separate Python process to handle AppKit's main thread requirement.
    """
    
    def __init__(self, width=100, height=50):
        self.width = width
        self.height = height
        self.process = None
        self.is_visible = False
        
    def show(self, language="English", model="turbo"):
        """Show the pulsating dots overlay"""
        if not IS_MACOS:
            return
            
        if self.is_visible:
            return
            
        self.is_visible = True
        
        # Ensure .vibepaste directory exists
        home_dir = os.path.expanduser("~")
        vibepaste_dir = os.path.join(home_dir, ".vibepaste")
        os.makedirs(vibepaste_dir, exist_ok=True)
        
        script_path = os.path.join(vibepaste_dir, "overlay_ui.py")
        log_path = os.path.join(vibepaste_dir, "overlay.log")
        
        # Write script to file
        script_content = self._get_overlay_script_content(language, model, log_path)
        with open(script_path, "w") as f:
            f.write(script_content)
        
        try:
            # Execute the script file
            # Use the same python executable that launched us
            self.process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=None,
                stderr=None
            )
            print(f"DEBUG: Overlay process started (PID: {self.process.pid})")
        except Exception as e:
            print(f"DEBUG: Failed to start overlay process: {e}")
            import traceback
            traceback.print_exc()
            self._fallback_notification(language, model)

    def hide(self):
        """Hide the overlay"""
        if self.process:
            try:
                print(f"DEBUG: Hiding overlay (PID: {self.process.pid})")
                self.process.terminate()
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            except Exception as e:
                print(f"DEBUG: Error hiding overlay: {e}")
            finally:
                self.process = None
        self.is_visible = False
    def _get_overlay_script_content(self, language, model, log_path):
        """Generate the Python script content"""
        return f'''
import math
import signal
import sys
import os

# Debug log
LOG_PATH = "{log_path}"
def log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(str(msg) + "\\n")
    except:
        pass

log("--- Overlay Script Starting ---")
log(f"Python: {{sys.executable}}")
log(f"CWD: {{os.getcwd()}}")

# Handle termination gracefully
def signal_handler(sig, frame):
    log("Received signal to exit")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

try:
    import AppKit
    from AppKit import (
        NSApplication, NSWindow, NSView, NSColor, NSBezierPath,
        NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
        NSFloatingWindowLevel, NSScreen, NSTimer, NSRunLoop
    )
    from Foundation import NSRect, NSMakeRect, NSObject
    import objc
except ImportError as e:
    log(f"Import Error: {{e}}")
    sys.exit(1)
except Exception as e:
    log(f"General Error: {{e}}")
    sys.exit(1)

log("Imports successful")

# Initialize app
try:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory - no dock icon
    log("NSApplication initialized")

    class DotsView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(DotsView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.phase = 0.0
            return self
            
        def animate_(self, timer):
            self.phase += 0.15
            if self.phase > 6.28:
                self.phase = 0
            self.setNeedsDisplay_(True)
            
        def drawRect_(self, rect):
            # Background with rounded corners
            bg_rect = NSMakeRect(0, 0, rect.size.width, rect.size.height)
            bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bg_rect, 12, 12
            )
            
            # Dark semi-transparent background
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.08, 0.08, 0.1, 0.92
            ).setFill()
            bg_path.fill()
            
            # Subtle border
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.3, 0.3, 0.35, 0.8
            ).setStroke()
            bg_path.setLineWidth_(1.0)
            bg_path.stroke()
            
            # Draw 3 pulsating dots
            dot_radius_base = 5
            dot_spacing = 18
            center_x = rect.size.width / 2
            center_y = rect.size.height / 2
            
            for i in range(3):
                # Phase offset creates wave effect
                dot_phase = self.phase - (i * 0.7)
                
                # Smooth pulsation
                scale = 0.5 + 0.5 * math.sin(dot_phase)
                alpha = 0.4 + 0.6 * math.sin(dot_phase)
                
                x = center_x + (i - 1) * dot_spacing
                y = center_y
                
                # Outer glow
                glow_radius = dot_radius_base * (1.0 + scale * 0.8)
                glow_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.25, 0.2, alpha * 0.25
                )
                glow_rect = NSMakeRect(
                    x - glow_radius, y - glow_radius,
                    glow_radius * 2, glow_radius * 2
                )
                glow_path = NSBezierPath.bezierPathWithOvalInRect_(glow_rect)
                glow_color.setFill()
                glow_path.fill()
                
                # Main dot
                dot_radius = dot_radius_base * (0.7 + scale * 0.5)
                dot_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.25, 0.2, alpha
                )
                dot_rect = NSMakeRect(
                    x - dot_radius, y - dot_radius,
                    dot_radius * 2, dot_radius * 2
                )
                dot_path = NSBezierPath.bezierPathWithOvalInRect_(dot_rect)
                dot_color.setFill()
                dot_path.fill()
                
        def isOpaque(self):
            return False

    # Create window
    screen = NSScreen.mainScreen()
    if screen:
        screen_frame = screen.frame()
        width, height = {self.width}, {self.height}
        x = (screen_frame.size.width - width) / 2
        y = screen_frame.size.height - height - 35
        
        window_rect = NSMakeRect(x, y, width, height)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            window_rect,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )

        window.setLevel_(NSFloatingWindowLevel + 100)
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setIgnoresMouseEvents_(True)
        window.setCollectionBehavior_(
            1 << 0 |  # CanJoinAllSpaces
            1 << 3    # Stationary
        )

        # Create view
        dots_view = DotsView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        window.setContentView_(dots_view)
        window.makeKeyAndOrderFront_(None)

        # Animation timer
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.016,  # ~60fps
            dots_view,
            'animate:',
            None,
            True
        )
        
        log("Window created and timer scheduled")
        
        # Run the app
        app.run()
    else:
        log("Error: No screen found")

except Exception as e:
    log(f"Runtime Error: {{e}}")
'''
            
    def _fallback_notification(self, language, model):
        """Fallback when native overlay fails"""
        try:
            script = f'''
            display notification "Recording... ({language}, {model})" with title "🎙️ VibePaste"
            '''
            subprocess.Popen(['osascript', '-e', script], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            pass


class RecordingOverlay:
    """Visual overlay for recording status - wrapper for PulsatingDotsOverlay"""
    
    def __init__(self):
        self.overlay = PulsatingDotsOverlay(width=100, height=50)
        
    def show(self, language="English", model="turbo"):
        """Show recording overlay"""
        self.overlay.show(language, model)
        logger.info("Recording overlay shown")

    def hide(self):
        """Hide recording overlay"""
        self.overlay.hide()
        logger.info("Recording overlay hidden")


class SoundEffects:
    """Sound effects for recording events"""
    
    @staticmethod
    def play_start():
        """Play sound when recording starts"""
        try:
            subprocess.Popen(
                ['afplay', '-v', '0.3', '/System/Library/Sounds/Hero.aiff'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.debug("Played start sound")
        except Exception as e:
            logger.error(f"Failed to play start sound: {e}")
    
    @staticmethod
    def play_stop():
        """Play sound when recording stops"""
        try:
            subprocess.Popen(
                ['afplay', '-v', '0.3', '/System/Library/Sounds/Glass.aiff'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.debug("Played stop sound")
        except Exception as e:
            logger.error(f"Failed to play stop sound: {e}")

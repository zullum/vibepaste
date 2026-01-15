"""Keyboard listener for global hotkeys"""

from pynput import keyboard
import logging
import threading

logger = logging.getLogger(__name__)


class KeyboardListener:
    """Listens for global hotkeys and triggers callbacks"""

    def __init__(self):
        self.listener = None
        self.current_keys = set()
        self.callbacks = {}
        self.single_key_callbacks = {}
        self.active_hotkey = None
        self.lock = threading.RLock()

    def reset_keys(self):
        """Force reset of tracked keys and state"""
        with self.lock:
            self.current_keys.clear()
            # Reset all pressed flags
            for config in self.callbacks.values():
                config['pressed'] = False
            for config in self.single_key_callbacks.values():
                config['pressed'] = False
            logger.info("Keyboard state reset")

    def register_hotkey(self, name, modifier, key, on_toggle):
        """
        Register a hotkey combination (toggle mode)

        Args:
            name: Unique name for this hotkey (e.g., 'english', 'bosnian')
            modifier: Modifier key (e.g., keyboard.Key.alt_l)
            key: Main key (e.g., 'e', 'b')
            on_toggle: Callback when hotkey is pressed (toggle action)
        """
        self.callbacks[name] = {
            'modifier': modifier,
            'key': key,
            'on_toggle': on_toggle,
            'pressed': False
        }
        logger.info(f"Registered hotkey: {name} ({modifier}+{key})")

    def register_single_key(self, name, key, on_press):
        """
        Register a single key press (without modifiers)

        Args:
            name: Unique name for this key (e.g., 'stop')
            key: Key to detect (e.g., keyboard.Key.space)
            on_press: Callback when key is pressed alone (no modifiers)
        """
        self.single_key_callbacks[name] = {
            'key': key,
            'on_press': on_press,
            'pressed': False
        }
        logger.info(f"Registered single key: {name} ({key})")

    def _on_press(self, key):
        """Handle key press events (toggle mode)"""
        with self.lock:
            # Add key to current pressed keys
            self.current_keys.add(key)
            
            # DEBUG: Print current keys
            try:
                print(f"DEBUG: Key pressed: {key}. Current keys: {self.current_keys}")
                # Log explicitly to file just in case stdout redirection fails
                with open("/tmp/vibepaste_keys.log", "a") as f:
                    f.write(f"Key: {key}, Current: {self.current_keys}\\n")
            except:
                pass

            # Check each registered hotkey

            # Check each registered hotkey
            for name, config in self.callbacks.items():
                modifier = config['modifier']
                hotkey_key = config['key']

                # Check if both modifier and key are pressed
                # Use exact modifier match (left vs right Option)
                modifier_pressed = modifier in self.current_keys

                # Check if hotkey_key is in current pressed keys
                # This allows detection regardless of press order
                # Supports both char keys (like 'e') and Key objects (like Key.space)
                key_pressed = False
                for pressed_key in self.current_keys:
                    # Check for Key objects (e.g., Key.space)
                    if pressed_key == hotkey_key:
                        key_pressed = True
                        break
                    # Check for char keys (e.g., 'e', 'b')
                    try:
                        if hasattr(pressed_key, 'char') and pressed_key.char == hotkey_key:
                            key_pressed = True
                            break
                    except AttributeError:
                        pass

                # If hotkey combination is active and not already triggered
                if modifier_pressed and key_pressed and not config['pressed']:
                    config['pressed'] = True
                    logger.info(f"Hotkey pressed: {name}")

                    # Call on_toggle callback
                    if config['on_toggle']:
                        try:
                            config['on_toggle'](name)
                        except Exception as e:
                            logger.error(f"Error in on_toggle callback: {e}")

            # Check single key triggers
            # DEBUG: Trace callbacks
            try:
                with open("/tmp/vibepaste_keys.log", "a") as f:
                    f.write(f"Callbacks count: {len(self.single_key_callbacks)}. Keys: {list(self.single_key_callbacks.keys())}\n")
            except:
                pass

            for name, config in self.single_key_callbacks.items():
                is_key = (key == config['key']) or \
                         (hasattr(key, 'char') and hasattr(config['key'], 'char') and key.char == config['key'].char)
                
                if key == keyboard.Key.space:
                     try:
                        with open("/tmp/vibepaste_keys.log", "a") as f:
                            f.write(f"Compare Space: name={name}, config_key={config['key']}, match={is_key}\n")
                     except:
                        pass
                
                # Check that ONLY this key is pressed (no modifiers)
                no_modifiers = not any(
                    k in self.current_keys for k in [
                        keyboard.Key.alt_l, keyboard.Key.alt_r,
                        keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
                        keyboard.Key.cmd_l, keyboard.Key.cmd_r,
                        keyboard.Key.shift_l, keyboard.Key.shift_r
                    ]
                )

                if is_key:
                    # DEBUG LOGGING FOR SINGLE KEY MATCH
                    try:
                        with open("/tmp/vibepaste_keys.log", "a") as f:
                            f.write(f"Check Single: {name}, is_key={is_key}, no_modifiers={no_modifiers}, pressed={config['pressed']}\\n")
                    except:
                        pass

                if is_key and no_modifiers and not config['pressed']:
                    config['pressed'] = True
                    logger.info(f"Single key pressed: {name}")
                    
                    try:
                        with open("/tmp/vibepaste_keys.log", "a") as f:
                            f.write(f"TRIGGERING SINGLE KEY: {name}\\n")
                    except:
                        pass
                    
                    # Call on_press callback
                    if config['on_press']:
                        try:
                            config['on_press']()
                        except Exception as e:
                            logger.error(f"Error in on_press callback: {e}")

    def _on_release(self, key):
        """Handle key release events (reset pressed state)"""
        with self.lock:
            # Reset pressed state for all hotkeys when their keys are released
            for name, config in self.callbacks.items():
                modifier = config['modifier']
                hotkey_key = config['key']

                # Check if released key is part of this hotkey
                # Use exact modifier match
                is_modifier = key == modifier

                # Check for hotkey key - supports both Key objects and char keys
                is_hotkey_key = (key == hotkey_key)
                if not is_hotkey_key:
                    try:
                        is_hotkey_key = hasattr(key, 'char') and key.char == hotkey_key
                    except AttributeError:
                        pass

                # Reset pressed state when either key is released
                if (is_modifier or is_hotkey_key) and config['pressed']:
                    config['pressed'] = False
                    logger.debug(f"Hotkey released: {name}")

            # Reset single key pressed state
            for name, config in self.single_key_callbacks.items():
                if key == config['key'] and config['pressed']:
                    config['pressed'] = False
                    logger.debug(f"Single key released: {name}")

            # Remove key from current pressed keys
            if key in self.current_keys:
                self.current_keys.remove(key)

    def start(self):
        """Start listening for keyboard events"""
        if self.listener is not None:
            logger.warning("Listener already started")
            return

        logger.info("Starting keyboard listener")
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        self.listener.start()

    def stop(self):
        """Stop listening for keyboard events"""
        if self.listener is None:
            logger.warning("Listener not started")
            return

        logger.info("Stopping keyboard listener")
        self.listener.stop()
        self.listener = None

    def is_running(self):
        """Check if listener is running"""
        return self.listener is not None and self.listener.is_alive()

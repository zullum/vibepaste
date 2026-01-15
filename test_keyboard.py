"""Test keyboard detection"""
from pynput import keyboard
import time

pressed_keys = set()

def on_press(key):
    print(f"✓ Key pressed: {key}")
    pressed_keys.add(key)
    
    # Check for Left Alt + E
    if keyboard.Key.alt_l in pressed_keys:
        try:
            if hasattr(key, 'char') and key.char == 'e':
                print(">>> DETECTED: Left Option + E")
        except AttributeError:
            pass

def on_release(key):
    print(f"✗ Key released: {key}")
    if key in pressed_keys:
        pressed_keys.remove(key)

print("Testing keyboard detection...")
print("Press Left Option + E")
print("Press Ctrl+C to exit\n")

listener = keyboard.Listener(on_press=on_press, on_release=on_release)
listener.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping...")
    listener.stop()

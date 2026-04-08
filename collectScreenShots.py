import os
import time
import subprocess
from datetime import datetime

import cv2
import numpy as np
from pynput import mouse
import win32gui

# =========================
# CONFIG
# =========================
ADB_DEVICE = "127.0.0.1:5575"

# Smaller rectangular crop:
# width = 2 * CROP_HALF_WIDTH
# height = 2 * CROP_HALF_HEIGHT
CROP_HALF_WIDTH = 110
CROP_HALF_HEIGHT = 55

SAVE_DIR = "bluestacks_click_crops"

WINDOW_KEYWORDS = [
    "BlueStacks",
    "App Player",
    "Reroller",
]

# =========================
# HELPERS
# =========================
def ensure_output_dir():
    os.makedirs(SAVE_DIR, exist_ok=True)


def adb_connect():
    try:
        result = subprocess.run(
            ["adb", "connect", ADB_DEVICE],
            capture_output=True,
            text=True,
            timeout=10
        )
        print("[ADB]", result.stdout.strip() or result.stderr.strip())
    except Exception as e:
        print(f"[ERROR] Failed to connect ADB: {e}")


def adb_get_screen_size():
    try:
        result = subprocess.run(
            ["adb", "-s", ADB_DEVICE, "shell", "wm", "size"],
            capture_output=True,
            text=True,
            timeout=10
        )

        output = result.stdout.strip()
        for line in output.splitlines():
            if "size:" in line:
                size_part = line.split(":")[-1].strip()
                w, h = size_part.lower().split("x")
                return int(w), int(h)

        raise RuntimeError(f"Could not parse wm size output: {output}")
    except Exception as e:
        raise RuntimeError(f"Failed to get screen size: {e}")


def adb_screencap():
    try:
        result = subprocess.run(
            ["adb", "-s", ADB_DEVICE, "exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors="ignore"))

        data = np.frombuffer(result.stdout, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)

        if img is None:
            raise RuntimeError("OpenCV failed to decode screenshot bytes.")

        return img
    except Exception as e:
        raise RuntimeError(f"Failed to capture screenshot: {e}")


def crop_around_point_rect(img, x, y, half_width, half_height):
    """
    Crop a rectangle around (x, y) in device coordinates.
    Returns cropped image and crop bounds.
    """
    h, w = img.shape[:2]

    x1 = max(0, x - half_width)
    y1 = max(0, y - half_height)
    x2 = min(w, x + half_width)
    y2 = min(h, y + half_height)

    crop = img[y1:y2, x1:x2]
    return crop, (x1, y1, x2, y2)


def find_bluestacks_window():
    matches = []

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = win32gui.GetWindowText(hwnd)
        if not title:
            return

        for kw in WINDOW_KEYWORDS:
            if kw.lower() in title.lower():
                matches.append((hwnd, title))
                break

    win32gui.EnumWindows(enum_handler, None)

    if not matches:
        return None

    hwnd, title = matches[0]
    print(f"[INFO] Using window: {title}")
    return hwnd


def get_client_rect_screen(hwnd):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_left_top = win32gui.ClientToScreen(hwnd, (left, top))
    screen_right_bottom = win32gui.ClientToScreen(hwnd, (right, bottom))

    sl, st = screen_left_top
    sr, sb = screen_right_bottom
    return sl, st, sr, sb


def point_in_rect(x, y, rect):
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def window_to_device_coords(click_x, click_y, client_rect, device_size):
    left, top, right, bottom = client_rect
    client_w = right - left
    client_h = bottom - top

    dev_w, dev_h = device_size

    rel_x = click_x - left
    rel_y = click_y - top

    rel_x = max(0, min(rel_x, client_w))
    rel_y = max(0, min(rel_y, client_h))

    dev_x = int(rel_x * dev_w / client_w)
    dev_y = int(rel_y * dev_h / client_h)

    dev_x = max(0, min(dev_x, dev_w - 1))
    dev_y = max(0, min(dev_y, dev_h - 1))

    return dev_x, dev_y


def save_images(full_img, crop_img, dev_x, dev_y, crop_bounds):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    full_path = os.path.join(SAVE_DIR, f"full_{timestamp}.png")
    crop_path = os.path.join(SAVE_DIR, f"crop_{timestamp}.png")

    cv2.imwrite(full_path, full_img)
    cv2.imwrite(crop_path, crop_img)

    print(f"[SAVED] Full screenshot: {full_path}")
    print(f"[SAVED] Cropped region:   {crop_path}")
    print(f"[INFO] Device click: ({dev_x}, {dev_y})")
    print(f"[INFO] Crop bounds: {crop_bounds}")


class BlueStacksClickCropper:
    def __init__(self):
        self.hwnd = None
        self.device_size = None

    def setup(self):
        ensure_output_dir()
        adb_connect()
        self.device_size = adb_get_screen_size()
        print(f"[INFO] Device size: {self.device_size[0]}x{self.device_size[1]}")

        self.hwnd = find_bluestacks_window()
        if not self.hwnd:
            raise RuntimeError(
                "Could not find a BlueStacks window. "
                "Open BlueStacks first and make sure its title contains 'BlueStacks' or 'App Player'."
            )

    def handle_click(self, x, y, button, pressed):
        if not pressed:
            return

        if button != mouse.Button.left:
            return

        try:
            client_rect = get_client_rect_screen(self.hwnd)

            if not point_in_rect(x, y, client_rect):
                return

            print(f"\n[CLICK] BlueStacks click detected at screen coords: ({x}, {y})")

            dev_x, dev_y = window_to_device_coords(
                x, y, client_rect, self.device_size
            )

            full_img = adb_screencap()
            crop_img, crop_bounds = crop_around_point_rect(
                full_img,
                dev_x,
                dev_y,
                CROP_HALF_WIDTH,
                CROP_HALF_HEIGHT
            )

            save_images(full_img, crop_img, dev_x, dev_y, crop_bounds)

        except Exception as e:
            print(f"[ERROR] {e}")

    def run(self):
        print("[INFO] Listening for clicks inside BlueStacks...")
        print("[INFO] Left-click inside the BlueStacks game window to save a cropped screenshot.")
        print("[INFO] Press Ctrl+C in the terminal to stop.\n")

        with mouse.Listener(on_click=self.handle_click) as listener:
            listener.join()


if __name__ == "__main__":
    try:
        app = BlueStacksClickCropper()
        app.setup()
        app.run()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    except Exception as e:
        print(f"[FATAL] {e}")
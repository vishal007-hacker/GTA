"""
Gemini-driven auto-play bot for GTA: San Andreas.

Loop: screenshot the game window -> ask Gemini what to press -> simulate that
key via SendInput (hardware-level, works with DirectInput games) -> repeat.

Reads the Gemini API key / base_url / model straight out of re-agent.yaml so
the key only lives in one place.
"""
import base64
import ctypes
import io
import json
import re
import sys
import time
from ctypes import wintypes

import requests
import win32gui
import win32con
import yaml
from PIL import ImageGrab

CONFIG_PATH = r"D:\GTA\re-agent.yaml"
WINDOW_TITLE = "GTA: San Andreas"
LOG_PATH = r"D:\GTA\autoplay_log.jsonl"

MAX_STEPS = 80
MAX_SECONDS = 15 * 60

# --- SendInput plumbing (hardware-level key events via scan codes) ---------

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", Input_I)]


INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

SCANCODES = {
    "ESC": (0x01, False),
    "ENTER": (0x1C, False),
    "SPACE": (0x39, False),
    "SHIFT": (0x2A, False),
    "W": (0x11, False),
    "A": (0x1E, False),
    "S": (0x1F, False),
    "D": (0x20, False),
    "UP": (0x48, True),
    "DOWN": (0x50, True),
    "LEFT": (0x4B, True),
    "RIGHT": (0x4D, True),
}


def _send_scan(scan, extended, keyup):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if keyup else 0)
    extra = ctypes.c_ulong(0)
    ii = Input_I()
    ii.ki = KeyBdInput(0, scan, flags, 0, ctypes.pointer(extra))
    inp = Input(INPUT_KEYBOARD, ii)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def press_keys(keys, hold_s):
    codes = [SCANCODES[k] for k in keys if k in SCANCODES]
    for scan, ext in codes:
        _send_scan(scan, ext, False)
    time.sleep(hold_s)
    for scan, ext in reversed(codes):
        _send_scan(scan, ext, True)


# --- window / capture -------------------------------------------------------


def get_hwnd():
    return win32gui.FindWindow(None, WINDOW_TITLE)


def ensure_foreground(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(1)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def capture(hwnd):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    return img


def encode_jpeg_b64(img, max_w=960):
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- Gemini ------------------------------------------------------------------

SYSTEM_PROMPT = """You are playing GTA: San Andreas (PC, 2005) on behalf of a player, from a \
single screenshot each turn. The game has just started.

Valid actions (pick exactly ONE per turn):
- ENTER, ESC, SPACE, SHIFT
- UP, DOWN, LEFT, RIGHT   (menu navigation)
- W, A, S, D              (movement/driving - you may combine e.g. "W A" to turn while moving)
- WAIT                    (do nothing this turn, e.g. during an uninterruptible cutscene)

Typical flow: legal/title screen -> press ENTER to continue -> logo screens -> ENTER \
-> main menu (New Game / Load Game / Options / Quit, navigate with UP/DOWN, confirm with ENTER) \
-> choose New Game -> intro cutscene (do NOT skip it, it sets up the story: WAIT through it) \
-> gains control of CJ -> walk/drive using W/A/S/D.

Respond with ONLY a compact JSON object, no markdown fences, no commentary:
{"stage": "<title|logo|main_menu|new_game_confirm|cutscene|walking|driving|other>", \
"action": "<one of the valid actions, space separated if combining keys>", \
"reason": "<very short reason, <15 words>"}
"""


AUTOPLAY_MODEL = "gemini-flash-lite-latest"  # separate free-tier quota from the re-agent model


def call_gemini(cfg, img_b64, step):
    base_url = cfg["llm"]["base_url"].rstrip("/")
    model = AUTOPLAY_MODEL
    api_key = cfg["llm"]["api_key"]
    url = f"{base_url}/chat/completions"

    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Step {step}. What do I do next?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ],
            },
        ],
    }
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    hwnd = get_hwnd()
    if not hwnd:
        print("GTA window not found - is gta_sa.exe running?")
        sys.exit(1)

    ensure_foreground(hwnd)
    time.sleep(1)

    start = time.time()
    log = open(LOG_PATH, "a", encoding="utf-8")

    for step in range(1, MAX_STEPS + 1):
        if time.time() - start > MAX_SECONDS:
            print("Time budget reached, stopping.")
            break

        hwnd = get_hwnd()
        if not hwnd:
            print("Window disappeared, stopping.")
            break
        ensure_foreground(hwnd)

        img = capture(hwnd)
        img_b64 = encode_jpeg_b64(img)

        try:
            decision = call_gemini(cfg, img_b64, step)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print(f"[{step}] Gemini quota exhausted (429), stopping run.")
                print(e.response.text[:500])
                break
            print(f"[{step}] Gemini call failed: {e}")
            time.sleep(2)
            continue
        except Exception as e:
            print(f"[{step}] Gemini call failed: {e}")
            time.sleep(2)
            continue

        action = decision.get("action", "WAIT").upper()
        stage = decision.get("stage", "?")
        reason = decision.get("reason", "")
        print(f"[{step}] stage={stage} action={action} :: {reason}")
        log.write(json.dumps({"step": step, "t": time.time() - start, **decision}) + "\n")
        log.flush()

        keys = [k for k in action.replace("+", " ").split() if k in SCANCODES]
        if keys:
            hold = 0.8 if any(k in ("W", "A", "S", "D") for k in keys) else 0.08
            press_keys(keys, hold)
        time.sleep(0.4)

    log.close()
    print("Done. Transcript:", LOG_PATH)


if __name__ == "__main__":
    main()

"""
Direct memory control of GTA:SA's CPad input state, bypassing DirectInput
entirely.

The gta-reversed project (https://github.com/gta-reversed/gta-reversed)
targets GTA:SA 1.0 US; our install is 1.01, which turned out to have a
materially different code layout (not just a constant shift - ReconcileTwoControllersInput
and ProcessPCSpecificStuff land on unrelated code at the 1.0 addresses).
Field *layouts* (CKeyboardState/CControllerState, same compiler) are unaffected -
only the *base addresses* of the live globals differ. Re-derived for this exact
binary (sha256 cd885d84...) via Ghidra, by finding the distinctive
ClearKeyBoardHistory-style history-rotation copy (three 0x270-byte blocks) and
GetAnaloguePadUp/Down/Left/Right (which read DAT_00b75ad8 directly):
  NewKeyState @ 0xB75810  (was 0xB73190 in the v1.0 reference)
  OldKeyState @ 0xB755A0  (was 0xB72F20)
  TempKeyState @ 0xB75330 (was 0xB72CB0)
  Pads[0].NewState @ 0xB75AD8 (was 0xB73458)   <- confirmed via GetAnaloguePad* getters
  source/game_sa/KeyboardState.h  -> CKeyboardState field layout (VALIDATE_SIZE 0x270)
  source/game_sa/ControllerState.h-> CControllerState field layout (VALIDATE_SIZE 0x30)

CPad::Update() recomputes NewState/NewKeyState from real devices every frame,
so a single write gets wiped out ~16ms later. We fight that by hammering the
write in a tight loop for the duration we want the "key" held.
"""
import ctypes
import time
import psutil

PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ALL_ACCESS_SUBSET = PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION

NEWKEYSTATE = 0xB75810
NEWSTATE = 0xB75AD8  # CPad::Pads[0].NewState (verified for v1.01 via GetAnaloguePad* getters)

KEY_OFFSETS = {
    "up": 0x226, "down": 0x228, "left": 0x22A, "right": 0x22C,
    "enter": 0x23C, "extenter": 0x25A, "esc": 0x218, "tab": 0x256,
}

PAD_OFFSETS = {
    "LeftStickX": 0x00, "LeftStickY": 0x02, "RightStickX": 0x04, "RightStickY": 0x06,
    "LeftShoulder1": 0x08, "LeftShoulder2": 0x0A, "RightShoulder1": 0x0C, "RightShoulder2": 0x0E,
    "DPadUp": 0x10, "DPadDown": 0x12, "DPadLeft": 0x14, "DPadRight": 0x16,
    "Start": 0x18, "Select": 0x1A,
    "ButtonSquare": 0x1C, "ButtonTriangle": 0x1E, "ButtonCross": 0x20, "ButtonCircle": 0x22,
    "ShockButtonL": 0x24, "ShockButtonR": 0x26,
}


class MemPad:
    def __init__(self):
        pid = next(p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "gta_sa.exe")
        self.pid = pid
        self.handle = ctypes.windll.kernel32.OpenProcess(PROCESS_ALL_ACCESS_SUBSET, False, pid)
        if not self.handle:
            raise OSError(f"OpenProcess failed, err={ctypes.windll.kernel32.GetLastError()}")

    def close(self):
        ctypes.windll.kernel32.CloseHandle(self.handle)

    def _write_i16(self, addr, value):
        buf = ctypes.c_int16(value)
        ctypes.windll.kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(addr), ctypes.byref(buf), 2, None)

    def hammer_key(self, key_names, hold_s=0.15, value=1, interval_s=0.003, release=True):
        addrs = [NEWKEYSTATE + KEY_OFFSETS[k] for k in key_names]
        end = time.time() + hold_s
        while time.time() < end:
            for a in addrs:
                self._write_i16(a, value)
            time.sleep(interval_s)
        if release:
            for a in addrs:
                self._write_i16(a, 0)

    def hammer_pad(self, fields, hold_s=0.5, interval_s=0.003, release=True):
        """fields: dict of PAD_OFFSETS key -> int16 value, held simultaneously."""
        writes = [(NEWSTATE + PAD_OFFSETS[k], v) for k, v in fields.items()]
        end = time.time() + hold_s
        while time.time() < end:
            for addr, v in writes:
                self._write_i16(addr, v)
            time.sleep(interval_s)
        if release:
            for addr, _ in writes:
                self._write_i16(addr, 0)


if __name__ == "__main__":
    pad = MemPad()
    print("attached to pid", pad.pid)

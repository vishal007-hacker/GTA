# GTA:SA Input Automation

Tools for driving a real, running copy of GTA: San Andreas (v1.01) from the
outside — a Gemini vision loop that decides what to do, and a memory-level
input layer that actually does it.

## Why this exists

GTA:SA acquires its keyboard and mouse through DirectInput in exclusive mode,
which reads raw hardware input directly and ignores OS-level synthetic input
(`SendInput`, `keybd_event`, etc.) entirely. That rules out the usual
screenshot-and-fake-keypress approach to building a game-playing bot. Instead,
`mem_pad.py` writes straight into the game's own `CPad` input-state structure
in process memory, which the game can't tell apart from a real controller.

## Files

- **`mem_pad.py`** — `MemPad` class: attaches to the running `gta_sa.exe`
  process and writes directly into its live `CPad` state (menu navigation via
  `NewKeyState`/`NewState.DPad*`, movement via `NewState.LeftStickX/Y`, etc.).
  Since `CPad::Update()` recomputes this state from real devices every frame,
  a single write gets overwritten ~16ms later — so writes are hammered in a
  tight loop for the duration a "key" should be held, then explicitly released.
- **`gemini_autoplay.py`** — the decision loop: screenshot the game window,
  send it to Gemini asking what to do next, map the answer to a `mem_pad`
  action, repeat. Logs every decision to `autoplay_log.jsonl`.
- **`re-agent.yaml.example`, `ghidra-bridge.yaml.example`** — config
  templates. Copy to `re-agent.yaml` / `ghidra-bridge.yaml` (gitignored) and
  fill in your own Gemini API key and local Ghidra paths.

## On the memory addresses

The [gta-reversed](https://github.com/gta-reversed/gta-reversed) project
publishes `CPad` addresses for GTA:SA **v1.0 US**. This install is **v1.01**,
and those addresses turned out to point at unrelated code (confirmed via
Ghidra — e.g. the published `ProcessPCSpecificStuff` address decompiles to
unrelated MSVC CRT locale-handling code in this binary, not game logic).

The real addresses for this exact binary were re-derived by:
1. Fully analyzing `gta_sa.exe` with Ghidra (12.1.4 headless).
2. Finding the distinctive keyboard-history rotation copy (three `0x270`-byte
   block copies — `sizeof(CKeyboardState)`) to locate `NewKeyState` /
   `OldKeyState` / `TempKeyState`.
3. Finding `GetAnaloguePadUp/Down/Left/Right` (four near-identical functions
   reading the same `short` pair) to locate `Pads[0].NewState`.
4. Confirming both against independent cross-references (`IsStandardKeyJustPressed`-equivalent, `GetCurrentKeyPressed`-equivalent).

Verified live by writing `DPadUp`/`DPadDown` and watching the in-game menu
cursor move in response.

```
NewKeyState       @ 0xB75810   (gta-reversed v1.0: 0xB73190)
OldKeyState       @ 0xB755A0   (gta-reversed v1.0: 0xB72F20)
TempKeyState      @ 0xB75330   (gta-reversed v1.0: 0xB72CB0)
Pads[0].NewState  @ 0xB75AD8   (gta-reversed v1.0: 0xB73458)
```

Field *layouts* within `CKeyboardState`/`CControllerState` are unaffected
(same source, same compiler) — only these base addresses differ from the
v1.0 reference.

## What's not in this repo

`.ghidra-exports/`, `ghidra_project/`, `source/`, and `reports/` are
gitignored on purpose: they're decompiled/analyzed output pulled directly
from the copyrighted `gta_sa.exe` binary (20,000+ decompiled functions, a
full Ghidra project database, and reconstructed source). Not appropriate to
publish here.

## Status

- Menu navigation (`DPadUp`/`DPadDown`) confirmed working live.
- Confirm/select (`ButtonCross`) not yet confirmed — open question whether
  the observed cursor movement on press was real input or the menu's own
  idle auto-cycle; still being isolated.
- In-game movement (`LeftStickX`/`LeftStickY`) not yet verified.

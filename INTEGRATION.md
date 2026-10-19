# EVA Stage 2B: Voice Input (hotkey -> speak -> text)

Stage 2A (typed input + Understanding System) is already in the project.
This zip adds the **voice** way in. Spoken words end up in the same
`understand()` function that typed text already uses, with `InputSource.VOICE`.

## How to apply
1. Unzip over the project. New files go in `eva/input/`. One existing file is replaced: `eva/ui/app.py`.
2. `pip install -r requirements_voice.txt`
3. Run EVA. The first run downloads the speech model (`base.en`, about 140 MB) and needs internet once.
4. Run the tests: `python -m unittest tests.test_voice -v`

## What changes
| File | Status | Job |
|---|---|---|
| `eva/input/voice_settings.py` | new | all voice settings (hotkey, model, silence times) |
| `eva/input/voice_logic.py` | new | pure rules: when to stop recording, junk-text filter, hotkey format |
| `eva/input/audio_recorder.py` | new | records the mic until you stop talking |
| `eva/input/speech_to_text.py` | new | faster-whisper, model loaded once |
| `eva/input/hotkey.py` | new | global hotkey (pynput), Qt signal |
| `eva/input/voice_service.py` | new | runs it all in a background thread, updates states, emits `text_ready` |
| `eva/ui/app.py` | replaced | typed and spoken text now share one `handle_input()`; starts the voice service |
| `tests/test_voice.py` | new | 11 tests, no mic or model needed |
| `requirements_voice.txt` | new | extra packages |

Not touched: `main_window.py`, `understand.py`, `request.py`, `config.py`, `types.py`, the state manager, the tray.

## Flow
```
hotkey -> LISTENING -> record until silence -> PROCESSING -> faster-whisper
       -> junk filter -> text_ready(text, confidence)
       -> understand(text, InputSource.VOICE, confidence) -> window label + log
       -> BACKGROUND
```
Every path (no speech, cancel, mic missing, model error, junk text) ends back at BACKGROUND.
Errors show ERROR for 2 seconds first. Pressing the hotkey while listening cancels. Pressing it while processing is ignored.

## Things to know / check when integrating
1. **Assumed names.** The code uses these existing items. If any is named differently, adjust only the import line:
   `eva.common.types.InterfaceState` (BACKGROUND, LISTENING, PROCESSING, ERROR),
   `eva.ui.state_manager.StateManager` (`.state`, `.set_state()`),
   `eva.common.logging_setup.get_logger`.
2. **Settings are in `VoiceSettings`, not `config.py`**, so config.py stays untouched. They can be moved into Config later.
3. **No state guard on purpose.** The hotkey works whatever the current state is (so the dev-panel buttons cannot lock it out). EVA Core can add a guard in Stage 3.
4. **Tuning:** each recording logs its loudest level next to the threshold. If speech is not detected, lower `speech_threshold`. In a noisy room, raise it.
5. **Not in this stage:** wake word, speaking back (TTS), running commands, any LLM.
6. The `eva/input` folder has no `__init__.py` here; add an empty one if your project uses them in other folders.

## How it was tested here
- 11 unit tests pass (stop-recording rules, junk filter, hotkey format).
- State flow checked with a fake mic and fake model: success, junk text, no speech, mic missing, model failure, cancel by second press. All end at BACKGROUND with no stuck thread.
- NOT tested here: the real microphone, real faster-whisper, real global hotkey, real PySide6 window. These need your Windows machine (acceptance checks below).

## Acceptance checks on your machine
1. Press `Ctrl+Alt+E`, say "Hey EVA open notepad", stop: orb goes LISTENING -> PROCESSING -> back; label shows `Heard: [COMMAND_MODE] open notepad`.
2. Press the hotkey and stay silent: after about 5 s you see "I didn't hear anything."
3. Press the hotkey, then again: "Listening cancelled."
4. Disable the microphone in Windows: clear error message, EVA stays usable, typing still works.
5. Do 10 voice commands in a row: no freeze, no stuck state. Window stays responsive.
6. Open the log: each step shows timing (recording time, transcription time, confidence).

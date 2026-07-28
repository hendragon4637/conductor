# Arduino / Embedded Firmware Conventions

## Toolchain
- PlatformIO (CLI, not Arduino IDE): version-pinned deps in `platformio.ini`
- Unit tests on x86 WITHOUT hardware: `pio test -e native`

## Project Structure
- `platformio.ini` — board + framework + deps version-pinned; `[env:native]` for tests
- `src/main.cpp` — setup()/loop() only; wires modules together, no logic
- `src/<module>.cpp` + `include/<module>.h` — one concern per module
- `lib/` — private libraries
- `test/test_<module>/` — Unity tests runnable in native env

## Style Rules
- Non-blocking main loop: `millis()` scheduling, never `delay()` in loop paths
- Pins/intervals/thresholds: named `constexpr` in `include/config.h`
- Separate hardware I/O from logic: pure-logic functions without Arduino.h → testable on x86
- Fixed-width types (`uint32_t`); no dynamic allocation in loop paths
- Serial debug behind `DEBUG` build flag

## Verification Gates
- `pio run` exits 0 → firmware compiles
- `pio test -e native` exits 0 → logic modules verified on x86
- `pio check` — new HIGH defects = fix before completion

## Completion Check
Before marking a node complete, run `bash gates.sh` from the workspace root.
The script must exit 0 and print "ALL GATES GREEN".

# Project

## Setup
pip install platformio

## Run
pio run -e uno

## Test (native, no hardware)
pio test -e native

## Verify
pio run && pio test -e native && pio check --fail-on-defect high

## FLASH
pio run -t upload --upload-port <PORT>
# (requires physical hardware — verify = compile + native tests + static check)

# __APP__

## Setup
pip install platformio

## Run
pio run -e uno

## Test (native, no hardware)
pio test -e native

## Verify
bash gates.sh

## FLASH
pio run -t upload --upload-port <PORT>
# (requires physical hardware — verify = compile + native tests + static check)

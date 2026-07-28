#include "config.h"
#include "blink.h"

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    blink_setup();
}

void loop() {
    blink_poll(millis());
}

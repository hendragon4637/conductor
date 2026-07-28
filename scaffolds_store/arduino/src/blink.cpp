#include "blink.h"
#include <Arduino.h>

static unsigned long lastToggle = 0;
static bool ledState = LOW;

void blink_setup() {
    pinMode(LED_BUILTIN, OUTPUT);
}

void blink_poll(unsigned long now) {
    if (now - lastToggle >= LED_INTERVAL_MS) {
        lastToggle = now;
        ledState = !ledState;
        digitalWrite(LED_BUILTIN, ledState);
    }
}

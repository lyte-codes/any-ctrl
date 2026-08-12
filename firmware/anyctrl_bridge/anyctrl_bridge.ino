/*
 * any-ctrl USB bridge firmware (ATmega32u4: Pro Micro, Leonardo, Micro).
 *
 * The board enumerates over USB as a wired Switch gamepad and plugs into the
 * console. The host computer talks to it over the *hardware UART* (Serial1,
 * pins 0/1) through a USB-to-serial adapter, because the board's own USB port
 * is busy being a controller. See firmware/README.md for the wiring and for
 * the USB VID/PID override the console requires.
 *
 * Framing (identical to anyctrl/backends/serial_bridge.py):
 *
 *     0xA5 | type | length | payload[length] | checksum
 *
 * checksum = type XOR length XOR every payload byte.
 *
 * Host to device: 0x01 HELLO, 0x02 STATE (8 byte report), 0x03 RESET, 0x04 PING
 * Device to host: 0x81 BANNER, 0x82 ACK, 0x84 PONG, 0x8F LOG
 */

#include <Arduino.h>
#include <HID.h>

#define BRIDGE_VERSION "anyctrl-bridge/1 atmega32u4"

static const uint8_t FRAME_START = 0xA5;

static const uint8_t MSG_HELLO = 0x01;
static const uint8_t MSG_STATE = 0x02;
static const uint8_t MSG_RESET = 0x03;
static const uint8_t MSG_PING = 0x04;
static const uint8_t MSG_BANNER = 0x81;
static const uint8_t MSG_ACK = 0x82;
static const uint8_t MSG_PONG = 0x84;
static const uint8_t MSG_LOG = 0x8F;

static const uint8_t REPORT_SIZE = 8;
static const uint8_t HAT_NEUTRAL = 8;
static const uint8_t STICK_CENTER = 128;

/* Resend the HID report at least this often, and drop everything if the host
 * goes quiet: a crashed host must never leave a button held down. */
static const unsigned long REPORT_INTERVAL_MS = 10;
static const unsigned long HOST_TIMEOUT_MS = 1000;

/* Standard wired Switch gamepad report descriptor: 16 buttons, one hat, four
 * 8 bit axes and a vendor byte. */
static const uint8_t PROGMEM kReportDescriptor[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x35, 0x00,        //   Physical Minimum (0)
    0x45, 0x01,        //   Physical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x10,        //   Report Count (16)
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (Button 1)
    0x29, 0x10,        //   Usage Maximum (Button 16)
    0x81, 0x02,        //   Input (Data, Variable, Absolute)
    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x25, 0x07,        //   Logical Maximum (7)
    0x46, 0x3B, 0x01,  //   Physical Maximum (315)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x65, 0x14,        //   Unit (Degrees)
    0x09, 0x39,        //   Usage (Hat switch)
    0x81, 0x42,        //   Input (Data, Variable, Absolute, Null state)
    0x65, 0x00,        //   Unit (None)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x01,        //   Input (Constant) - padding
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x46, 0xFF, 0x00,  //   Physical Maximum (255)
    0x09, 0x30,        //   Usage (X)
    0x09, 0x31,        //   Usage (Y)
    0x09, 0x32,        //   Usage (Z)
    0x09, 0x35,        //   Usage (Rz)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x04,        //   Report Count (4)
    0x81, 0x02,        //   Input (Data, Variable, Absolute)
    0x06, 0x00, 0xFF,  //   Usage Page (Vendor Defined)
    0x09, 0x20,        //   Usage (0x20)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x02,        //   Input (Data, Variable, Absolute)
    0x0A, 0x21, 0x26,  //   Usage (0x2621)
    0x95, 0x08,        //   Report Count (8)
    0x91, 0x02,        //   Output (Data, Variable, Absolute)
    0xC0               // End Collection
};

static uint8_t gReport[REPORT_SIZE];
static unsigned long gLastHostFrame = 0;
static unsigned long gLastReport = 0;
static bool gHostSeen = false;

/* ---------------------------------------------------------------- HID ---- */

static void neutralReport() {
    gReport[0] = 0x00;  // buttons, low byte
    gReport[1] = 0x00;  // buttons, high byte
    gReport[2] = HAT_NEUTRAL;
    gReport[3] = STICK_CENTER;  // left X
    gReport[4] = STICK_CENTER;  // left Y
    gReport[5] = STICK_CENTER;  // right X
    gReport[6] = STICK_CENTER;  // right Y
    gReport[7] = 0x00;          // vendor
}

static void sendReport() {
    HID().SendReport(0, gReport, REPORT_SIZE);
    gLastReport = millis();
}

/* ------------------------------------------------------------- framing --- */

static void sendFrame(uint8_t type, const uint8_t *payload, uint8_t length) {
    uint8_t checksum = type ^ length;
    Serial1.write(FRAME_START);
    Serial1.write(type);
    Serial1.write(length);
    for (uint8_t i = 0; i < length; i++) {
        Serial1.write(payload[i]);
        checksum ^= payload[i];
    }
    Serial1.write(checksum);
}

static void sendText(uint8_t type, const char *text) {
    sendFrame(type, (const uint8_t *)text, (uint8_t)strlen(text));
}

static void handleFrame(uint8_t type, const uint8_t *payload, uint8_t length) {
    gLastHostFrame = millis();
    gHostSeen = true;
    switch (type) {
        case MSG_HELLO:
            sendText(MSG_BANNER, BRIDGE_VERSION);
            break;
        case MSG_STATE:
            if (length == REPORT_SIZE) {
                memcpy(gReport, payload, REPORT_SIZE);
                sendReport();
            } else {
                sendText(MSG_LOG, "bad state length");
            }
            break;
        case MSG_RESET:
            neutralReport();
            sendReport();
            sendFrame(MSG_ACK, NULL, 0);
            break;
        case MSG_PING:
            sendFrame(MSG_PONG, NULL, 0);
            break;
        default:
            sendText(MSG_LOG, "unknown frame");
            break;
    }
}

/* Incremental parser: one byte at a time, resynchronising on a bad checksum. */
static void feed(uint8_t byte) {
    static uint8_t state = 0;  // 0 start, 1 type, 2 length, 3 payload, 4 checksum
    static uint8_t type = 0;
    static uint8_t length = 0;
    static uint8_t index = 0;
    static uint8_t checksum = 0;
    static uint8_t payload[64];

    switch (state) {
        case 0:
            if (byte == FRAME_START) state = 1;
            break;
        case 1:
            type = byte;
            checksum = byte;
            state = 2;
            break;
        case 2:
            length = byte;
            checksum ^= byte;
            index = 0;
            if (length > sizeof(payload)) {
                state = 0;  // implausible, resynchronise
            } else {
                state = length ? 3 : 4;
            }
            break;
        case 3:
            payload[index++] = byte;
            checksum ^= byte;
            if (index >= length) state = 4;
            break;
        case 4:
            if (byte == checksum) handleFrame(type, payload, length);
            state = 0;
            break;
    }
}

/* --------------------------------------------------------------- sketch --- */

void setup() {
    static HIDSubDescriptor node(kReportDescriptor, sizeof(kReportDescriptor));
    HID().AppendDescriptor(&node);

    Serial1.begin(115200);
    neutralReport();
    sendReport();
    gLastHostFrame = millis();
}

void loop() {
    while (Serial1.available() > 0) {
        feed((uint8_t)Serial1.read());
    }

    const unsigned long now = millis();

    // Drop every input if the host stops talking to us.
    if (gHostSeen && (now - gLastHostFrame) > HOST_TIMEOUT_MS) {
        neutralReport();
        gHostSeen = false;
        sendReport();
    }

    // Keep the console fed even when the state has not changed.
    if ((now - gLastReport) >= REPORT_INTERVAL_MS) {
        sendReport();
    }
}

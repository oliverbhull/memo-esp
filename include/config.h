#ifndef CONFIG_H
#define CONFIG_H

// WiFi Configuration
#define WIFI_SSID "Founders Guest"
#define WIFI_PASSWORD "artifact1!"
#define WIFI_TIMEOUT_MS 20000

// Audio Configuration
#define SAMPLE_RATE 16000
#define BITS_PER_SAMPLE 16
#define CHANNELS 1
#define BUFFER_SIZE 512  // samples per buffer
#define MAX_RECORDING_SEC 30  // Maximum recording duration in seconds

// Button Configuration (XIAO ESP32-S3 has built-in button on D1/GPIO 1)
#define BUTTON_PIN 1  // Built-in button on XIAO ESP32-S3
#define BUTTON_ACTIVE_LOW true  // Button pulls to ground when pressed

// I2S Configuration for XIAO ESP32-S3 built-in mic
#define I2S_PORT I2S_NUM_0
#define I2S_WS_PIN 42   // Word Select (LRCLK)
#define I2S_SD_PIN 41   // Serial Data (DOUT)
#define I2S_SCK_PIN -1  // Not used in PDM mode

// LED Configuration (XIAO ESP32-S3 built-in RGB LED - common anode)
#define LED_R_PIN 21    // Red LED
#define LED_G_PIN 20    // Green LED
#define LED_B_PIN 22    // Blue LED

// HTTP Configuration
#define SERVER_HOST "10.104.16.88"
#define SERVER_PORT "8000"
#define SERVER_URL "http://10.104.16.88:8000/audio"
#define DEVICE_ID "esp32-dev-01"

// Debug
#define DEBUG_SERIAL true

#endif

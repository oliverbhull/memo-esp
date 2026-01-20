#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <driver/i2s.h>
#include "config.h"

// Global state
bool wifiConnected = false;
bool streamingActive = false;
uint8_t audioBuffer[BUFFER_SIZE * sizeof(int16_t)];
size_t bytesRead = 0;
unsigned long lastStatusCheck = 0;
const unsigned long STATUS_CHECK_INTERVAL = 1000;  // Check status every 1 second

// Function declarations
void setupWiFi();
void setupI2S();
void captureAndStreamAudio();
bool sendAudioChunk(uint8_t* data, size_t length);
bool checkRecordingStatus();

void setup() {
    // Initialize serial for debugging
    Serial.begin(115200);
    delay(2000);  // Longer delay for USB CDC to stabilize

    Serial.println("\n\n=== ESP32-S3 WiFi Audio Streamer ===");
    Serial.println("Device ID: " + String(DEVICE_ID));
    Serial.flush();

    // Connect to WiFi
    setupWiFi();

    // Initialize I2S microphone
    if (wifiConnected) {
        setupI2S();
        Serial.println("System ready - waiting for recording start");
    } else {
        Serial.println("WiFi connection failed - cannot stream audio");
    }
}

void loop() {
    // Check WiFi connection
    if (WiFi.status() != WL_CONNECTED) {
        if (wifiConnected) {
            Serial.println("WiFi disconnected - attempting reconnect");
            wifiConnected = false;
            streamingActive = false;
        }
        setupWiFi();
        delay(5000);
        return;
    }

    // Check recording status from server periodically
    unsigned long now = millis();
    if (now - lastStatusCheck >= STATUS_CHECK_INTERVAL) {
        lastStatusCheck = now;
        bool serverRecording = checkRecordingStatus();

        // Update streaming state based on server
        if (serverRecording && !streamingActive) {
            streamingActive = true;
            Serial.println("\n🔴 Recording started by server");
        } else if (!serverRecording && streamingActive) {
            streamingActive = false;
            Serial.println("\n⏹️  Recording stopped by server");
        }
    }

    // Capture and stream audio if recording
    if (streamingActive) {
        captureAndStreamAudio();
    } else {
        // Small delay when not recording to prevent tight loop
        delay(100);
    }
}

void setupWiFi() {
    Serial.println("\nConnecting to WiFi: " + String(WIFI_SSID));

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED &&
           millis() - startAttempt < WIFI_TIMEOUT_MS) {
        delay(500);
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
        Serial.println("\nWiFi connected!");
        Serial.println("IP address: " + WiFi.localIP().toString());
        Serial.println("Signal strength: " + String(WiFi.RSSI()) + " dBm");
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}

void setupI2S() {
    Serial.println("\nInitializing I2S microphone...");

    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = BUFFER_SIZE,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_SCK_PIN,
        .ws_io_num = I2S_WS_PIN,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = I2S_SD_PIN
    };

    esp_err_t err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("I2S driver install failed: %d\n", err);
        return;
    }

    err = i2s_set_pin(I2S_PORT, &pin_config);
    if (err != ESP_OK) {
        Serial.printf("I2S pin config failed: %d\n", err);
        return;
    }

    // Set PDM microphone clock
    err = i2s_set_clk(I2S_PORT, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
    if (err != ESP_OK) {
        Serial.printf("I2S clock config failed: %d\n", err);
        return;
    }

    Serial.println("I2S microphone initialized successfully");
    Serial.printf("Sample rate: %d Hz, %d-bit, mono\n", SAMPLE_RATE, BITS_PER_SAMPLE);
    Serial.printf("Data rate: ~%d KB/s\n", (SAMPLE_RATE * BITS_PER_SAMPLE) / 8000);
}

void captureAndStreamAudio() {
    // Calculate samples needed for chunk
    const int CHUNK_DURATION_MS = 1000;  // 1 second chunks
    size_t samplesPerChunk = (SAMPLE_RATE * CHUNK_DURATION_MS) / 1000;
    size_t bytesPerChunk = samplesPerChunk * sizeof(int16_t);

    // Allocate chunk buffer
    uint8_t* chunkBuffer = (uint8_t*)malloc(bytesPerChunk);
    if (!chunkBuffer) {
        Serial.println("Failed to allocate chunk buffer");
        delay(1000);
        return;
    }

    size_t chunkBytesRead = 0;
    unsigned long chunkStartTime = millis();

    // Read audio data to fill one chunk
    while (chunkBytesRead < bytesPerChunk) {
        size_t bytesToRead = min(sizeof(audioBuffer), bytesPerChunk - chunkBytesRead);

        esp_err_t result = i2s_read(I2S_PORT, audioBuffer, bytesToRead, &bytesRead, portMAX_DELAY);

        if (result == ESP_OK && bytesRead > 0) {
            memcpy(chunkBuffer + chunkBytesRead, audioBuffer, bytesRead);
            chunkBytesRead += bytesRead;
        } else {
            Serial.printf("I2S read error: %d\n", result);
            break;
        }
    }

    // Send the complete chunk
    if (chunkBytesRead > 0) {
        unsigned long captureTime = millis() - chunkStartTime;

        if (DEBUG_SERIAL) {
            Serial.printf("Captured %d bytes in %lu ms, sending to server...\n",
                         chunkBytesRead, captureTime);
        }

        bool success = sendAudioChunk(chunkBuffer, chunkBytesRead);

        if (DEBUG_SERIAL) {
            Serial.printf("Send %s\n", success ? "successful" : "failed");
        }
    }

    free(chunkBuffer);
}

bool sendAudioChunk(uint8_t* data, size_t length) {
    HTTPClient http;

    // Add device ID and audio metadata to URL
    String url = String(SERVER_URL) +
                 "?device=" + DEVICE_ID +
                 "&rate=" + SAMPLE_RATE +
                 "&bits=" + BITS_PER_SAMPLE +
                 "&channels=" + CHANNELS;

    http.begin(url);
    http.addHeader("Content-Type", "application/octet-stream");
    http.addHeader("X-Audio-Format", "pcm");
    http.addHeader("X-Sample-Rate", String(SAMPLE_RATE));
    http.addHeader("X-Bits-Per-Sample", String(BITS_PER_SAMPLE));
    http.addHeader("X-Channels", String(CHANNELS));

    int httpCode = http.POST(data, length);
    bool success = (httpCode == 200 || httpCode == 204);

    if (!success && DEBUG_SERIAL) {
        Serial.printf("HTTP POST failed, code: %d\n", httpCode);
        if (httpCode > 0) {
            Serial.println("Response: " + http.getString());
        }
    }

    http.end();
    return success;
}

bool checkRecordingStatus() {
    HTTPClient http;
    String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + "/status";

    http.begin(url);
    http.setTimeout(1000);  // 1 second timeout

    int httpCode = http.GET();

    if (httpCode == 200) {
        String payload = http.getString();

        // Parse JSON response: {"recording": true/false}
        int recordingIdx = payload.indexOf("\"recording\"");
        if (recordingIdx >= 0) {
            int trueIdx = payload.indexOf("true", recordingIdx);
            int falseIdx = payload.indexOf("false", recordingIdx);

            http.end();

            if (trueIdx > recordingIdx && (falseIdx < 0 || trueIdx < falseIdx)) {
                return true;
            }
        }
    }

    http.end();
    return false;
}

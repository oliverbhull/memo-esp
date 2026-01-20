#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <driver/i2s.h>
#include <esp_system.h>
#include <math.h>
#include <Preferences.h>
#include "config.h"

// Global state
bool wifiConnected = false;
bool recordingActive = false;
bool wasRecording = false;
uint8_t audioBuffer[BUFFER_SIZE * sizeof(int16_t)];
size_t bytesRead = 0;
unsigned long lastStatusCheck = 0;
const unsigned long STATUS_CHECK_INTERVAL = 200;  // Check status every 200ms for responsive start/stop

// Status check retry logic
int statusCheckFailures = 0;
const int MAX_CONSECUTIVE_FAILURES = 3;  // Only stop recording after 3 consecutive failures
bool lastKnownRecordingState = false;  // Maintain last known good state

// Device unique ID (generated from MAC address)
String deviceId;

// Recording buffer using PSRAM
uint8_t* recordingBuffer = nullptr;
size_t recordingBufferSize = 0;
size_t recordingBufferCapacity = 0;
const size_t MAX_RECORDING_BYTES = MAX_RECORDING_SEC * SAMPLE_RATE * (BITS_PER_SAMPLE / 8);

// Audio quality metrics
struct AudioQualityMetrics {
    float avgDbLevel = 0.0;
    float maxDbLevel = -100.0;
    float minDbLevel = 0.0;
    int clipCount = 0;
    int silenceChunks = 0;
    int i2sErrors = 0;
    int totalChunks = 0;
    float silenceThreshold = -40.0;  // dB threshold for silence
    float clipThreshold = -3.0;  // dB threshold for clipping
} audioMetrics;

// WiFi credential storage
Preferences preferences;
const char* PREF_NAMESPACE = "wifi_storage";
const int MAX_WIFI_NETWORKS = 10;

// Function declarations
void setupWiFi();
void setupI2S();
void startRecording();
void stopRecordingAndUpload();
void captureAudioChunk();
bool uploadRecording();
bool checkRecordingStatus();
String getHttpErrorDescription(int errorCode);
void logHttpError(const char* operation, int httpCode, const char* context = nullptr);
void initializeDefaultWiFiNetworks();
int getSavedWiFiCount();
bool getSavedWiFi(int index, String& ssid, String& password);
bool saveWiFiNetwork(const String& ssid, const String& password);
bool connectToWiFi(const String& ssid, const String& password);

String generateDeviceId() {
    // Get MAC address (unique to each device)
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);

    // Create device ID from last 4 bytes of MAC address
    // Format: memo_XXXXXXXX (8 uppercase hex characters)
    char deviceIdBuf[16];
    snprintf(deviceIdBuf, sizeof(deviceIdBuf), "memo_%02X%02X%02X%02X",
             mac[2], mac[3], mac[4], mac[5]);

    return String(deviceIdBuf);
}

String getHttpErrorDescription(int errorCode) {
    switch (errorCode) {
        case -1:  return "Connection failed";
        case -2:  return "Connection timeout";
        case -3:  return "Invalid response";
        case -4:  return "Too many redirects";
        case -5:  return "Out of memory";
        case -6:  return "Encoding error";
        case -7:  return "Stream write error";
        case -8:  return "Stream read error";
        case -9:  return "Stream timeout";
        case -10: return "Invalid URL";
        case -11: return "Read timeout (server didn't respond in time)";
        default:  return "Unknown error";
    }
}

void logHttpError(const char* operation, int httpCode, const char* context) {
    unsigned long timestamp = millis();
    
    // Format timestamp as seconds.milliseconds
    unsigned long seconds = timestamp / 1000;
    unsigned long milliseconds = timestamp % 1000;
    
    Serial.printf("[%lu.%03lu] ⚠️  %s failed: ", seconds, milliseconds, operation);
    
    // In ESP32 HTTPClient, negative values indicate errors, positive values are HTTP status codes
    if (httpCode < 0) {
        String errorDesc = getHttpErrorDescription(httpCode);
        Serial.printf("Error %d - %s", httpCode, errorDesc.c_str());
    } else if (httpCode > 0) {
        Serial.printf("HTTP %d", httpCode);
    } else {
        Serial.print("Unknown error");
    }
    
    if (context != nullptr) {
        Serial.printf(" (%s)", context);
    }
    
    Serial.println();
}

void setup() {
    // Initialize serial for debugging
    Serial.begin(115200);
    delay(2000);

    // Generate unique device ID from MAC address
    deviceId = generateDeviceId();

    Serial.println("\n\n=== ESP32-S3 WiFi Audio Streamer ===");
    Serial.println("Device ID: " + deviceId);
    Serial.flush();

    // Initialize Preferences for WiFi storage
    preferences.begin(PREF_NAMESPACE, false);
    
    // Initialize default WiFi networks on first run
    initializeDefaultWiFiNetworks();

    // Allocate recording buffer in PSRAM
    recordingBuffer = (uint8_t*)ps_malloc(MAX_RECORDING_BYTES);
    if (!recordingBuffer) {
        Serial.println("ERROR: Failed to allocate PSRAM buffer!");
        Serial.println("Recording will not work.");
    } else {
        recordingBufferCapacity = MAX_RECORDING_BYTES;
        Serial.printf("Allocated %d KB recording buffer in PSRAM\n", recordingBufferCapacity / 1024);
    }

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
            recordingActive = false;
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

        // Handle state transitions
        if (serverRecording && !wasRecording) {
            // START: Server wants to record
            startRecording();
            wasRecording = true;
            recordingActive = true;
            lastKnownRecordingState = true;
        } else if (!serverRecording && wasRecording) {
            // STOP: Only stop if we've had multiple consecutive failures OR server explicitly says stop
            // If it's a network error (statusCheckFailures > 0), maintain last known state
            if (statusCheckFailures >= MAX_CONSECUTIVE_FAILURES) {
                // Too many failures - stop recording as a safety measure
                Serial.println("⚠️  Too many status check failures - stopping recording");
                stopRecordingAndUpload();
                wasRecording = false;
                recordingActive = false;
                lastKnownRecordingState = false;
            } else if (statusCheckFailures == 0) {
                // Server explicitly said to stop (not a network error)
                stopRecordingAndUpload();
                wasRecording = false;
                recordingActive = false;
                lastKnownRecordingState = false;
            }
            // If statusCheckFailures > 0 but < MAX, maintain current recording state
        }
    }

    // Capture audio if recording
    if (recordingActive) {
        captureAudioChunk();
    } else {
        // Small delay when not recording to prevent tight loop
        delay(50);
    }
}

void initializeDefaultWiFiNetworks() {
    int savedCount = getSavedWiFiCount();
    
    // Only initialize defaults if no networks are saved
    if (savedCount == 0) {
        Serial.println("Initializing default WiFi networks...");
        
        // Save default networks
        saveWiFiNetwork("Founders Guest", "artifact1!");
        saveWiFiNetwork("Boston2", "larrybird");
        
        Serial.printf("Saved %d default WiFi networks\n", getSavedWiFiCount());
    } else {
        Serial.printf("Found %d saved WiFi network(s)\n", savedCount);
    }
}

int getSavedWiFiCount() {
    return preferences.getInt("wifi_count", 0);
}

bool getSavedWiFi(int index, String& ssid, String& password) {
    if (index < 0 || index >= MAX_WIFI_NETWORKS) {
        return false;
    }
    
    char keySsid[32];
    char keyPass[32];
    snprintf(keySsid, sizeof(keySsid), "wifi_%d_ssid", index);
    snprintf(keyPass, sizeof(keyPass), "wifi_%d_pass", index);
    
    ssid = preferences.getString(keySsid, "");
    password = preferences.getString(keyPass, "");
    
    return ssid.length() > 0;
}

bool saveWiFiNetwork(const String& ssid, const String& password) {
    if (ssid.length() == 0 || ssid.length() > 32) {
        return false;  // SSID too long or empty
    }
    
    // Check if network already exists
    int count = getSavedWiFiCount();
    for (int i = 0; i < count; i++) {
        String savedSsid;
        String savedPass;
        if (getSavedWiFi(i, savedSsid, savedPass)) {
            if (savedSsid == ssid) {
                // Update existing network
                char keySsid[32];
                char keyPass[32];
                snprintf(keySsid, sizeof(keySsid), "wifi_%d_ssid", i);
                snprintf(keyPass, sizeof(keyPass), "wifi_%d_pass", i);
                preferences.putString(keySsid, ssid);
                preferences.putString(keyPass, password);
                Serial.printf("Updated WiFi network: %s\n", ssid.c_str());
                return true;
            }
        }
    }
    
    // Add new network
    if (count >= MAX_WIFI_NETWORKS) {
        Serial.println("ERROR: Maximum number of WiFi networks reached!");
        return false;
    }
    
    char keySsid[32];
    char keyPass[32];
    snprintf(keySsid, sizeof(keySsid), "wifi_%d_ssid", count);
    snprintf(keyPass, sizeof(keyPass), "wifi_%d_pass", count);
    
    preferences.putString(keySsid, ssid);
    preferences.putString(keyPass, password);
    preferences.putInt("wifi_count", count + 1);
    
    Serial.printf("Saved new WiFi network: %s\n", ssid.c_str());
    return true;
}

bool connectToWiFi(const String& ssid, const String& password) {
    Serial.printf("Attempting to connect to: %s", ssid.c_str());
    
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), password.c_str());
    
    unsigned long startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED &&
           millis() - startAttempt < WIFI_TIMEOUT_MS) {
        delay(500);
        Serial.print(".");
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n✓ Connected!");
        Serial.println("  IP address: " + WiFi.localIP().toString());
        Serial.println("  Signal strength: " + String(WiFi.RSSI()) + " dBm");
        return true;
    } else {
        Serial.println("\n✗ Failed");
        return false;
    }
}

void setupWiFi() {
    int networkCount = getSavedWiFiCount();
    
    if (networkCount == 0) {
        Serial.println("\n⚠️  No saved WiFi networks found!");
        Serial.println("Using fallback from config.h");
        // Fallback to config.h values
        if (connectToWiFi(String(WIFI_SSID), String(WIFI_PASSWORD))) {
            wifiConnected = true;
            // Save this network for future use
            saveWiFiNetwork(String(WIFI_SSID), String(WIFI_PASSWORD));
        } else {
            wifiConnected = false;
        }
        return;
    }
    
    Serial.printf("\nTrying %d saved WiFi network(s)...\n", networkCount);
    
    // Try each saved network in order
    for (int i = 0; i < networkCount; i++) {
        String ssid, password;
        if (getSavedWiFi(i, ssid, password)) {
            Serial.printf("[%d/%d] ", i + 1, networkCount);
            if (connectToWiFi(ssid, password)) {
                wifiConnected = true;
                return;  // Successfully connected
            }
        }
    }
    
    // All networks failed
    Serial.println("\n✗ Failed to connect to any saved WiFi network!");
    wifiConnected = false;
}

void setupI2S() {
    Serial.println("\nInitializing I2S microphone...");

    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,  // Standard I2S format
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 16,  // Increased for better stability
        .dma_buf_len = BUFFER_SIZE,
        .use_apll = true,  // Enable APLL for better clock stability and audio quality
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

    // Set PDM microphone clock with APLL enabled
    err = i2s_set_clk(I2S_PORT, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
    if (err != ESP_OK) {
        Serial.printf("I2S clock config failed: %d\n", err);
        return;
    }

    // Configure PDM microphone gain (higher gain for better sensitivity)
    // Note: This may vary by board, but setting PDM RX clock divider can help
    // The XIAO ESP32-S3 Sense has a built-in mic with fixed gain, but we can optimize I2S settings
    
    Serial.println("I2S microphone initialized successfully");
    Serial.printf("Sample rate: %d Hz, %d-bit, mono\n", SAMPLE_RATE, BITS_PER_SAMPLE);
    Serial.printf("Data rate: ~%d KB/s\n", (SAMPLE_RATE * BITS_PER_SAMPLE) / 8000);
    Serial.println("APLL enabled for improved clock stability");
}

void startRecording() {
    Serial.println("\n🔴 Recording started by server");

    // Reset buffer
    recordingBufferSize = 0;

    // Reset audio quality metrics
    audioMetrics.avgDbLevel = 0.0;
    audioMetrics.maxDbLevel = -100.0;
    audioMetrics.minDbLevel = 0.0;
    audioMetrics.clipCount = 0;
    audioMetrics.silenceChunks = 0;
    audioMetrics.i2sErrors = 0;
    audioMetrics.totalChunks = 0;

    // Clear I2S buffer to avoid stale data
    size_t bytes_read;
    i2s_read(I2S_PORT, audioBuffer, sizeof(audioBuffer), &bytes_read, 0);
}

void stopRecordingAndUpload() {
    Serial.println("\n⏹️  Recording stopped by server");

    Serial.printf("Captured %d bytes (%.2f seconds)\n",
                  recordingBufferSize,
                  (float)recordingBufferSize / (SAMPLE_RATE * 2));

    if (recordingBufferSize > 0) {
        Serial.println("Uploading to server...");
        bool success = uploadRecording();
        if (success) {
            Serial.println("✓ Upload successful");
        } else {
            Serial.println("✗ Upload failed");
        }
    } else {
        Serial.println("⚠️  No audio data captured");
    }
}

void captureAudioChunk() {
    // Check if buffer is full - stop recording if we've reached max capacity
    if (recordingBufferSize >= recordingBufferCapacity) {
        Serial.println("⚠️  Recording buffer full - stopping recording");
        recordingActive = false;
        // Trigger stop and upload
        stopRecordingAndUpload();
        wasRecording = false;
        return;
    }

    // Calculate how much we can read - ensure we don't exceed buffer capacity
    size_t spaceAvailable = recordingBufferCapacity - recordingBufferSize;
    if (spaceAvailable == 0) {
        return;  // No space available
    }
    
    size_t bytesToRead = min(sizeof(audioBuffer), spaceAvailable);

    // Read from I2S with timeout to prevent blocking indefinitely
    esp_err_t result = i2s_read(I2S_PORT, audioBuffer, bytesToRead, &bytesRead, portMAX_DELAY);

    if (result == ESP_OK && bytesRead > 0) {
        // Ensure we don't exceed buffer capacity (safety check)
        if (recordingBufferSize + bytesRead > recordingBufferCapacity) {
            bytesRead = recordingBufferCapacity - recordingBufferSize;
        }
        
        // Calculate audio level for monitoring (RMS calculation)
        int16_t* samples = (int16_t*)audioBuffer;
        int numSamples = bytesRead / sizeof(int16_t);
        long sumSquares = 0;
        int clipSamples = 0;
        
        for (int i = 0; i < numSamples; i++) {
            long sample = (long)samples[i];
            sumSquares += sample * sample;
            
            // Detect clipping (samples near max/min values)
            if (sample > 30000 || sample < -30000) {
                clipSamples++;
            }
        }
        
        float rms = sqrt((float)sumSquares / numSamples);
        
        // Calculate dB level safely (avoid log10 of 0 or negative)
        float dbLevel = -100.0;  // Default to very quiet
        if (rms > 0.0 && rms <= 32768.0) {
            float ratio = rms / 32768.0;
            if (ratio > 0.0) {
                dbLevel = 20.0 * log10(ratio);
            }
        } else if (rms > 32768.0) {
            // Clipping detected
            dbLevel = 0.0;  // At maximum
        }
        
        // Update audio quality metrics (only if dbLevel is valid)
        // Check for NaN/Inf using comparison (ESP32 may not have isnan/isinf)
        bool isValidDb = (dbLevel == dbLevel) && (dbLevel >= -200.0) && (dbLevel <= 100.0);
        
        if (isValidDb) {
            audioMetrics.totalChunks++;
            
            // Initialize avgDbLevel on first valid chunk
            if (audioMetrics.totalChunks == 1) {
                audioMetrics.avgDbLevel = dbLevel;
            } else {
                // Running average calculation
                audioMetrics.avgDbLevel = (audioMetrics.avgDbLevel * (audioMetrics.totalChunks - 1) + dbLevel) / audioMetrics.totalChunks;
            }
            
            // Ensure avgDbLevel is valid (NaN check: NaN != NaN is true)
            if (audioMetrics.avgDbLevel != audioMetrics.avgDbLevel || 
                audioMetrics.avgDbLevel < -200.0 || 
                audioMetrics.avgDbLevel > 100.0) {
                audioMetrics.avgDbLevel = dbLevel;  // Reset to current value
            }
        }
        
        if (dbLevel > audioMetrics.maxDbLevel) {
            audioMetrics.maxDbLevel = dbLevel;
        }
        // Update min level (only if current is valid and less than previous, or if min is uninitialized)
        bool isValidMin = (dbLevel == dbLevel) && (dbLevel >= -200.0) && (dbLevel <= 100.0);
        if (isValidMin && (audioMetrics.minDbLevel == 0.0 || dbLevel < audioMetrics.minDbLevel)) {
            audioMetrics.minDbLevel = dbLevel;
        }
        
        // Detect clipping (more than 1% of samples clipped)
        if (clipSamples > (numSamples / 100)) {
            audioMetrics.clipCount++;
        }
        
        // Detect silence
        if (dbLevel < audioMetrics.silenceThreshold) {
            audioMetrics.silenceChunks++;
        }
        
        // Copy to recording buffer
        if (bytesRead > 0) {
            memcpy(recordingBuffer + recordingBufferSize, audioBuffer, bytesRead);
            recordingBufferSize += bytesRead;
        }

        // Print progress every second with audio level monitoring
        static unsigned long lastProgressPrint = 0;
        unsigned long now = millis();
        if (now - lastProgressPrint >= 1000) {  // Print every 1 second
            float seconds = (float)recordingBufferSize / (SAMPLE_RATE * 2);
            float bufferPercent = (float)recordingBufferSize / recordingBufferCapacity * 100.0;
            Serial.printf("🔴 Recording... %.1fs (%.1f%% buffer, %d KB, %.1f dB)\n", 
                         seconds, bufferPercent, recordingBufferSize / 1024, dbLevel);
            lastProgressPrint = now;
        }
    } else if (result != ESP_OK) {
        audioMetrics.i2sErrors++;
        Serial.printf("⚠️  I2S read error: %d (total errors: %d)\n", result, audioMetrics.i2sErrors);
        // Don't stop recording on single I2S error, but log it
    }
}

bool uploadRecording() {
    if (recordingBufferSize == 0) {
        return false;
    }

    // Store buffer size before upload (in case it changes during upload)
    size_t bufferSizeToUpload = recordingBufferSize;

    HTTPClient http;

    // Add device ID and audio metadata to URL
    String url = String(SERVER_URL) +
                 "?device=" + deviceId +
                 "&rate=" + SAMPLE_RATE +
                 "&bits=" + BITS_PER_SAMPLE +
                 "&channels=" + CHANNELS;

    http.begin(url);
    http.addHeader("Content-Type", "application/octet-stream");
    http.addHeader("X-Audio-Format", "pcm");
    http.addHeader("X-Sample-Rate", String(SAMPLE_RATE));
    http.addHeader("X-Bits-Per-Sample", String(BITS_PER_SAMPLE));
    http.addHeader("X-Channels", String(CHANNELS));
    
    // Add audio quality metrics as headers (only if valid)
    // Use NaN check: NaN != NaN is true
    if (audioMetrics.avgDbLevel == audioMetrics.avgDbLevel && 
        audioMetrics.avgDbLevel >= -200.0 && audioMetrics.avgDbLevel <= 100.0) {
        http.addHeader("X-Audio-AvgDb", String(audioMetrics.avgDbLevel, 1));
    }
    if (audioMetrics.maxDbLevel == audioMetrics.maxDbLevel && 
        audioMetrics.maxDbLevel >= -200.0 && audioMetrics.maxDbLevel <= 100.0) {
        http.addHeader("X-Audio-MaxDb", String(audioMetrics.maxDbLevel, 1));
    }
    if (audioMetrics.minDbLevel == audioMetrics.minDbLevel && 
        audioMetrics.minDbLevel >= -200.0 && audioMetrics.minDbLevel <= 100.0 && 
        audioMetrics.minDbLevel != 0.0) {
        http.addHeader("X-Audio-MinDb", String(audioMetrics.minDbLevel, 1));
    }
    http.addHeader("X-Audio-ClipCount", String(audioMetrics.clipCount));
    http.addHeader("X-Audio-SilenceChunks", String(audioMetrics.silenceChunks));
    http.addHeader("X-Audio-I2SErrors", String(audioMetrics.i2sErrors));
    http.addHeader("X-Audio-TotalChunks", String(audioMetrics.totalChunks));
    
    // Calculate timeout based on data size (at least 30s, more for larger files)
    // Assume upload speed of ~100KB/s minimum
    unsigned long calculatedTimeout = (unsigned long)((bufferSizeToUpload / 1024) * 100);
    unsigned long timeoutMs = (30000UL > calculatedTimeout) ? 30000UL : calculatedTimeout;
    http.setTimeout(timeoutMs);

    float duration = (float)bufferSizeToUpload / (SAMPLE_RATE * 2);
    Serial.printf("Uploading %d bytes (%.2f seconds, timeout: %lu ms)...\n", 
                 bufferSizeToUpload, duration, timeoutMs);

    unsigned long uploadStart = millis();
    int httpCode = http.POST(recordingBuffer, bufferSizeToUpload);
    unsigned long uploadDuration = millis() - uploadStart;

    bool success = (httpCode == 200 || httpCode == 204);

    if (!success) {
        char context[256];
        if (httpCode < 0) {
            // Error code (timeout, connection failure, etc.)
            snprintf(context, sizeof(context), "Device: %s, Size: %d bytes (%.2fs), Timeout: %lums, Duration: %lums", 
                     deviceId.c_str(), bufferSizeToUpload, duration, timeoutMs, uploadDuration);
        } else {
            // HTTP error code
            snprintf(context, sizeof(context), "Device: %s, Size: %d bytes (%.2fs), HTTP %d", 
                     deviceId.c_str(), bufferSizeToUpload, duration, httpCode);
        }
        logHttpError("Audio upload", httpCode, context);
        if (httpCode > 0) {
            String response = http.getString();
            if (response.length() > 0) {
                Serial.printf("  Server response: %s\n", response.c_str());
            }
        }
    } else {
        float uploadSpeed = (float)bufferSizeToUpload / (uploadDuration / 1000.0) / 1024.0;  // KB/s
        Serial.printf("✓ Audio upload successful: HTTP %d, %d bytes in %lu ms (%.1f KB/s)\n", 
                     httpCode, bufferSizeToUpload, uploadDuration, uploadSpeed);
    }

    http.end();
    return success;
}

bool checkRecordingStatus() {
    HTTPClient http;
    String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + "/status?device=" + deviceId;

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

            // Success - reset failure counter
            statusCheckFailures = 0;
            
            if (trueIdx > recordingIdx && (falseIdx < 0 || trueIdx < falseIdx)) {
                lastKnownRecordingState = true;
                return true;
            } else {
                lastKnownRecordingState = false;
                return false;
            }
        }
        
        // If we got 200 but couldn't parse, treat as success but unknown state
        statusCheckFailures = 0;
        http.end();
        return lastKnownRecordingState;  // Return last known state
    } else {
        // Error or non-200 HTTP response
        statusCheckFailures++;
        
        char context[128];
        if (httpCode < 0) {
            // Error code (timeout, connection failure, etc.)
            snprintf(context, sizeof(context), "Device: %s, Timeout: 1s, Failures: %d/%d", 
                     deviceId.c_str(), statusCheckFailures, MAX_CONSECUTIVE_FAILURES);
        } else {
            // HTTP error code
            snprintf(context, sizeof(context), "Device: %s, HTTP %d, Failures: %d/%d", 
                     deviceId.c_str(), httpCode, statusCheckFailures, MAX_CONSECUTIVE_FAILURES);
        }
        
        // Only log if we're getting close to max failures or it's a new failure
        if (statusCheckFailures == 1 || statusCheckFailures >= MAX_CONSECUTIVE_FAILURES - 1) {
            logHttpError("Status check", httpCode, context);
        }
        
        http.end();
        
        // Return last known state on failure (don't change state on single failure)
        return lastKnownRecordingState;
    }
}

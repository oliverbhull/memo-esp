# ESP32-S3 WiFi Audio Streamer

WiFi-based audio capture device that streams voice data from the Seeed XIAO ESP32-S3 Sense built-in microphone to an HTTP endpoint.

## Hardware

- **Board**: Seeed Studio XIAO ESP32-S3 Sense
- **Microphone**: Built-in PDM microphone (no external wiring needed)
- **Connection**: USB-C for power and programming

## Audio Specifications

- **Sample Rate**: 16 kHz
- **Bit Depth**: 16-bit
- **Channels**: Mono
- **Format**: Raw PCM
- **Data Rate**: ~32 KB/second
- **Chunk Size**: 1 second (configurable)

## Quick Start

### 1. Install Dependencies

Install VS Code and PlatformIO:
```bash
# Install PlatformIO Core (if not using VS Code extension)
pip install platformio
```

### 2. Configure WiFi and Server

Edit `include/config.h`:
```cpp
#define WIFI_SSID "YourNetworkName"
#define WIFI_PASSWORD "YourPassword"
#define SERVER_URL "http://YOUR_SERVER_IP:8000/audio"
```

Currently configured for:
- **WiFi**: Founders Guest / artifact1!
- **Server**: http://192.168.1.100:8000/audio (update with your IP)

### 3. Build and Upload

```bash
# Build firmware
pio run

# Upload to board (connect via USB-C)
pio run --target upload

# Monitor serial output
pio device monitor
```

Or use VS Code PlatformIO extension buttons.

### 4. Run Test Server

Start the audio receiver on your computer:

```bash
python3 test_server.py
```

The server will:
- Listen on `http://0.0.0.0:8000/audio`
- Save received audio to `received_audio/` directory
- Save both raw PCM and WAV formats
- Print metadata for each chunk received

## File Structure

```
memo-esp/
├── platformio.ini          # PlatformIO configuration
├── include/
│   └── config.h            # WiFi, audio, and server settings
├── src/
│   └── main.cpp            # Main firmware code
├── test_server.py          # Python test server
└── README.md               # This file
```

## How It Works

1. **Initialization**
   - Connects to WiFi network
   - Initializes I2S interface in PDM mode
   - Configures built-in microphone

2. **Audio Capture**
   - Reads audio samples from PDM mic via I2S
   - Buffers samples (default: 1 second chunks)
   - Each chunk is 32 KB of raw PCM data

3. **HTTP Streaming**
   - POSTs audio chunks to configured endpoint
   - Includes metadata in headers and URL params
   - Handles reconnection if WiFi drops

4. **Server Processing**
   - Receives raw PCM data
   - Converts to WAV format (if using test server)
   - Can be piped to transcription services (Whisper, etc.)

## Pin Configuration (XIAO ESP32-S3 Sense)

The built-in PDM microphone uses:
- **I2S_WS (LRCLK)**: GPIO 42
- **I2S_SD (DOUT)**: GPIO 41
- **SCK**: Not used in PDM mode

These pins are hardwired to the built-in mic - no external connections needed.

## Troubleshooting

### WiFi won't connect
- Check SSID and password in `config.h`
- Verify network is 2.4GHz (ESP32 doesn't support 5GHz)
- Check serial monitor for connection status

### No audio received
- Update `SERVER_URL` in `config.h` with your computer's IP
- Make sure test server is running
- Check firewall isn't blocking port 8000
- Verify board is on same network as server

### Audio quality issues
- Check sample rate matches on both ends (16kHz)
- Verify I2S pins are correct for your board
- Try different DMA buffer settings in `setupI2S()`

### Build errors
- Make sure PlatformIO is installed
- Try `pio lib install` to fetch dependencies
- Check board definition is correct

## Customization

### Change chunk duration
In `config.h`:
```cpp
#define CHUNK_DURATION_MS 2000  // 2 second chunks
```

### Change sample rate
In `config.h`:
```cpp
#define SAMPLE_RATE 8000  // 8kHz for lower bandwidth
```

### Add audio compression
Consider adding Opus encoding before transmission to reduce bandwidth by ~10x.

### Add device authentication
Implement token-based auth in HTTP headers to identify/authenticate devices.

## Next Steps

- [ ] Test end-to-end streaming with test server
- [ ] Integrate with existing transcription pipeline (Whisper)
- [ ] Add Opus compression for bandwidth efficiency
- [ ] Implement device authentication mechanism
- [ ] Add button trigger for on-demand recording
- [ ] Battery level monitoring (if using battery power)
- [ ] OTA firmware updates over WiFi

## Comparison: WiFi vs BLE

| Feature | WiFi (this project) | BLE (Memo) |
|---------|---------------------|------------|
| Power consumption | Higher (~80mA active) | Lower (~15mA) |
| Range | 50-100m (depends on router) | ~10m from phone |
| Bandwidth | High (raw PCM streaming) | Limited (compressed) |
| Latency | Low (~100ms) | Higher (~200-500ms) |
| Setup complexity | Simple (WiFi credentials) | More complex (pairing) |
| Best for | Dev/test, stationary use | Wearable, mobile use |

## License

MIT

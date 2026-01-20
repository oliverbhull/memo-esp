# ESP32 USB Connection Troubleshooting

## Current Situation
The XIAO ESP32-S3 is not appearing as a USB device on your Mac. The device shows only:
- `/dev/cu.OBHJBL` (not the ESP32)
- `/dev/cu.Bluetooth-Incoming-Port`
- `/dev/cu.debug-console`

**Expected device:** `/dev/cu.usbmodem####` (where #### is a number)

---

## Why This Matters (And Why It Doesn't Right Now)

### You DON'T need USB for:
✅ **Running the system** - ESP32 connects via WiFi
✅ **Recording audio** - Works over network
✅ **Viewing transcripts** - Web UI at http://10.104.16.88:8000
✅ **Normal operation** - Everything works wirelessly

### You DO need USB for:
❌ **Uploading new firmware** - Can't flash without USB
❌ **Debugging via Serial Monitor** - Can't see debug output
❌ **Initial WiFi setup** - (Already done)

---

## Root Cause Analysis

The ESP32 isn't appearing because macOS isn't detecting it as a USB device. This is almost always one of these issues:

### 1. **USB Cable (Most Common - 80%)**
Many USB-C cables are **power-only** and don't have data lines:
- ✅ **Data cables:** Have 4+ wires inside (power + data)
- ❌ **Charging cables:** Have only 2 wires (power only)

**How to test:**
- Try a cable that you KNOW works for data transfer
- Try the cable that came with the device
- Try a cable you use to transfer files from your phone

### 2. **USB Port Issue (10%)**
Some USB ports or hubs don't work well:
- Try plugging directly into Mac (not through hub)
- Try different USB ports on your Mac
- Some USB-C ports on Macs have better compatibility than others

### 3. **Driver/Bootloader Issue (5%)**
The ESP32 might be in the wrong mode:
- **Try:** Hold BOOT button while plugging in USB
- **Try:** Hold BOOT, press RESET, release both
- **Try:** Restart your Mac (sometimes USB controller needs reset)

### 4. **Hardware Fault (5%)**
Less common but possible:
- USB port on ESP32 might be damaged
- Solder joints might be broken
- Board might have manufacturing defect

---

## Diagnostic Steps

Run the diagnostic script:
```bash
./check_usb.sh
```

Or manually check:

### Step 1: Check before plugging in
```bash
ls /dev/cu.* > /tmp/before.txt
```

### Step 2: Plug in ESP32

### Step 3: Check after plugging in
```bash
ls /dev/cu.* > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

**Expected output:** A new device like `/dev/cu.usbmodem3101`
**Your output:** Nothing new appears

---

## What OBHJBL Might Be

`/dev/cu.OBHJBL` is showing up but it's not clear what device this is. It could be:
- A Bluetooth device
- Another USB device
- Not related to the ESP32

---

## Current Workaround

Since your ESP32 already has the firmware and WiFi credentials, you can:

1. **Power the ESP32 via USB** (even with power-only cable)
2. **Run the server:** `python3 transcription_server.py`
3. **Use the web UI:** http://10.104.16.88:8000
4. **Press SPACE** to start/stop recording
5. **LED will turn red** when recording

The system works entirely over WiFi - USB was only needed for initial setup!

---

## Next Steps

1. **Test with different USB cables** (most likely fix)
2. **If you get a data cable working:**
   - Upload the latest firmware with LED support
   - Upload firmware with 250ms chunks (faster response)
   - Upload firmware with 200ms status polling

3. **If you can't get USB working:**
   - System still works fine over WiFi
   - You won't be able to update firmware easily
   - Consider getting a USB-UART adapter as backup

---

## Known Working Configuration

Last time USB worked, the device appeared as:
- **Port:** `/dev/cu.usbmodem3101`
- **Upload command:** `pio run --target upload`
- **Monitor command:** `pio device monitor`

The ESP32 was successfully programmed with:
- WiFi credentials for "Founders Guest"
- Server IP: 10.104.16.88
- Status polling every 1 second (old firmware)
- 1-second audio chunks (old firmware)

---

## For Future Reference

When you get a proper data cable, the latest firmware includes:
- ✨ LED control (red when recording, green when ready, blue at startup)
- ⚡ 250ms audio chunks (4x more responsive)
- 🔄 200ms status polling (5x faster response to start/stop)
- 📦 2-second grace period (catches all in-flight audio)

But for now, the system works great as-is!

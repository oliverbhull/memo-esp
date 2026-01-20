#!/bin/bash
# Script to fix ESP32-S3 USB enumeration issues

echo "=========================================="
echo "ESP32-S3 USB Enumeration Fix"
echo "=========================================="
echo

echo "Step 1: Unplug the ESP32 from USB"
read -p "Press Enter after you've unplugged it..."

echo
echo "Step 2: Waiting 3 seconds for USB subsystem to reset..."
sleep 3

echo
echo "Step 3: Checking current devices..."
ls -la /dev/cu.* 2>/dev/null | grep -v "Bluetooth\|debug-console" || echo "No USB devices found (expected)"

echo
echo "Step 4: Now plug in the ESP32"
echo "   IMPORTANT: While plugging in, HOLD the BOOT button"
echo "   Keep holding BOOT for 2-3 seconds after plugging in"
read -p "Press Enter after you've plugged it in while holding BOOT..."

echo
echo "Step 5: Waiting 2 seconds for enumeration..."
sleep 2

echo
echo "Step 6: Checking for ESP32 device..."
NEW_DEVICE=$(ls /dev/cu.* 2>/dev/null | grep -E "usbmodem|usbserial" | head -1)

if [ -n "$NEW_DEVICE" ]; then
    echo "✅ SUCCESS! Found device: $NEW_DEVICE"
    echo
    echo "Verifying with PlatformIO..."
    pio device list | grep -A 3 "$NEW_DEVICE"
else
    echo "❌ No ESP32 device found"
    echo
    echo "All current devices:"
    ls -la /dev/cu.* 2>/dev/null
    echo
    echo "Troubleshooting steps:"
    echo "1. Try a different USB port on your Mac"
    echo "2. Try unplugging and replugging (without holding BOOT this time)"
    echo "3. Try holding BOOT, pressing RESET, then releasing both"
    echo "4. Restart your Mac (USB controller reset)"
fi

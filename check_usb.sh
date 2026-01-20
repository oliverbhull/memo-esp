#!/bin/bash
# USB Device Diagnostic Script for ESP32

echo "=========================================="
echo "ESP32 USB Device Diagnostic"
echo "=========================================="
echo

echo "1. Checking for USB serial ports..."
echo "---"
ls -la /dev/cu.* 2>/dev/null | grep -v "Bluetooth\|debug-console\|OBHJBL" || echo "No USB serial devices found"
echo

echo "2. Checking with PlatformIO..."
echo "---"
pio device list | grep -v "Bluetooth\|debug-console\|OBHJBL" || echo "No devices detected by PlatformIO"
echo

echo "3. Checking USB system information..."
echo "---"
system_profiler SPUSBDataType 2>/dev/null | grep -B 3 -A 10 "Vendor ID: 0x1a86\|Vendor ID: 0x303a\|Vendor ID: 0x10c4\|ESP32\|XIAO\|CP210\|CH340" | head -30 || echo "No ESP32/USB-Serial devices in system"
echo

echo "=========================================="
echo "What to check:"
echo "=========================================="
echo "1. Cable: Use a USB-C cable that supports DATA (not power-only)"
echo "2. Port: Try different USB ports on your Mac"
echo "3. Reset: While plugging in, hold BOOT button on ESP32"
echo "4. LED: Check if any LED lights up when plugged in"
echo
echo "Expected device name: /dev/cu.usbmodem#### or /dev/cu.usbserial-####"
echo

# Check if a device appeared recently
echo "5. Checking system logs for recent USB events..."
echo "---"
log show --predicate 'eventMessage contains "USB"' --last 1m --info 2>/dev/null | grep -i "attach\|detach\|connect" | tail -5 || echo "No recent USB events"

#!/bin/bash
# Quick test runner for mqtt_debugger trigger tests
#
# This script sets up and runs the comprehensive test suite for the modbus
# trigger functionality in mqtt_debugger.py

set -e

VENV_PATH="/tmp/mqtt_debugger_venv"
PROJECT_PATH="/srv/bluetti_mqtt_ep2000"

echo "=========================================="
echo "MQTT Debugger Trigger Tests"
echo "=========================================="
echo ""

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating virtual environment at $VENV_PATH..."
    python3 -m venv "$VENV_PATH"
fi

echo "Activating virtual environment..."
source "$VENV_PATH/bin/activate"

echo "Installing dependencies..."
pip install -q paho-mqtt bleak crcmod dbus-next pycryptodome

echo ""
echo "Running tests..."
echo "=========================================="
cd "$PROJECT_PATH"
python -m unittest tests.test_mqtt_debugger_triggers -v

echo ""
echo "=========================================="
echo "Test run complete!"
echo "=========================================="

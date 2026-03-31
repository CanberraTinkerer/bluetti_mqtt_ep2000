#!/bin/bash
# Integration test runner for mqtt_debugger with real Bluetti devices
#
# This script helps you discover devices and run integration tests

set -e

VENV_PATH="/tmp/mqtt_integration_venv"
PROJECT_PATH="/srv/bluetti_mqtt_ep2000"

function print_header() {
    echo ""
    echo "======================================================================"
    echo "$1"
    echo "======================================================================"
    echo ""
}

function check_environment() {
    print_header "Environment Setup"
    
    if [ ! -d "$VENV_PATH" ]; then
        echo "📦 Creating virtual environment..."
        python3 -m venv "$VENV_PATH"
    fi
    
    echo "📂 Activating virtual environment..."
    source "$VENV_PATH/bin/activate"
    
    echo "📥 Installing dependencies..."
    pip install -q paho-mqtt bleak crcmod dbus-next pycryptodome
    
    cd "$PROJECT_PATH"
    echo "✓ Environment ready"
}

function scan_devices() {
    print_header "Scanning for Bluetti Devices"
    
    echo "⏳ Scanning... (this may take 30-60 seconds)"
    echo ""
    
    python -m bluetti_mqtt.discovery_cli --scan 2>/dev/null || {
        echo "⚠️  Discovery CLI not available. Using manual discovery..."
        echo ""
        echo "On Linux, use bluetoothctl:"
        echo "  sudo bluetoothctl"
        echo "  scan on"
        echo ""
        echo "On macOS, use System Preferences > Bluetooth"
        echo ""
        return
    }
}

function run_tests() {
    local device_address="$1"
    
    if [ -z "$device_address" ]; then
        print_header "Running Tests (Device Not Specified - Tests Will Be Skipped)"
        python -m unittest tests.test_mqtt_debugger_integration -v
    else
        print_header "Running Integration Tests with Device: $device_address"
        
        export BLUETTI_DEVICE_ADDRESS="$device_address"
        echo "📱 Device: $BLUETTI_DEVICE_ADDRESS"
        echo ""
        
        python -m unittest tests.test_mqtt_debugger_integration -v
    fi
}

function run_unit_tests() {
    print_header "Running Unit Tests (No Device Required)"
    
    python -m unittest tests.test_mqtt_debugger_triggers -v
}

function run_all_tests() {
    local device_address="$1"
    
    echo ""
    echo "Running all tests (unit + integration)..."
    echo ""
    
    run_unit_tests
    
    if [ -n "$device_address" ]; then
        run_tests "$device_address"
    else
        echo ""
        echo "⚠️  Integration tests skipped (no device address)"
        echo "   To run with device: $0 --device XX:XX:XX:XX:XX:XX"
    fi
}

function show_help() {
    cat << EOF
MQTT Debugger Integration Test Runner

Usage: $0 [COMMAND] [OPTIONS]

Commands:
    --scan              Scan for Bluetti devices
    --device ADDR       Run tests with specific device (XX:XX:XX:XX:XX:XX)
    --unit              Run only unit tests (no device needed)
    --integration ADDR  Run only integration tests with device
    --all [ADDR]        Run all tests (unit + integration)
    --help              Show this help message

Examples:
    # Scan for devices
    $0 --scan

    # Run integration tests with a specific device
    $0 --device AA:BB:CC:DD:EE:FF

    # Run only unit tests
    $0 --unit

    # Run all tests with device
    $0 --all AA:BB:CC:DD:EE:FF

    # Run all tests without device (integration skipped)
    $0 --all

EOF
}

# Main logic
check_environment

if [ $# -eq 0 ]; then
    show_help
    exit 1
fi

case "$1" in
    --scan)
        scan_devices
        ;;
    --device)
        if [ -z "$2" ]; then
            echo "❌ Device address required"
            echo "Usage: $0 --device XX:XX:XX:XX:XX:XX"
            exit 1
        fi
        run_tests "$2"
        ;;
    --unit)
        run_unit_tests
        ;;
    --integration)
        if [ -z "$2" ]; then
            echo "❌ Device address required"
            echo "Usage: $0 --integration XX:XX:XX:XX:XX:XX"
            exit 1
        fi
        run_tests "$2"
        ;;
    --all)
        run_all_tests "$2"
        ;;
    --help)
        show_help
        ;;
    *)
        echo "❌ Unknown command: $1"
        show_help
        exit 1
        ;;
esac

echo ""
echo "✓ Test run complete"
echo ""

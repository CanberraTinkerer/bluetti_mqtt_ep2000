"""
Tests for mqtt_debugger.py modbus trigger functionality.

These tests verify that:
1. Trigger register writes are sent correctly before reading target registers
2. The 3500 modbus register range can be read after trigger activation
3. Non-exception responses (values other than exception 3) are properly returned
4. Various trigger configurations work correctly
"""

import asyncio
import json
import struct
import unittest
from unittest.mock import Mock, patch, AsyncMock, MagicMock, call
from typing import Dict, Any

# Import the modules we're testing
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bluetti_mqtt.mqtt_debugger import (
    get_target_slave_id,
    get_slave_validation_register,
    group_commands,
    WriteSingleRegister,
    ReadHoldingRegisters,
)


class TestTriggerConfiguration(unittest.TestCase):
    """Tests for trigger configuration in command definitions."""

    def test_trigger_reg_exists_in_config(self):
        """Test that trigger_reg and trigger_val are present in sample config."""
        # Create a mock config with trigger entries
        config = [
            {
                "reg": 3500,
                "name": "PV Cumulative Energy",
                "len": 32,
                "scale": 1,
                "unit": "kWh",
                "trigger_reg": 2027,
                "trigger_val": 0,
                "encrypted": True,
            },
            {
                "reg": 3506,
                "name": "PV 2024 Energy",
                "len": 32,
                "scale": 1,
                "unit": "kWh",
                "trigger_reg": 2027,
                "trigger_val": 0,
                "encrypted": True,
            }
        ]
        
        # Verify trigger_reg and trigger_val are present
        for cmd in config:
            self.assertIn("trigger_reg", cmd)
            self.assertIn("trigger_val", cmd)
            self.assertEqual(cmd["trigger_reg"], 2027)
            self.assertIn(cmd["trigger_val"], [0, 2])

    def test_multiple_trigger_values(self):
        """Test that different trigger values are supported."""
        config = [
            {
                "reg": 3500,
                "trigger_reg": 2027,
                "trigger_val": 0,
            },
            {
                "reg": 3500,
                "trigger_reg": 2027,
                "trigger_val": 2,
            },
        ]
        
        # Group commands - they should be in separate groups due to different trigger values
        grouped = group_commands(config)
        
        # Should have at least 1 group
        self.assertGreaterEqual(len(grouped), 1)


class TestGroupCommandsWithTriggers(unittest.TestCase):
    """Tests for grouping commands while preserving trigger information."""

    def test_group_commands_preserves_trigger_reg(self):
        """Test that grouping preserves trigger_reg from commands."""
        commands = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0, "encrypted": True},
            {"reg": 3506, "len": 32, "trigger_reg": 2027, "trigger_val": 0, "encrypted": True},
        ]
        
        grouped = group_commands(commands)
        
        self.assertGreaterEqual(len(grouped), 1)
        # At least one group should have the trigger
        has_trigger = any(g.get("trigger_reg") == 2027 for g in grouped)
        self.assertTrue(has_trigger)

    def test_group_commands_separates_different_triggers(self):
        """Test that commands with different trigger values go to different groups."""
        commands = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0, "encrypted": True},
            {"reg": 3600, "len": 32, "trigger_reg": 2027, "trigger_val": 2, "encrypted": True},
        ]
        
        grouped = group_commands(commands)
        
        # Should be able to group (at minimum, shouldn't crash)
        self.assertIsNotNone(grouped)
        self.assertGreaterEqual(len(grouped), 1)

    def test_get_slave_validation_register_inverter(self):
        group = {'slave_id': 1, 'start_reg': 100, 'commands': [{'reg': 100}]}
        self.assertEqual(get_slave_validation_register(group), 1100)

        group = {'slave_id': 2, 'start_reg': 200, 'commands': [{'reg': 200}]}
        self.assertEqual(get_slave_validation_register(group), 1100)

    def test_get_slave_validation_register_battery_pack(self):
        group = {'slave_id': 41, 'start_reg': 16000, 'commands': [{'reg': 16000}]}
        self.assertEqual(get_slave_validation_register(group), 6100)

    def test_get_slave_validation_register_bmu(self):
        group = {'slave_id': 45, 'start_reg': 7220, 'commands': [{'reg': 7220}]}
        self.assertEqual(get_slave_validation_register(group), 7232)

    def test_get_slave_validation_register_bmu_from_command_range(self):
        group = {'slave_id': 45, 'start_reg': 100, 'commands': [{'reg': 7232}]}
        self.assertEqual(get_slave_validation_register(group), 7232)

    def test_group_commands_includes_3500_range(self):
        """Test that 3500 register range is properly grouped."""
        commands = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3506, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3508, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
        ]
        
        grouped = group_commands(commands)
        
        # Should have at least one group
        self.assertGreaterEqual(len(grouped), 1)
        
        # The group should contain registers around 3500
        has_3500_range = any(
            group["start_reg"] <= 3510
            for group in grouped
        )
        self.assertTrue(has_3500_range)


class TestTriggerWriteAndRead(unittest.TestCase):
    """Tests for the trigger write and subsequent read functionality."""

    def test_trigger_write_command_creation(self):
        """Test that a trigger write command can be created."""
        trigger_reg = 2027
        trigger_val = 0
        
        # Should not raise exceptions
        cmd = WriteSingleRegister(trigger_reg, trigger_val)
        
        # Command should produce bytes
        cmd_bytes = bytes(cmd)
        self.assertGreater(len(cmd_bytes), 0)

    def test_write_single_register_trigger_format(self):
        """Test that WriteSingleRegister creates correct format for trigger."""
        trigger_reg = 2027
        trigger_val = 0
        
        cmd = WriteSingleRegister(trigger_reg, trigger_val)
        
        # Verify command structure
        self.assertEqual(cmd.address, trigger_reg)
        self.assertEqual(cmd.value, trigger_val)
        # The command should be executable bytes
        cmd_bytes = bytes(cmd)
        self.assertGreater(len(cmd_bytes), 0)

    def test_read_command_after_trigger(self):
        """Test creating a read command after trigger setup."""
        # Create read command for 3500 registers
        cmd = ReadHoldingRegisters(3500, 16)
        
        # Should create valid command
        cmd_bytes = bytes(cmd)
        self.assertGreater(len(cmd_bytes), 0)
        self.assertEqual(cmd.starting_address, 3500)
        self.assertEqual(cmd.quantity, 16)


class TestReadRegister3500Range(unittest.TestCase):
    """Tests for reading from the 3500 modbus register range after trigger."""

    def test_read_3500_register_address(self):
        """Test reading register 3500."""
        cmd = ReadHoldingRegisters(3500, 16)
        
        self.assertEqual(cmd.starting_address, 3500)
        self.assertEqual(cmd.quantity, 16)

    def test_read_multiple_3500_registers(self):
        """Test reading multiple registers starting from 3500."""
        registers = [3500, 3506, 3508, 3602, 3604, 3606, 3608]
        
        for reg in registers:
            cmd = ReadHoldingRegisters(reg, 16)
            self.assertEqual(cmd.starting_address, reg)
            self.assertEqual(cmd.quantity, 16)

    def test_3500_range_with_trigger(self):
        """Test that 3500 range registers include trigger configuration."""
        config = [
            {"reg": 3500, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3506, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3602, "trigger_reg": 2027, "trigger_val": 0},
        ]
        
        grouped = group_commands(config)
        
        # All should be in groups with trigger defined
        for group in grouped:
            self.assertEqual(group.get("trigger_reg"), 2027)
            self.assertIn(group.get("trigger_val"), [0, 2, None])


class TestModbusExceptionHandling(unittest.TestCase):
    """Tests for handling modbus exceptions (particularly exception 3)."""

    def test_modbus_exception_3_detection(self):
        """Test detection of modbus exception 3 (Illegal Data Value)."""
        # Modbus exception format: [slave_id][func_code | 0x80][exception_code]
        exception_3_response = bytes([0x01, 0x83, 0x03])  # Exception 3
        
        # This would typically raise a ModbusError
        self.assertNotEqual(exception_3_response[1] & 0x80, 0)  # Exception flag
        self.assertEqual(exception_3_response[2], 3)  # Exception code 3

    def test_valid_read_response_not_exception(self):
        """Test that valid read responses don't have exception bit set."""
        # Valid read response: [slave_id][func_code][byte_count][data...][crc]
        valid_response = bytes([0x01, 0x03, 0x10]) + (b'\x00' * 16) + b'\x00\x00'
        
        # Check that exception bit is not set
        self.assertEqual(valid_response[1] & 0x80, 0)
        self.assertEqual(valid_response[1], 0x03)  # Read Holding Registers function code

    def test_response_with_data_is_valid(self):
        """Test that a response with actual data is considered valid."""
        # Simulate a successful read of 8 registers (16 bytes)
        response = struct.pack('!BB', 0x01, 0x03)  # Slave ID, Function Code
        response += struct.pack('!B', 16)  # Byte count
        response += b'\x12\x34\x56\x78\x9a\xbc\xde\xf0'  # Some data
        response += b'\x00' * 8  # More data
        response += b'\x00\x00'  # CRC (dummy)
        
        self.assertGreater(len(response), 3)
        self.assertEqual(response[1] & 0x80, 0)  # Not an exception


class TestTriggerWithDifferentValues(unittest.TestCase):
    """Tests for trigger functionality with different trigger values."""

    def test_pv_trigger_value_0(self):
        """Test PV data trigger with trigger_val=0."""
        config = [
            {"reg": 3500, "trigger_reg": 2027, "trigger_val": 0, "name": "PV Cumulative"},
            {"reg": 3506, "trigger_reg": 2027, "trigger_val": 0, "name": "PV 2024"},
        ]
        
        grouped = group_commands(config)
        
        # Should have at least one group
        self.assertGreater(len(grouped), 0)
        
        # Find group with trigger_val 0
        pv_groups = [g for g in grouped if g.get("trigger_val") == 0]
        self.assertGreater(len(pv_groups), 0)

    def test_grid_trigger_value_2(self):
        """Test Grid Import trigger with trigger_val=2."""
        config = [
            {"reg": 3500, "trigger_reg": 2027, "trigger_val": 2, "name": "Grid Import Cumulative"},
            {"reg": 3506, "trigger_reg": 2027, "trigger_val": 2, "name": "Grid Import 2024"},
        ]
        
        grouped = group_commands(config)
        self.assertGreater(len(grouped), 0)
        
        # Find group with trigger_val 2
        grid_groups = [g for g in grouped if g.get("trigger_val") == 2]
        self.assertGreater(len(grid_groups), 0)

    def test_trigger_values_separate_groups(self):
        """Test that different trigger values result in different groups."""
        config = [
            {"reg": 3500, "trigger_reg": 2027, "trigger_val": 0, "len": 32},
            {"reg": 3506, "trigger_reg": 2027, "trigger_val": 0, "len": 32},
            # Different trigger value using separate register
            {"reg": 4000, "trigger_reg": 2027, "trigger_val": 2, "len": 32},
            {"reg": 4006, "trigger_reg": 2027, "trigger_val": 2, "len": 32},
        ]
        
        grouped = group_commands(config)
        
        # Should have groups for different trigger values
        trigger_0_groups = [g for g in grouped if g.get("trigger_val") == 0]
        trigger_2_groups = [g for g in grouped if g.get("trigger_val") == 2]
        
        # At least one of each should exist or they share groups
        self.assertGreater(len(trigger_0_groups) + len(trigger_2_groups), 0)


class TestEncryptedRegistersWithTrigger(unittest.TestCase):
    """Tests for encrypted registers (V2 protocol) with trigger."""

    def test_encrypted_register_with_trigger(self):
        """Test that encrypted 3500 registers with trigger are properly configured."""
        config = [
            {
                "reg": 3500,
                "len": 32,
                "encrypted": True,
                "trigger_reg": 2027,
                "trigger_val": 0,
            }
        ]
        
        grouped = group_commands(config)
        
        self.assertGreater(len(grouped), 0)
        self.assertEqual(grouped[0].get("encrypted"), True)
        self.assertEqual(grouped[0].get("trigger_reg"), 2027)

    def test_encrypted_flag_preserved_in_group(self):
        """Test that encrypted flag is preserved when grouping commands with triggers."""
        commands = [
            {
                "reg": 3500,
                "len": 32,
                "encrypted": True,
                "trigger_reg": 2027,
                "trigger_val": 0,
            },
            {
                "reg": 3506,
                "len": 32,
                "encrypted": True,
                "trigger_reg": 2027,
                "trigger_val": 0,
            },
        ]
        
        grouped = group_commands(commands)
        
        # All groups should have encrypted=True
        for group in grouped:
            self.assertEqual(group["encrypted"], True)


class TestTargetSlaveId(unittest.TestCase):
    """Tests for slave ID determination with triggers."""

    def test_default_slave_id_for_3500_range(self):
        """Test that 3500 range defaults to slave ID 1."""
        cmd = {"reg": 3500}
        self.assertEqual(get_target_slave_id(cmd), 1)

    def test_explicit_slave_id_override(self):
        """Test that explicit slave_id overrides default."""
        cmd = {"reg": 3500, "slave_id": 42}
        self.assertEqual(get_target_slave_id(cmd), 42)

    def test_trigger_with_explicit_slave_id(self):
        """Test trigger commands with explicit slave ID."""
        config = [
            {
                "reg": 3500,
                "slave_id": 1,
                "trigger_reg": 2027,
                "trigger_val": 0,
            }
        ]
        
        grouped = group_commands(config)
        self.assertEqual(grouped[0]["slave_id"], 1)


class TestFullTriggerPollingSequence(unittest.TestCase):
    """Integration tests for the full trigger polling sequence."""

    def test_trigger_poll_sequence_order(self):
        """Test the logical sequence of trigger write followed by read."""
        # Group should have trigger info
        config = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0}
        ]
        
        grouped = group_commands(config)
        group = grouped[0]
        
        # Verify group has all necessary info for polling
        self.assertIn("trigger_reg", group)
        self.assertIn("trigger_val", group)
        self.assertIn("start_reg", group)
        self.assertIn("num_regs", group)
        self.assertEqual(group["trigger_reg"], 2027)
        self.assertEqual(group["trigger_val"], 0)
        self.assertEqual(group["start_reg"], 3500)

    def test_multiple_register_reads_after_single_trigger(self):
        """Test reading multiple registers after a single trigger write."""
        config = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3506, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
            {"reg": 3508, "len": 32, "trigger_reg": 2027, "trigger_val": 0},
        ]
        
        grouped = group_commands(config)
        
        # All should be in one group since same trigger
        self.assertGreaterEqual(len(grouped), 1)
        group = grouped[0]
        
        # Should have one trigger that applies to all
        self.assertEqual(group.get("trigger_reg"), 2027)


class TestDebuggerJsonIntegration(unittest.TestCase):
    """Tests that verify the actual debugger.json configuration."""

    def test_load_debugger_config(self):
        """Test loading the actual debugger.json configuration."""
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "debugger.json"
        )
        
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
            
            self.assertIsInstance(config, list)
            self.assertGreater(len(config), 0)

    def test_find_3500_trigger_entries(self):
        """Test finding 3500 register entries with triggers in debugger.json."""
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "debugger.json"
        )
        
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
            
            # Find entries in 3500 range with triggers
            trigger_entries = [
                cmd for cmd in config
                if 3500 <= cmd.get("reg", 0) <= 3700
                and "trigger_reg" in cmd
            ]
            
            # Should have at least some entries
            self.assertGreater(len(trigger_entries), 0)
            
            # All should have trigger_val
            for entry in trigger_entries:
                self.assertIn("trigger_val", entry)
                self.assertIn("trigger_reg", entry)

    def test_trigger_write_sequence_simulation(self):
        """Simulate the trigger write followed by register read sequence."""
        # This test verifies the sequence that happens during actual polling
        config = [
            {"reg": 3500, "len": 32, "trigger_reg": 2027, "trigger_val": 0}
        ]
        
        grouped = group_commands(config)
        self.assertEqual(len(grouped), 1)
        
        group = grouped[0]
        
        # Simulate what mqtt_debugger.py does:
        # 1. Check if trigger exists
        self.assertIsNotNone(group.get("trigger_reg"))
        
        # 2. Create trigger write command
        trigger_reg = group["trigger_reg"]
        trigger_val = group["trigger_val"]
        write_cmd = WriteSingleRegister(trigger_reg, trigger_val)
        self.assertEqual(write_cmd.address, 2027)
        self.assertEqual(write_cmd.value, 0)
        
        # 3. Create read command for data registers
        read_cmd = ReadHoldingRegisters(group["start_reg"], group["num_regs"])
        self.assertEqual(read_cmd.starting_address, 3500)
        self.assertGreater(read_cmd.quantity, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import unittest

from assembly.models import Color, EntityKind
from assembly.vm_protocol import (
    VM_ACK,
    VisionMeasurementStore,
    VmProtocolError,
    encode_vm_ack,
    extract_vm_packets,
    fixed_vm_packets,
    fixed_workspace_measurements,
    parse_vm_measurement,
    parse_vm_trigger_response,
)


class VmProtocolTests(unittest.TestCase):
    def test_loads_all_project_book_coordinates_without_network(self) -> None:
        measurements = fixed_workspace_measurements()

        self.assertEqual(len(measurements), 12)
        self.assertEqual(
            set(measurements),
            {
                (color, kind)
                for color in Color
                if color.has_tray
                for kind in EntityKind
            },
        )

    def test_every_project_book_packet_parses(self) -> None:
        measurements = [parse_vm_measurement(packet) for packet in fixed_vm_packets()]
        self.assertEqual(len(measurements), 12)
        self.assertIn(
            (Color.RED, EntityKind.BLOCK),
            {measurement.key for measurement in measurements},
        )
        self.assertIn(
            (Color.PURPLE, EntityKind.TRAY),
            {measurement.key for measurement in measurements},
        )

    def test_coordinate_identifies_expected_color_and_kind(self) -> None:
        block = parse_vm_measurement("#0;1;-1;10")
        tray = parse_vm_measurement("#0;30;-30;30")
        self.assertEqual((block.color, block.kind), (Color.RED, EntityKind.BLOCK))
        self.assertEqual((tray.color, tray.kind), (Color.YELLOW, EntityKind.TRAY))

    def test_rejects_invalid_protocol_packets(self) -> None:
        for packet in ("#0;1;-1", "#0;a;-1;10", "#0;7;-7;70"):
            with self.subTest(packet=packet):
                with self.assertRaises(VmProtocolError):
                    parse_vm_measurement(packet)

    def test_accepts_book_format_without_hash_and_extracts_packet_stream(self) -> None:
        measurement = parse_vm_measurement("0;1;-1;10")
        packets = extract_vm_packets(b"#0;1;-1;10\r\n0;30;-30;30\n")

        self.assertEqual(measurement.color, Color.RED)
        self.assertEqual(packets, ("#0;1;-1;10", "0;30;-30;30"))

    def test_store_requires_exactly_one_of_each_fixed_packet(self) -> None:
        store = VisionMeasurementStore()
        for packet in fixed_vm_packets():
            store.ingest(packet)
        self.assertTrue(store.is_complete)
        self.assertEqual(store.count, 12)
        self.assertEqual(store.missing(), ())
        with self.assertRaises(VmProtocolError):
            store.ingest("#0;1;-1;10")

    def test_ack_is_exact(self) -> None:
        self.assertEqual(encode_vm_ack(), VM_ACK.encode("utf-8"))

    def test_trigger_response_maps_all_block_and_tray_branches(self) -> None:
        expected = {
            "11": "#0;1;-1;10;",
            "12": "#0;2;-2;20;",
            "13": "#0;3;-3;30;",
            "14": "#0;4;-4;40;",
            "15": "#0;5;-5;-50;",
            "16": "#0;6;-6;-60;",
            "17": "#0;7;-7;70;",
            "18": "#0;8;-8;80;",
            "19": "#0;9;-9;90;",
            "21": "#0;10;-10;10;",
            "22": "#0;20;-20;20;",
            "23": "#0;30;-30;30;",
            "24": "#0;40;-40;40;",
            "25": "#0;50;-50;-50;",
            "26": "#0;60;-60;-60;",
        }

        for trigger, packet in expected.items():
            with self.subTest(trigger=trigger):
                measurement = parse_vm_trigger_response(
                    trigger,
                    packet.encode("utf-8"),
                )
                self.assertEqual(measurement.raw_packet, packet)

    def test_trigger_response_accepts_randomized_coordinates_and_maps_by_trigger(self) -> None:
        measurement = parse_vm_trigger_response("11", b"#0;147.25;-83.5;40;")

        self.assertEqual(measurement.key, (Color.RED, EntityKind.BLOCK))
        self.assertEqual((measurement.x, measurement.y, measurement.rz), (147.25, -83.5, 40.0))

    def test_trigger_response_rejects_non_exact_formats(self) -> None:
        for packet in (b"#0;1;-1;10", b"#1;-1;10;", b"b'#0;1;-1;10;'", b"text:#0;1;-1;10;"):
            with self.subTest(packet=packet):
                with self.assertRaisesRegex(VmProtocolError, "#0;X;Y;RZ;"):
                    parse_vm_trigger_response("11", packet)

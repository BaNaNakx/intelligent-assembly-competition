from __future__ import annotations

import socket
import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from assembly.visionmaster_tcp import (
    ReceivedTaskImage,
    VisionMasterCollectionConfig,
    VisionMasterImageError,
    VisionMasterTaskCardCollector,
    VisionMasterTcpConfig,
    VisionMasterTcpImageClient,
    detect_image_suffix,
    save_task_image,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"competition-card"


def write_task_card(directory: str, name: str, data: bytes = PNG_BYTES) -> Path:
    path = Path(directory) / name
    path.write_bytes(data)
    return path


class TcpRequestResponseServer:
    def __init__(self, responses: tuple[tuple[bytes, ...], ...]) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(len(responses))
        self.port = self._listener.getsockname()[1]
        self._responses = responses
        self.requests: list[bytes] = []
        self._thread = threading.Thread(target=self._respond, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._thread.join(timeout=2)
        self._listener.close()

    def _respond(self) -> None:
        for chunks in self._responses:
            connection, _ = self._listener.accept()
            with connection:
                request = connection.recv(1024)
                self.requests.append(request)
                for chunk in chunks:
                    connection.sendall(chunk)


class VisionMasterTcpClientTests(unittest.TestCase):
    def test_captures_chunked_image_from_one_connection(self) -> None:
        with TemporaryDirectory() as directory:
            card_path = write_task_card(directory, "task_card_1.png")
            path_payload = str(card_path).encode("utf-8")
            server = TcpRequestResponseServer(
                ((path_payload[:8], path_payload[8:]),)
            )
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(port=server.port, idle_timeout_s=0.2)
                )
                image = client.request_task_card(1)
            finally:
                server.close()

        self.assertEqual(image.data, PNG_BYTES)
        self.assertEqual(image.suffix, ".png")
        self.assertEqual(image.port, server.port)
        self.assertEqual(server.requests, [b"duqu"])

    def test_rejects_task_card_file_larger_than_limit(self) -> None:
        with TemporaryDirectory() as directory:
            card_path = write_task_card(directory, "large.png", PNG_BYTES + b"x" * 600)
            server = TcpRequestResponseServer(((str(card_path).encode("utf-8"),),))
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(
                        port=server.port, max_image_bytes=512, idle_timeout_s=0.2
                    )
                )
                with self.assertRaisesRegex(VisionMasterImageError, "超过"):
                    client.request_task_card(1)
            finally:
                server.close()

    def test_saves_original_bytes_with_detected_suffix(self) -> None:
        image = ReceivedTaskImage(
            data=PNG_BYTES,
            received_at=datetime.now().astimezone(),
            host="127.0.0.1",
            port=7930,
        )
        with TemporaryDirectory() as directory:
            path = save_task_image(image, Path(directory), 1)
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.read_bytes(), PNG_BYTES)

    def test_unknown_data_is_preserved_as_binary(self) -> None:
        self.assertEqual(detect_image_suffix(b"not-an-image"), ".bin")

    def test_rejects_task_card_path_that_is_not_absolute(self) -> None:
        server = TcpRequestResponseServer(((b"task_card_1.png",),))
        server.start()
        try:
            client = VisionMasterTcpImageClient(
                VisionMasterTcpConfig(port=server.port, idle_timeout_s=0.2)
            )
            with self.assertRaisesRegex(VisionMasterImageError, "绝对路径"):
                client.request_task_card(1)
        finally:
            server.close()

    def test_sends_one_prefixed_trigger_without_extra_characters(self) -> None:
        server = TcpRequestResponseServer(
            (("格式化结果:#0;1;-1;10;".encode("utf-8"),),)
        )
        server.start()
        try:
            client = VisionMasterTcpImageClient(
                VisionMasterTcpConfig(port=server.port, idle_timeout_s=0.2)
            )
            measurement = client.request_measurement("wukuai", "11")
        finally:
            server.close()

        self.assertEqual(server.requests, [b"wukuai,11"])
        self.assertEqual(measurement.raw_packet, "#0;1;-1;10;")

    def test_collector_requests_cards_by_number_in_competition_order(self) -> None:
        with TemporaryDirectory() as directory:
            card1 = write_task_card(directory, "task_card_1.png", PNG_BYTES + b"-one")
            card2 = write_task_card(directory, "task_card_2.png", PNG_BYTES + b"-two")
            server = TcpRequestResponseServer(
                ((str(card1).encode("utf-8"),), (str(card2).encode("utf-8"),))
            )
            server.start()
            try:
                collector = VisionMasterTaskCardCollector(
                    VisionMasterCollectionConfig(
                        tcp=VisionMasterTcpConfig(port=server.port, idle_timeout_s=0.2),
                        retry_interval_s=0.01,
                        collection_timeout_s=2,
                    )
                )
                first_card = collector.collect_card(1)
                self.assertEqual(first_card.data, PNG_BYTES + b"-one")
                second_card = collector.collect_card(2)
            finally:
                server.close()

        self.assertEqual(second_card.data, PNG_BYTES + b"-two")
        self.assertEqual(
            server.requests,
            [b"duqu", b"duqu"],
        )

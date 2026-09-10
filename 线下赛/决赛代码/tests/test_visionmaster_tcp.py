from __future__ import annotations

import socket
import time
from io import BytesIO
from unittest.mock import patch

from PIL import Image
import threading
import unittest
from collections.abc import Callable
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


_image_buffer = BytesIO()
Image.new("RGB", (4, 4), "white").save(_image_buffer, format="PNG")
PNG_BYTES = _image_buffer.getvalue()


def write_task_card(directory: str, name: str, data: bytes = PNG_BYTES) -> Path:
    path = Path(directory) / name
    path.write_bytes(data)
    return path


class TcpRequestResponseServer:
    def __init__(
        self,
        responses: tuple[tuple[bytes, ...], ...],
        actions: tuple[Callable[[], None], ...] | None = None,
    ) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(len(responses))
        self.port = self._listener.getsockname()[1]
        self._responses = responses
        self._actions = actions or tuple(lambda: None for _ in responses)
        self.requests: list[bytes] = []
        self._thread = threading.Thread(target=self._respond, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._thread.join(timeout=2)
        self._listener.close()

    def _respond(self) -> None:
        for chunks, action in zip(self._responses, self._actions):
            connection, _ = self._listener.accept()
            with connection:
                request = connection.recv(1024)
                self.requests.append(request)
                action()
                for chunk in chunks:
                    connection.sendall(chunk)


class VisionMasterTcpClientTests(unittest.TestCase):
    def test_waits_for_capture_ack_then_reads_newest_image(self) -> None:
        with TemporaryDirectory() as directory:
            write_task_card(directory, "task_card_1_old.png", PNG_BYTES + b"-old")
            server = TcpRequestResponseServer(
                ((b"0011",),),
                (lambda: write_task_card(directory, "task_card_1_new.png"),),
            )
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(
                        port=server.port,
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=0.8,
                    )
                )
                image = client.request_task_card(1)
            finally:
                server.close()

        self.assertEqual(image.data, PNG_BYTES)
        self.assertEqual(image.suffix, ".png")
        self.assertEqual(image.port, server.port)
        self.assertEqual(server.requests, [b"duqu,99"])

    def test_uses_newest_new_image_regardless_of_name(self) -> None:
        with TemporaryDirectory() as directory:
            write_task_card(directory, "task_card_1_004.png", PNG_BYTES + b"-old")

            def create_two_new_images() -> None:
                first = write_task_card(directory, "arbitrary_name.png", PNG_BYTES + b"-first")
                second = write_task_card(directory, "random_latest.png", PNG_BYTES + b"-latest")
                first.touch()
                second.touch()

            server = TcpRequestResponseServer(((b"0011",),), (create_two_new_images,))
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(
                        port=server.port,
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=0.8,
                    )
                )
                image = client.request_task_card(2)
            finally:
                server.close()

        self.assertEqual(image.data, PNG_BYTES + b"-latest")
        self.assertEqual(server.requests, [b"duqu,99"])

    def test_old_images_remain_untouched(self):
        with TemporaryDirectory() as directory:
            old = write_task_card(directory, "previous.png", PNG_BYTES + b"-old")
            server = TcpRequestResponseServer(
                ((b"00", b"11"),),
                (lambda: write_task_card(directory, "new_capture.png"),),
            )
            server.start()
            try:
                client = VisionMasterTcpImageClient(VisionMasterTcpConfig(
                    port=server.port, task_card_image_directory=directory))
                self.assertEqual(client.request_task_card(1).data, PNG_BYTES)
                self.assertEqual(old.read_bytes(), PNG_BYTES + b"-old")
                self.assertEqual(len(list(Path(directory).iterdir())), 2)
            finally:
                server.close()

    def test_rejects_invalid_ack_before_reading_image(self):
        for response in (b"11", b"0012", b"0011\\n", b"\\x00\\x11"):
            with self.subTest(response=response), TemporaryDirectory() as directory:
                server = TcpRequestResponseServer(((response,),),
                    (lambda: write_task_card(directory, "fresh.png"),))
                server.start()
                try:
                    client = VisionMasterTcpImageClient(VisionMasterTcpConfig(
                        port=server.port, task_card_image_directory=directory))
                    with patch("assembly.visionmaster_tcp.read_task_card_file") as read:
                        with self.assertRaises(VisionMasterImageError):
                            client.request_task_card(1)
                        read.assert_not_called()
                finally:
                    server.close()

    def test_waits_for_partial_ack_and_incomplete_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            class Connection:
                def __enter__(self): return self
                def __exit__(self, *_): pass
                def settimeout(self, _): pass
                def sendall(self, payload):
                    self.request = payload
                    path.write_bytes(PNG_BYTES[:12])
                def recv(self, _):
                    return next(self.chunks)
            connection = Connection()
            connection.chunks = iter((b"00", b"1", b"1"))
            timer = threading.Timer(0.25, lambda: path.write_bytes(PNG_BYTES))
            timer.start()
            try:
                with patch("assembly.visionmaster_tcp.socket.create_connection", return_value=connection):
                    image = VisionMasterTcpImageClient(VisionMasterTcpConfig(
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=1)).request_task_card(1)
                self.assertEqual(image.data, PNG_BYTES)
                self.assertEqual(connection.request, b"duqu,99")
            finally:
                timer.join()

    def test_ack_timeout_and_reset_never_read_images(self):
        for reset in (False, True):
            with self.subTest(reset=reset), TemporaryDirectory() as directory:
                event = threading.Event()
                class Connection:
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                    def settimeout(self, _): pass
                    def sendall(self, _): pass
                    def recv(self, _):
                        if reset:
                            event.set()
                        time.sleep(0.01)
                        raise socket.timeout()
                with patch("assembly.visionmaster_tcp.socket.create_connection", return_value=Connection()), \
                     patch("assembly.visionmaster_tcp.read_task_card_file") as read:
                    client = VisionMasterTcpImageClient(VisionMasterTcpConfig(
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=0.05), event)
                    with self.assertRaises(RuntimeError if reset else VisionMasterImageError):
                        client.request_task_card(1)
                    read.assert_not_called()

    def test_rejects_task_card_file_larger_than_limit(self) -> None:
        with TemporaryDirectory() as directory:
            server = TcpRequestResponseServer(
                ((b"0011",),),
                (lambda: write_task_card(directory, "large_001.png", PNG_BYTES + b"x" * 600),),
            )
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(
                        port=server.port,
                        max_image_bytes=512,
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=0.8,
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

    def test_reports_missing_configured_task_card_file(self) -> None:
        with TemporaryDirectory() as directory:
            write_task_card(directory, "old.png")
            server = TcpRequestResponseServer(((b"0011",),))
            server.start()
            try:
                client = VisionMasterTcpImageClient(
                    VisionMasterTcpConfig(
                        port=server.port,
                        task_card_image_directory=directory,
                        task_card_capture_timeout_s=0.8,
                    )
                )
                with self.assertRaisesRegex(VisionMasterImageError, "没有发现"):
                    client.request_task_card(1)
            finally:
                server.close()

    def test_sends_one_prefixed_trigger_without_extra_characters(self) -> None:
        server = TcpRequestResponseServer(
            ((b"#0;1;-1;10;",),)
        )
        server.start()
        try:
            client = VisionMasterTcpImageClient(
                VisionMasterTcpConfig(port=server.port, idle_timeout_s=0.2)
            )
            measurement = client.request_measurement("11")
        finally:
            server.close()

        self.assertEqual(server.requests, [b"wukuai,11"])
        self.assertEqual(measurement.raw_packet, "#0;1;-1;10;")

    def test_collector_requests_cards_by_number_in_competition_order(self) -> None:
        with TemporaryDirectory() as directory:
            server = TcpRequestResponseServer(
                ((b"0011",), (b"0011",)),
                (
                    lambda: write_task_card(directory, "task_card_1_001.png", PNG_BYTES + b"-one"),
                    lambda: write_task_card(directory, "task_card_1_004.png", PNG_BYTES + b"-two"),
                ),
            )
            server.start()
            try:
                collector = VisionMasterTaskCardCollector(
                    VisionMasterCollectionConfig(
                        tcp=VisionMasterTcpConfig(
                            port=server.port,
                            task_card_image_directory=directory,
                            task_card_capture_timeout_s=0.8,
                        ),
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
            [b"duqu,99", b"duqu,99"],
        )

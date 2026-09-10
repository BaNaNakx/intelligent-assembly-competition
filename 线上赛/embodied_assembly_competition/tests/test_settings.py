from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assembly.settings import (
    ConfigurationError,
    load_competition_config,
    load_visionmaster_collection_config,
)


VALID_CONFIG = """
[visionmaster]
host = "192.168.10.20"
port = 9000
connect_timeout_s = 3.0
idle_timeout_s = 0.5
max_image_bytes = 1024
chunk_size = 256
task_card_payload_encoding = "local_image_path"
retry_interval_s = 0.1
collection_timeout_s = 30.0

[vmware]
network_mode = "host_only"
arcs_guest_ip = "192.168.18.10"

[aubo_arcs]
host = "192.168.18.10"
port = 5002
robot_name = "rob1"
request_timeout_s = 5.0

[llm]
base_url = "https://example.test/v1"
model = "qwen3.7-plus"
request_timeout_s = 45.0
"""


class SettingsTests(unittest.TestCase):
    def test_loads_all_pre_competition_network_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(VALID_CONFIG, encoding="utf-8")
            config = load_visionmaster_collection_config(path)

        self.assertEqual(config.tcp.host, "192.168.10.20")
        self.assertEqual(config.tcp.port, 9000)
        self.assertEqual(config.collection_timeout_s, 30)

    def test_loads_vmware_and_arcs_pre_competition_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(VALID_CONFIG, encoding="utf-8")
            config = load_competition_config(path)

        self.assertEqual(config.vmware.network_mode, "host_only")
        self.assertEqual(config.vmware.arcs_guest_ip, "192.168.18.10")
        self.assertEqual(config.aubo_arcs.host, "192.168.18.10")
        self.assertEqual(config.aubo_arcs.port, 5002)
        self.assertEqual(config.aubo_arcs.robot_name, "rob1")
        self.assertEqual(config.llm.model, "qwen3.7-plus")
        self.assertEqual(config.llm.request_timeout_s, 45)

    def test_rejects_missing_or_invalid_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[visionmaster]\nhost = \"\"\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_visionmaster_collection_config(path)

        with self.assertRaises(ConfigurationError):
            load_visionmaster_collection_config(Path("does-not-exist.toml"))

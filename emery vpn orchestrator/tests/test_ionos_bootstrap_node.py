"""Pure/offline tests, also runnable with stdlib unittest without backend deps."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import tempfile
import unittest
import urllib.error
import uuid
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / "deploy/ionos-cloud/bootstrap_node.py"
SPEC = importlib.util.spec_from_file_location("ionos_bootstrap_node_tests", PATH)
node = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(node)


def config():
    return {
        "operation_id": str(uuid.uuid4()), "node_id": 7, "hostname": "node.vpn.example.com",
        "endpoint": "93.184.216.34", "management_ipv4": "9.9.9.9", "gate_port": 24443,
        "assignment_port_start": 20000, "assignment_port_end": 20199,
        "authorize_url": "https://control.example.com/internal/device-gate/authorize", "authorize_key": "k" * 40,
        "probe_url": "https://example.com/", "acme_email": "operator@example.com", "acme_terms_accepted": True,
        "xray_version": "26.8.1", "xray_sha256": "a" * 64, "reality_server_name": "example.com",
    }


KEYS = {"private_key": "private-fixture", "public_key": "a" * 43,
        "short_id": "0123456789abcdef", "template_uuid": "11111111-1111-4111-8111-111111111111"}


class BootstrapOfflineTests(unittest.TestCase):
    def test_valid_configuration(self):
        node.validate(config())

    def test_invalid_configuration_is_rejected_before_any_commands(self):
        for key, value in [
            ("endpoint", "127.0.0.1"), ("management_ipv4", "0.0.0.0/0"),
            ("authorize_key", "k" * 32 + "\nBAD=1"), ("hostname", "host.example;whoami"),
            ("authorize_url", "http://control.example.com/internal/device-gate/authorize"),
            ("authorize_url", "https://control.example.com/other"), ("gate_port", 22),
            ("assignment_port_start", True), ("acme_terms_accepted", False), ("xray_version", "latest"),
        ]:
            with self.subTest(key=key, value=value), patch.object(node, "run") as command:
                value_config = config()
                value_config[key] = value
                with self.assertRaises((ValueError, node.BootstrapError)):
                    node.validate(value_config)
                command.assert_not_called()

    def test_template_has_no_working_shared_uuid_or_public_inbound(self):
        value = node.xray_config(config(), KEYS)
        self.assertEqual(value["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(value["inbounds"][0]["settings"]["clients"], [])
        self.assertNotIn(KEYS["template_uuid"], json.dumps(value))
        self.assertEqual(value["outbounds"][0]["protocol"], "freedom")
        self.assertEqual(value["routing"]["rules"][0]["port"], "25,465,587")
        self.assertIn("flow=xtls-rprx-vision", node.template_uri(config(), KEYS))

    def test_firewall_does_not_flush_others_or_expose_assignment_ports(self):
        rules = node.firewall_rules(config())
        self.assertIn("ip saddr 9.9.9.9 tcp dport 22 accept", rules)
        self.assertIn("tcp dport { 80, 24443 } accept", rules)
        self.assertIn("policy drop", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertNotIn("tcp dport 22 accept\n", rules.replace("ip saddr 9.9.9.9 tcp dport 22 accept", ""))
        self.assertNotIn("20000", rules)
        self.assertIn("tcp dport { 25, 465, 587 } drop", rules)

    def test_rate_limits_are_rebuilt_from_dedicated_listeners_after_reboot(self):
        value = node.xray_config(config(), KEYS)
        inbound = copy.deepcopy(value["inbounds"][0])
        inbound.update(tag="emery-device-91-30", port=20000)
        inbound["settings"]["clients"] = [{"id": KEYS["template_uuid"], "flow": "xtls-rprx-vision"}]
        value["inbounds"].append(inbound)
        rules = node.rate_rules(config(), value)
        self.assertIn("tcp dport 20000 limit rate over 3750 kbytes/second", rules)
        self.assertIn("tcp sport 20000 limit rate over 3750 kbytes/second", rules)
        self.assertNotIn("emery_ionos_ingress", rules)
        inbound["listen"] = "0.0.0.0"
        with self.assertRaises(node.BootstrapError):
            node.rate_rules(config(), value)

    def test_rate_restore_refuses_any_shared_template_credential(self):
        value = node.xray_config(config(), KEYS)
        value["inbounds"][0]["settings"]["clients"] = [{"id": KEYS["template_uuid"]}]
        with self.assertRaises(node.BootstrapError):
            node.rate_rules(config(), value)

    def test_archive_checksum_precedes_extraction_and_ignores_unknown_paths(self):
        with tempfile.TemporaryDirectory(prefix="ionos-archive-test-") as temporary:
            directory = Path(temporary)
            archive = directory / "archive.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                for name in ("xray", "geoip.dat", "geosite.dat"):
                    handle.writestr(name, name.encode())
                handle.writestr("../unowned", "must not be extracted")
            with self.assertRaisesRegex(node.BootstrapError, "checksum_mismatch"):
                node.extract_verified_xray(archive, "a" * 64, directory)
            self.assertFalse((directory / "xray").exists())
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = directory / "output"
            destination.mkdir()
            node.extract_verified_xray(archive, checksum, destination)
            self.assertEqual((destination / "xray").read_bytes(), b"xray")
            self.assertEqual(set(path.name for path in destination.iterdir()), {"xray", "geoip.dat", "geosite.dat"})
            self.assertFalse((directory / "unowned").exists())

    def test_archive_symlinks_cannot_install_an_executable(self):
        with tempfile.TemporaryDirectory(prefix="ionos-zip-test-") as temporary:
            directory = Path(temporary)
            archive = directory / "archive.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                info = zipfile.ZipInfo("xray")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                handle.writestr(info, "/etc/passwd")
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(node.BootstrapError, "archive_unsafe"):
                node.extract_verified_xray(archive, checksum, directory)

    def test_redirects_cannot_send_control_key_to_another_host(self):
        self.assertIsNone(node.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.example/"))
        with self.assertRaises(node.BootstrapError):
            node.ReleaseRedirect().redirect_request(None, None, 302, "redirect", {}, "http://github.com/file")

    def test_control_probe_requires_correct_service_key_and_denies_unregistered_device(self):
        value = config()
        for reason, succeeds in (("device_gate_not_authorized", True), ("device_gate_forbidden", False)):
            with self.subTest(reason=reason):
                error = urllib.error.HTTPError(value["authorize_url"], 403, "Forbidden", {},
                    io.BytesIO(json.dumps({"ok": False, "reason": reason}).encode()))
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(node, "certificate_pin", return_value="b" * 64), patch.object(node.urllib.request, "build_opener", return_value=opener):
                    if succeeds:
                        node.control_probe(value)
                    else:
                        with self.assertRaises(node.BootstrapError):
                            node.control_probe(value)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header("X-device-gate-key"), value["authorize_key"])
                self.assertEqual(json.loads(request.data)["protocol_version"], 2)
                self.assertEqual(json.loads(request.data)["regional_policy"], "russia")

    def test_command_error_does_not_leak_private_output(self):
        error = subprocess.CalledProcessError(1, ["fixture-command"], stderr="PRIVATE-KEY-MATERIAL")
        with patch.object(node.subprocess, "run", side_effect=error):
            with self.assertRaises(node.BootstrapError) as raised:
                node.run(["fixture-command"])
        self.assertNotIn("PRIVATE-KEY", str(raised.exception))

    def test_resuming_partial_install_keeps_completed_stages_and_keys(self):
        with tempfile.TemporaryDirectory(prefix="ionos-resume-test-") as temporary:
            state = Path(temporary)
            value = config()
            actions = {name: Mock() for name in ("install_packages", "install_xray", "install_firewall", "configure_xray",
                       "provision_certificate", "install_gate", "install_regional_policy", "run", "regional_ready", "control_probe")}
            actions["install_gate"].side_effect = node.BootstrapError("fixture_gate_failure")
            with patch.multiple(node, **actions), patch.object(node, "STATE", state), patch.object(node, "XRAY_CONFIG", state / "xray.json"), \
                    patch.object(node, "seed", return_value=KEYS), patch.object(node, "certificate_pin", return_value="b" * 64), \
                    patch.object(node, "read_object", side_effect=lambda path, **_: json.loads(path.read_text())):
                with self.assertRaises(node.BootstrapError):
                    node.bootstrap(value)
                self.assertFalse((state / "ready.json").exists())
                actions["install_gate"].side_effect = None
                node.bootstrap(value)
                actions["install_packages"].assert_called_once()
                actions["configure_xray"].assert_called_once()
                actions["install_regional_policy"].assert_called_once()
                self.assertEqual(actions["install_gate"].call_count, 2)
                ready = json.loads((state / "ready.json").read_text())
                self.assertTrue(ready["regional_policy_ready"])
                self.assertEqual(ready["operation_id"], value["operation_id"])
                self.assertNotIn(value["authorize_key"], (state / "ready.json").read_text())
                changed = dict(value, operation_id=str(uuid.uuid4()))
                with self.assertRaisesRegex(node.BootstrapError, "operation_conflict"):
                    node.bootstrap(changed)

    def test_existing_unowned_node_is_untouched(self):
        with tempfile.TemporaryDirectory(prefix="ionos-existing-test-") as temporary:
            state = Path(temporary)
            existing = state / "xray.json"
            existing.write_text('{"existing":"do-not-change"}')
            with patch.object(node, "STATE", state), patch.object(node, "XRAY_CONFIG", existing), patch.object(node, "install_packages") as install:
                with self.assertRaisesRegex(node.BootstrapError, "existing_node"):
                    node.bootstrap(config())
                install.assert_not_called()
            self.assertEqual(existing.read_text(), '{"existing":"do-not-change"}')


if __name__ == "__main__":
    unittest.main()

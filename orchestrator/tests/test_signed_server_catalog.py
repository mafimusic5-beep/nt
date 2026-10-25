import os
import unittest
from unittest.mock import patch

from fastapi import Request

from client_version_policy import (
    optional_lease_version,
    public_listing_disabled,
    signed_version,
)
from device_auth import DeviceAuthError, _request_canonical


def request(headers=None):
    pairs = [(name.lower().encode('ascii'), str(value).encode('ascii'))
             for name, value in (headers or {}).items()]
    return Request({'type': 'http', 'method': 'GET', 'path': '/api/vpn/servers/authorized',
                    'headers': pairs, 'query_string': b'', 'server': ('localhost', 80)})


class SignedCatalogPolicyTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'SKRYON_WINDOWS_MIN_VERSION_CODE': '20261026',
            'SKRYON_REQUIRE_VERSIONED_LEASES': '0',
            'SKRYON_PUBLIC_SERVER_LIST_DISABLED': '0',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_accept_current_version_with_signed_protocol(self):
        self.assertEqual(20261026, signed_version(request({
            'X-Skryon-Proof-Version': '2',
            'X-Skryon-App-Version-Code': '20261026',
        })))

    def test_reject_missing_version_and_protocol(self):
        for headers in ({}, {'X-Skryon-App-Version-Code': '999999999'},
                        {'X-Skryon-Proof-Version': '2'}):
            with self.subTest(headers=headers):
                with self.assertRaises(DeviceAuthError) as caught:
                    signed_version(request(headers))
                self.assertEqual(426, caught.exception.status_code)

    def test_reject_stale_version(self):
        with self.assertRaises(DeviceAuthError) as caught:
            signed_version(request({
                'X-Skryon-Proof-Version': '2',
                'X-Skryon-App-Version-Code': '20261025',
            }))
        self.assertEqual('upgrade_required', caught.exception.reason)

    def test_reject_malformed_version(self):
        for v in ('-1', 'hello', '20261026.1', '９９９９', '1' * 50):
            with self.subTest(version=v):
                with self.assertRaises(DeviceAuthError):
                    signed_version(request({
                        'X-Skryon-Proof-Version': '2',
                        'X-Skryon-App-Version-Code': v,
                    }))

    def test_version_must_be_part_of_signed_canonical(self):
        args = dict(method='GET', path='/api/vpn/servers/authorized',
                    device_id='device-1', timestamp='1234567891234',
                    nonce='abcd' * 8, raw_code='some-secret')
        old = _request_canonical(**args)
        newer = _request_canonical(**args, app_version_code=20261026)
        self.assertEqual(newer, old + '\napp_version_code=20261026')
        self.assertNotEqual(newer, _request_canonical(**args, app_version_code=20261027))

    def test_partial_version_headers_rejected_on_lease(self):
        with self.assertRaises(DeviceAuthError):
            optional_lease_version(request({'X-Skryon-Proof-Version': '2'}))

    def test_legacy_leases_switch_to_strict_only_after_rollout(self):
        self.assertIsNone(optional_lease_version(request()))
        with patch.dict(os.environ, {'SKRYON_REQUIRE_VERSIONED_LEASES': '1'}):
            with self.assertRaises(DeviceAuthError):
                optional_lease_version(request())

    def test_catalog_public_listing_switch(self):
        self.assertFalse(public_listing_disabled())
        with patch.dict(os.environ, {'SKRYON_PUBLIC_SERVER_LIST_DISABLED': '1'}):
            self.assertTrue(public_listing_disabled())


if __name__ == '__main__':
    unittest.main()

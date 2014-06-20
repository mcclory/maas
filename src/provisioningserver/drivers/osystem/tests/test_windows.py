# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the WindowsOS module."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from itertools import product

from maastesting.factory import factory
from maastesting.testcase import MAASTestCase
from mock import MagicMock
from provisioningserver.drivers.osystem.windows import (
    BOOT_IMAGE_PURPOSE,
    REQUIRE_LICENSE_KEY,
    WINDOWS_CHOICES,
    WINDOWS_DEFAULT,
    WindowsOS,
    )


class TestWindowsOS(MAASTestCase):

    def test_get_boot_image_purposes(self):
        osystem = WindowsOS()
        archs = [factory.make_name('arch') for _ in range(2)]
        subarchs = [factory.make_name('subarch') for _ in range(2)]
        releases = [factory.make_name('release') for _ in range(2)]
        labels = [factory.make_name('label') for _ in range(2)]
        for arch, subarch, release, label in product(
                archs, subarchs, releases, labels):
            expected = osystem.get_boot_image_purposes(
                arch, subarchs, release, label)
            self.assertIsInstance(expected, list)
            self.assertEqual(expected, [
                BOOT_IMAGE_PURPOSE.INSTALL,
                ])

    def test_get_supported_releases(self):
        osystem = WindowsOS()
        expected = osystem.get_supported_releases()
        self.assertIsInstance(expected, list)
        self.assertItemsEqual(expected, list(WINDOWS_CHOICES.keys()))

    def test_get_default_release(self):
        osystem = WindowsOS()
        expected = osystem.get_default_release()
        self.assertEqual(expected, WINDOWS_DEFAULT)

    def test_format_release_choices(self):
        osystem = WindowsOS()
        releases = osystem.get_supported_releases()
        formatted = osystem.format_release_choices(releases)
        self.assertEqual(WINDOWS_CHOICES, dict(formatted))

    def test_requires_license_key_True(self):
        osystem = WindowsOS()
        for release in REQUIRE_LICENSE_KEY:
            self.assertTrue(osystem.requires_license_key(release))

    def test_requires_license_key_False(self):
        osystem = WindowsOS()
        not_required = set(
            WINDOWS_CHOICES.keys()).difference(REQUIRE_LICENSE_KEY)
        for release in not_required:
            self.assertFalse(osystem.requires_license_key(release))

    def test_validate_license_key(self):
        osystem = WindowsOS()
        parts = [factory.getRandomString(size=5) for _ in range(5)]
        key = '-'.join(parts)
        self.assertTrue(
            osystem.validate_license_key(REQUIRE_LICENSE_KEY[0], key))

    def test_validate_license_key_invalid(self):
        osystem = WindowsOS()
        keys = [factory.getRandomString() for _ in range(3)]
        for key in keys:
            self.assertFalse(
                osystem.validate_license_key(REQUIRE_LICENSE_KEY[0], key))

    def make_node(self, hostname=None):
        if hostname is None:
            machine = factory.make_name('hostname')
            dns = factory.make_name('dns')
            hostname = '%s.%s' % (machine, dns)
        node = MagicMock()
        node.hostname = hostname
        return node

    def make_token(self, consumer_key=None, token_key=None, token_secret=None):
        if consumer_key is None:
            consumer_key = factory.make_name('consumer_key')
        if token_key is None:
            token_key = factory.make_name('token_key')
        if token_secret is None:
            token_secret = factory.make_name('secret_key')
        token = MagicMock()
        token.consumer.key = consumer_key
        token.key = token_key
        token.secret = token_secret
        return token

    def test_compose_preseed_has_required_keys(self):
        osystem = WindowsOS()
        node = self.make_node()
        token = self.make_token()
        url = factory.make_name('url')
        required_keys = [
            'maas_metadata_url',
            'maas_oauth_consumer_secret',
            'maas_oauth_consumer_key',
            'maas_oauth_token_key',
            'maas_oauth_token_secret',
            'hostname',
            ]
        preseed = osystem.compose_preseed(node, token, url)
        self.assertItemsEqual(required_keys, preseed.keys())

    def test_compose_preseed_uses_only_hostname(self):
        osystem = WindowsOS()
        machine = factory.make_name('hostname')
        dns = factory.make_name('dns')
        hostname = '%s.%s' % (machine, dns)
        node = self.make_node(hostname=hostname)
        token = self.make_token()
        url = factory.make_name('url')
        preseed = osystem.compose_preseed(node, token, url)
        self.assertEqual(machine, preseed['hostname'])

    def test_compose_preseed_truncates_hostname(self):
        osystem = WindowsOS()
        machine = factory.make_name('hostname', size=20)
        dns = factory.make_name('dns')
        hostname = '%s.%s' % (machine, dns)
        node = self.make_node(hostname=hostname)
        token = self.make_token()
        url = factory.make_name('url')
        preseed = osystem.compose_preseed(node, token, url)
        self.assertEqual(15, len(preseed['hostname']))

    def test_compose_preseed_includes_oauth(self):
        osystem = WindowsOS()
        node = self.make_node()
        consumer_key = factory.make_name('consumer_key')
        token_key = factory.make_name('token_key')
        token_secret = factory.make_name('secret_key')
        token = self.make_token(
            consumer_key=consumer_key, token_key=token_key,
            token_secret=token_secret)
        url = factory.make_name('url')
        preseed = osystem.compose_preseed(node, token, url)
        self.assertEqual('', preseed['maas_oauth_consumer_secret'])
        self.assertEqual(consumer_key, preseed['maas_oauth_consumer_key'])
        self.assertEqual(token_key, preseed['maas_oauth_token_key'])
        self.assertEqual(token_secret, preseed['maas_oauth_token_secret'])

    def test_compose_preseed_includes_metadata_url(self):
        osystem = WindowsOS()
        node = self.make_node()
        token = self.make_token()
        url = factory.make_name('url')
        preseed = osystem.compose_preseed(node, token, url)
        self.assertEqual(url, preseed['maas_metadata_url'])

# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the boot_resources module."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from datetime import (
    datetime,
    timedelta,
)
import errno
import hashlib
import json
import os
from random import randint
from subprocess import (
    PIPE,
    Popen,
)

from maastesting.factory import factory
from maastesting.matchers import (
    MockAnyCall,
    MockCalledWith,
    MockCallsMatch,
)
from maastesting.testcase import MAASTestCase
from maastesting.utils import age_file
import mock
from mock import call
from provisioningserver.boot import BootMethodRegistry
import provisioningserver.config
from provisioningserver.config import BootSources
from provisioningserver.import_images import boot_resources
from provisioningserver.import_images.boot_image_mapping import (
    BootImageMapping,
)
from provisioningserver.import_images.testing.factory import make_image_spec
from provisioningserver.testing.config import BootSourcesFixture
from provisioningserver.utils.fs import write_text_file
from testtools.content import Content
from testtools.content_type import UTF8_TEXT
from testtools.matchers import (
    DirExists,
    FileExists,
)
import yaml


class TestTgtEntry(MAASTestCase):
    """Tests for `tgt_entry`."""

    def test_generates_one_target(self):
        spec = make_image_spec()
        osystem = factory.make_name('osystem')
        image = self.make_file()
        entry = boot_resources.tgt_entry(
            osystem, spec.arch, spec.subarch, spec.release, spec.label, image)
        # The entry looks a bit like XML, but isn't well-formed.  So don't try
        # to parse it as such!
        self.assertIn('<target iqn.2004-05.com.ubuntu:maas:', entry)
        self.assertIn('backing-store "%s"' % image, entry)
        self.assertEqual(1, entry.count('</target>'))

    def test_produces_suitable_output_for_tgt_admin(self):
        spec = make_image_spec()
        image = self.make_file()
        osystem = factory.make_name('osystem')
        entry = boot_resources.tgt_entry(
            osystem, spec.arch, spec.subarch, spec.release, spec.label, image)
        config = self.make_file(contents=entry)
        # Pretend to be root, but without requiring the actual privileges and
        # without prompting for a password.  In that state, run tgt-admin.
        # It has to think it's root, even for a "pretend" run.
        # Make it read the config we just produced, and pretend to update its
        # iSCSI targets based on what it finds in the config.
        #
        # The only real test is that this succeed.
        cmd = Popen(
            [
                'fakeroot', 'tgt-admin',
                '--conf', config,
                '--pretend',
                '--update', 'ALL',
            ],
            stdout=PIPE, stderr=PIPE)
        stdout, stderr = cmd.communicate()
        self.addDetail('tgt-stderr', Content(UTF8_TEXT, lambda: [stderr]))
        self.addDetail('tgt-stdout', Content(UTF8_TEXT, lambda: [stdout]))
        self.assertEqual(0, cmd.returncode)


class TestUpdateCurrentSymlink(MAASTestCase):

    def make_test_dirs(self):
        storage_dir = self.make_dir()
        target_dir = os.path.join(storage_dir, factory.make_name("target"))
        return storage_dir, target_dir

    def assertLinkIsUpdated(self, storage_dir, target_dir):
        boot_resources.update_current_symlink(storage_dir, target_dir)
        link_path = os.path.join(storage_dir, "current")
        self.assertEqual(target_dir, os.readlink(link_path))

    def test_creates_current_symlink(self):
        storage_dir, target_dir = self.make_test_dirs()
        self.assertLinkIsUpdated(storage_dir, target_dir)

    def test_creates_current_symlink_when_link_exists(self):
        storage_dir = self.make_dir()
        self.assertLinkIsUpdated(storage_dir, "target01")
        self.assertLinkIsUpdated(storage_dir, "target02")

    def test_creates_current_symlink_when_temp_link_exists(self):
        symlink_real = os.symlink
        symlink = self.patch(os, "symlink")

        def os_symlink(src, dst):
            if symlink.call_count in (1, 2):
                raise OSError(errno.EEXIST, dst)
            else:
                return symlink_real(src, dst)

        # The first two times that os.symlink() is called, it will raise
        # OSError with EEXIST; update_current_symlink() handles this and
        # tries to create a new symlink with a different suffix.
        symlink.side_effect = os_symlink

        # Make the choice of provisional symlink less random, so that we can
        # match against what's happening.
        from provisioningserver.utils import fs
        randint = self.patch_autospec(fs, "randint")
        randint.side_effect = lambda a, b: randint.call_count

        storage_dir, target_dir = self.make_test_dirs()
        self.assertLinkIsUpdated(storage_dir, target_dir)
        self.assertThat(symlink, MockCallsMatch(
            call(target_dir, os.path.join(storage_dir, ".temp.000001")),
            call(target_dir, os.path.join(storage_dir, ".temp.000002")),
            call(target_dir, os.path.join(storage_dir, ".temp.000003")),
        ))

    def test_fails_when_creating_temp_link_exists_a_lot(self):
        symlink = self.patch(os, "symlink")
        symlink.side_effect = OSError(errno.EEXIST, "sorry buddy")
        storage_dir, target_dir = self.make_test_dirs()
        # If os.symlink() returns EEXIST more than 100 times, it gives up.
        error = self.assertRaises(
            OSError, boot_resources.update_current_symlink,
            storage_dir, target_dir)
        self.assertIs(error, symlink.side_effect)
        self.assertEqual(100, symlink.call_count)

    def test_fails_when_creating_temp_link_fails(self):
        symlink = self.patch(os, "symlink")
        symlink.side_effect = OSError(errno.EPERM, "just no")
        storage_dir, target_dir = self.make_test_dirs()
        # Errors from os.symlink() other than EEXIST are re-raised.
        error = self.assertRaises(
            OSError, boot_resources.update_current_symlink,
            storage_dir, target_dir)
        self.assertIs(error, symlink.side_effect)

    def test_cleans_up_when_renaming_fails(self):
        symlink = self.patch(os, "rename")
        symlink.side_effect = OSError(errno.EPERM, "just no")
        storage_dir, target_dir = self.make_test_dirs()
        error = self.assertRaises(
            OSError, boot_resources.update_current_symlink,
            storage_dir, target_dir)
        self.assertIs(error, symlink.side_effect)
        # No intermediate files are left behind.
        self.assertEqual([], os.listdir(storage_dir))


def checksum_sha256(data):
    """Return the SHA256 checksum for `data`, as a hex string."""
    assert isinstance(data, bytes)
    summer = hashlib.sha256()
    summer.update(data)
    return summer.hexdigest()


class TestMain(MAASTestCase):

    def setUp(self):
        super(TestMain, self).setUp()
        self.storage = self.make_dir()
        self.patch(
            provisioningserver.config, 'BOOT_RESOURCES_STORAGE', self.storage)
        # Forcing arch to amd64 causes pxelinux.0 to be installed, giving more
        # test coverage.
        self.image = make_image_spec(arch='amd64')
        self.os, self.arch, self.subarch, \
            self.release, self.label = self.image
        self.repo = self.make_simplestreams_repo(self.image)

    def patch_maaslog(self):
        """Suppress log output from the import code."""
        self.patch(boot_resources, 'maaslog')

    def make_args(self, sources="", **kwargs):
        """Fake an `argumentparser` parse result."""
        args = mock.Mock()
        # Set sources explicitly, otherwise boot_resources.main() gets
        # confused.
        args.sources = sources
        for key, value in kwargs.items():
            setattr(args, key, value)
        return args

    def make_simplestreams_index(self, index_dir, stream, product):
        """Write a fake simplestreams index file.  Return its path."""
        index_file = os.path.join(index_dir, 'index.json')
        index = {
            'format': 'index:1.0',
            'updated': 'Tue, 25 Mar 2014 16:19:49 +0000',
            'index': {
                stream: {
                    'datatype': 'image-ids',
                    'path': 'streams/v1/%s.json' % stream,
                    'updated': 'Tue, 25 Mar 2014 16:19:49 +0000',
                    'format': 'products:1.0',
                    'products': [product],
                    },
                },
            }
        write_text_file(index_file, json.dumps(index))
        return index_file

    def make_download_file(self, repo, image_spec, version,
                           filename='boot-kernel'):
        """Fake a downloadable file in `repo`.

        Return the new file's POSIX path, and its contents.
        """
        path = [
            image_spec.release,
            image_spec.arch,
            version,
            image_spec.release,
            image_spec.subarch,
            filename,
            ]
        native_path = os.path.join(repo, *path)
        os.makedirs(os.path.dirname(native_path))
        contents = ("Contents: %s" % filename).encode('utf-8')
        write_text_file(native_path, contents)
        # Return POSIX path for inclusion in Simplestreams data, not
        # system-native path for filesystem access.
        return '/'.join(path), contents

    def make_simplestreams_product_index(self, index_dir, stream, product,
                                         image_spec, os_release,
                                         download_file, contents, version):
        """Write a fake Simplestreams product index file.

        The image is written into the directory that holds the indexes.  It
        contains one downloadable file, as specified by the arguments.
        """
        index = {
            'format': 'products:1.0',
            'data-type': 'image-ids',
            'updated': 'Tue, 25 Mar 2014 16:19:49 +0000',
            'content_id': stream,
            'products': {
                product: {
                    'versions': {
                        version: {
                            'items': {
                                'boot-kernel': {
                                    'ftype': 'boot-kernel',
                                    '_fake': 'fake-data: %s' % download_file,
                                    'version': os_release,
                                    'release': image_spec.release,
                                    'path': download_file,
                                    'sha256': checksum_sha256(contents),
                                    'arch': image_spec.arch,
                                    'subarches': image_spec.subarch,
                                    'size': len(contents),
                                },
                            },
                        },
                    },
                    'subarch': image_spec.subarch,
                    'krel': image_spec.release,
                    'label': image_spec.label,
                    'kflavor': image_spec.subarch,
                    'version': os_release,
                    'subarches': [image_spec.subarch],
                    'release': image_spec.release,
                    'arch': image_spec.arch,
                    'os': image_spec.os,
                },
            },
        }
        write_text_file(
            os.path.join(index_dir, '%s.json' % stream),
            json.dumps(index))

    def make_simplestreams_repo(self, image_spec):
        """Fake a local simplestreams repository containing the given image.

        This creates a temporary directory that looks like a realistic
        Simplestreams repository, containing one downloadable file for the
        given `image_spec`.
        """
        os_release = '%d.%.2s' % (
            randint(1, 99),
            ('04' if randint(0, 1) == 0 else '10'),
            )
        repo = self.make_dir()
        index_dir = os.path.join(repo, 'streams', 'v1')
        os.makedirs(index_dir)
        stream = 'com.ubuntu.maas:daily:v2:download'
        product = 'com.ubuntu.maas:boot:%s:%s:%s' % (
            os_release,
            image_spec.arch,
            image_spec.subarch,
            )
        version = '20140317'
        download_file, sha = self.make_download_file(repo, image_spec, version)
        self.make_simplestreams_product_index(
            index_dir, stream, product, image_spec, os_release, download_file,
            sha, version)
        index = self.make_simplestreams_index(index_dir, stream, product)
        return index

    def make_working_args(self):
        """Create a set of working arguments for the script."""
        # Prepare a fake repository and sources.
        sources = [
            {
                'url': self.repo,
                'selections': [
                    {
                        'os': self.os,
                        'release': self.release,
                        'arches': [self.arch],
                        'subarches': [self.subarch],
                        'labels': [self.label],
                    },
                    ],
            },
            ]
        sources_file = self.make_file(
            'sources.yaml', contents=yaml.safe_dump(sources))
        return self.make_args(sources_file=sources_file)

    def test_successful_run(self):
        """Integration-test a successful run of the importer.

        This runs as much realistic code as it can, exercising most of the
        integration points for a real import.
        """
        # Patch out things that we don't want running during the test.  Patch
        # at a low level, so that we exercise all the function calls that a
        # unit test might not put to the test.
        self.patch_maaslog()
        self.patch(boot_resources, 'call_and_check')

        # We'll go through installation of a PXE boot loader here, but skip
        # all other boot loader types.  Testing them all is a job for proper
        # unit tests.
        for method_name, boot_method in BootMethodRegistry:
            if method_name != 'pxe':
                self.patch(boot_method, 'install_bootloader')

        args = self.make_working_args()
        osystem = self.os
        arch = self.arch
        subarch = self.subarch
        release = self.release
        label = self.label

        # Run the import code.
        boot_resources.main(args)

        # Verify the reuslts.
        self.assertThat(os.path.join(self.storage, 'cache'), DirExists())
        current = os.path.join(self.storage, 'current')
        self.assertTrue(os.path.islink(current))
        self.assertThat(current, DirExists())
        self.assertThat(os.path.join(current, 'pxelinux.0'), FileExists())
        self.assertThat(os.path.join(current, 'maas.meta'), FileExists())
        self.assertThat(os.path.join(current, 'maas.tgt'), FileExists())
        self.assertThat(
            os.path.join(
                current, osystem, arch, subarch, self.release, self.label),
            DirExists())

        # Verify the contents of the "meta" file.
        with open(os.path.join(current, 'maas.meta'), 'rb') as meta_file:
            meta_data = json.load(meta_file)
        self.assertEqual([osystem], meta_data.keys())
        self.assertEqual([arch], meta_data[osystem].keys())
        self.assertEqual([subarch], meta_data[osystem][arch].keys())
        self.assertEqual([release], meta_data[osystem][arch][subarch].keys())
        self.assertEqual(
            [label], meta_data[osystem][arch][subarch][release].keys())
        self.assertItemsEqual(
            [
                'content_id',
                'path',
                'product_name',
                'version_name',
                'subarches',
            ],
            meta_data[osystem][arch][subarch][release][label].keys())

    def test_warns_if_no_sources_selected(self):
        self.patch_maaslog()
        sources_fixture = self.useFixture(BootSourcesFixture([]))
        args = self.make_args(sources_file=sources_fixture.filename)

        boot_resources.main(args)

        self.assertThat(
            boot_resources.maaslog.warn,
            MockAnyCall("Can't import: region did not provide a source."))

    def test_warns_if_no_boot_resources_found(self):
        # The import code used to crash when no resources were found in the
        # Simplestreams repositories (bug 1305758).  This could happen easily
        # with mistakes in the sources.  Now, you just get a logged warning.
        sources_fixture = self.useFixture(BootSourcesFixture(
            [
                {
                    'url': self.make_dir(),
                    'keyring': factory.make_name('keyring'),
                    'selections': [{'release': factory.make_name('release')}],
                },
            ]))
        self.patch(boot_resources, 'download_all_image_descriptions')
        boot_resources.download_all_image_descriptions.return_value = (
            BootImageMapping())
        self.patch_maaslog()
        self.patch(boot_resources, 'RepoWriter')
        args = self.make_args(sources_file=sources_fixture.filename)

        boot_resources.main(args)

        self.assertThat(
            boot_resources.maaslog.warn,
            MockAnyCall(
                "Finished importing boot images, the region does not have "
                "any boot images available."))

    def test_raises_ioerror_when_no_sources_file_found(self):
        self.patch_maaslog()
        no_sources = os.path.join(
            self.make_dir(), '%s.yaml' % factory.make_name('no-sources'))
        self.assertRaises(
            boot_resources.NoConfigFile,
            boot_resources.main, self.make_args(sources_file=no_sources))

    def test_raises_non_ENOENT_IOErrors(self):
        # main() will raise a NoConfigFile error when it encounters an
        # ENOENT IOError, but will otherwise just re-raise the original
        # IOError.
        mock_load = self.patch(BootSources, 'load')
        other_error = IOError(randint(errno.ENOENT + 1, 1000))
        mock_load.side_effect = other_error
        self.patch_maaslog()
        raised_error = self.assertRaises(
            IOError,
            boot_resources.main, self.make_args())
        self.assertEqual(other_error, raised_error)

    def test_raises_error_when_no_sources_passed(self):
        # main() raises an error when neither a sources file nor a sources
        # listing is specified.
        self.patch_maaslog()
        self.assertRaises(
            boot_resources.NoConfigFile,
            boot_resources.main, self.make_args(sources="", sources_file=""))


class TestMetaContains(MAASTestCase):
    """Tests for the `meta_contains` function."""

    def make_meta_file(self, content=None):
        if content is None:
            content = factory.make_string()
        storage = self.make_dir()
        current = os.path.join(storage, 'current')
        os.mkdir(current)
        return storage, factory.make_file(current, 'maas.meta', content)

    def test_matching_content_is_compared_True(self):
        content = factory.make_string()
        storage, meta_file = self.make_meta_file(content)
        self.assertTrue(boot_resources.meta_contains(storage, content))

    def test_mismatching_content_is_compared_False(self):
        content = factory.make_string()
        storage, meta_file = self.make_meta_file()
        self.assertFalse(boot_resources.meta_contains(storage, content))

    def test_meta_contains_updates_file_timestamp(self):
        content = factory.make_string()
        storage, meta_file = self.make_meta_file(content)

        # Change the file's timestamp to a week ago.
        one_week_ago = timedelta(weeks=1).total_seconds()
        age_file(meta_file, one_week_ago)

        boot_resources.meta_contains(storage, content)

        # Check the timestamp was updated.
        expected_date = datetime.now()
        actual_date = datetime.fromtimestamp(int(os.path.getmtime(meta_file)))
        self.assertEqual(expected_date.day, actual_date.day)


class TestParseSources(MAASTestCase):
    """Tests for the `parse_sources` function."""

    def test_parses_sources(self):
        self.patch(boot_resources, 'maaslog')
        sources = [
            {
                'keyring': factory.make_name("keyring"),
                'keyring_data': '',
                'url': factory.make_name("something"),
                'selections': [
                    {
                        'os': factory.make_name("os"),
                        'release': factory.make_name("release"),
                        'arches': [factory.make_name("arch")],
                        'subarches': [factory.make_name("subarch")],
                        'labels': [factory.make_name("label")],
                    },
                    ],
            },
            ]
        parsed_sources = boot_resources.parse_sources(yaml.safe_dump(sources))
        self.assertEqual(sources, parsed_sources)


class TestImportImages(MAASTestCase):
    """Tests for the `import_images`() function."""

    def test_writes_source_keyrings(self):
        # Stop import_images() from actually doing anything.
        self.patch(boot_resources, 'maaslog')
        self.patch(boot_resources, 'call_and_check')
        self.patch(boot_resources, 'download_all_boot_resources')
        self.patch(boot_resources, 'download_all_image_descriptions')
        self.patch(boot_resources, 'install_boot_loaders')
        self.patch(boot_resources, 'update_current_symlink')
        self.patch(boot_resources, 'write_snapshot_metadata')
        self.patch(boot_resources, 'write_targets_conf')
        self.patch(boot_resources, 'update_targets_conf')

        fake_write_all_keyrings = self.patch(
            boot_resources, 'write_all_keyrings')
        sources = [
            {
                'keyring_data': self.getUniqueString(),
                'url': factory.make_name("something"),
                'selections': [
                    {
                        'os': factory.make_name("os"),
                        'release': factory.make_name("release"),
                        'arches': [factory.make_name("arch")],
                        'subarches': [factory.make_name("subarch")],
                        'labels': [factory.make_name("label")],
                    },
                    ],
            },
            ],
        boot_resources.import_images(sources)
        self.assertThat(
            fake_write_all_keyrings, MockCalledWith(mock.ANY, sources))

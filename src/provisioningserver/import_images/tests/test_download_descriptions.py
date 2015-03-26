# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the `download_descriptions` module."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from maastesting.factory import factory
from maastesting.testcase import MAASTestCase
from mock import sentinel
from provisioningserver.import_images import download_descriptions
from provisioningserver.import_images.boot_image_mapping import (
    BootImageMapping,
)
from provisioningserver.import_images.download_descriptions import (
    clean_up_repo_item,
    RepoDumper,
)
from provisioningserver.import_images.testing.factory import (
    make_image_spec,
    set_resource,
)


class TestValuePassesFilterList(MAASTestCase):
    """Tests for `value_passes_filter_list`."""

    def test_nothing_passes_empty_list(self):
        self.assertFalse(
            download_descriptions.value_passes_filter_list(
                [], factory.make_name('value')))

    def test_unmatched_value_does_not_pass(self):
        self.assertFalse(
            download_descriptions.value_passes_filter_list(
                [factory.make_name('filter')], factory.make_name('value')))

    def test_matched_value_passes(self):
        value = factory.make_name('value')
        self.assertTrue(
            download_descriptions.value_passes_filter_list([value], value))

    def test_value_passes_if_matched_anywhere_in_filter(self):
        value = factory.make_name('value')
        self.assertTrue(
            download_descriptions.value_passes_filter_list(
                [
                    factory.make_name('filter'),
                    value,
                    factory.make_name('filter'),
                ],
                value))

    def test_any_value_passes_asterisk(self):
        self.assertTrue(
            download_descriptions.value_passes_filter_list(
                ['*'], factory.make_name('value')))


class TestValuePassesFilter(MAASTestCase):
    """Tests for `value_passes_filter`."""

    def test_unmatched_value_does_not_pass(self):
        self.assertFalse(
            download_descriptions.value_passes_filter(
                factory.make_name('filter'), factory.make_name('value')))

    def test_matching_value_passes(self):
        value = factory.make_name('value')
        self.assertTrue(
            download_descriptions.value_passes_filter(value, value))

    def test_any_value_matches_asterisk(self):
        self.assertTrue(
            download_descriptions.value_passes_filter(
                '*', factory.make_name('value')))


class TestImagePassesFilter(MAASTestCase):
    """Tests for `image_passes_filter`."""

    def make_filter_from_image(self, image_spec=None):
        """Create a filter dict that matches the given `ImageSpec`.

        If `image_spec` is not given, creates a random value.
        """
        if image_spec is None:
            image_spec = make_image_spec()
        return {
            'os': image_spec.os,
            'arches': [image_spec.arch],
            'subarches': [image_spec.subarch],
            'release': image_spec.release,
            'labels': [image_spec.label],
            }

    def test_any_image_passes_none_filter(self):
        os, arch, subarch, release, label = make_image_spec()
        self.assertTrue(
            download_descriptions.image_passes_filter(
                None, os, arch, subarch, release, label))

    def test_any_image_passes_empty_filter(self):
        os, arch, subarch, release, label = make_image_spec()
        self.assertTrue(
            download_descriptions.image_passes_filter(
                [], os, arch, subarch, release, label))

    def test_image_passes_matching_filter(self):
        image = make_image_spec()
        self.assertTrue(
            download_descriptions.image_passes_filter(
                [self.make_filter_from_image(image)],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_image_does_not_pass_nonmatching_filter(self):
        image = make_image_spec()
        self.assertFalse(
            download_descriptions.image_passes_filter(
                [self.make_filter_from_image()],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_image_passes_if_one_filter_matches(self):
        image = make_image_spec()
        self.assertTrue(
            download_descriptions.image_passes_filter(
                [
                    self.make_filter_from_image(),
                    self.make_filter_from_image(image),
                    self.make_filter_from_image(),
                ],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_filter_checks_release(self):
        image = make_image_spec()
        self.assertFalse(
            download_descriptions.image_passes_filter(
                [
                    self.make_filter_from_image(image._replace(
                        release=factory.make_name('other-release')))
                ],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_filter_checks_arches(self):
        image = make_image_spec()
        self.assertFalse(
            download_descriptions.image_passes_filter(
                [
                    self.make_filter_from_image(image._replace(
                        arch=factory.make_name('other-arch')))
                ],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_filter_checks_subarches(self):
        image = make_image_spec()
        self.assertFalse(
            download_descriptions.image_passes_filter(
                [
                    self.make_filter_from_image(image._replace(
                        subarch=factory.make_name('other-subarch')))
                ],
                image.os, image.arch, image.subarch,
                image.release, image.label))

    def test_filter_checks_labels(self):
        image = make_image_spec()
        self.assertFalse(
            download_descriptions.image_passes_filter(
                [
                    self.make_filter_from_image(image._replace(
                        label=factory.make_name('other-label')))
                ],
                image.os, image.arch, image.subarch,
                image.release, image.label))


class TestBootMerge(MAASTestCase):
    """Tests for `boot_merge`."""

    def test_integrates(self):
        # End-to-end scenario for boot_merge: start with an empty boot
        # resources dict, and receive one resource from Simplestreams.
        total_resources = BootImageMapping()
        resources_from_repo = set_resource()
        download_descriptions.boot_merge(total_resources, resources_from_repo)
        # Since we started with an empty dict, the result contains the same
        # item that we got from Simplestreams, and nothing else.
        self.assertEqual(resources_from_repo.mapping, total_resources.mapping)

    def test_obeys_filters(self):
        filters = [
            {
                'os': factory.make_name('os'),
                'arches': [factory.make_name('other-arch')],
                'subarches': [factory.make_name('other-subarch')],
                'release': factory.make_name('other-release'),
                'label': [factory.make_name('other-label')],
            },
            ]
        total_resources = BootImageMapping()
        resources_from_repo = set_resource()
        download_descriptions.boot_merge(
            total_resources, resources_from_repo, filters=filters)
        self.assertEqual({}, total_resources.mapping)

    def test_does_not_overwrite_existing_entry(self):
        image = make_image_spec()
        total_resources = set_resource(
            resource="Original resource", image_spec=image)
        original_resources = total_resources.mapping.copy()
        resources_from_repo = set_resource(
            resource="New resource", image_spec=image)
        download_descriptions.boot_merge(total_resources, resources_from_repo)
        self.assertEqual(original_resources, total_resources.mapping)


class TestRepoDumper(MAASTestCase):
    """Tests for `RepoDumper`."""

    def make_item(self, os=None, release=None, arch=None,
                  subarch=None, subarches=None, label=None):
        if os is None:
            os = factory.make_name('os')
        if release is None:
            release = factory.make_name('release')
        if arch is None:
            arch = factory.make_name('arch')
        if subarch is None:
            subarch = factory.make_name('subarch')
        if subarches is None:
            subarches = [factory.make_name('subarch') for _ in range(3)]
        if subarch not in subarches:
            subarches.append(subarch)
        if label is None:
            label = factory.make_name('label')
        item = {
            'content_id': factory.make_name('content_id'),
            'product_name': factory.make_name('product_name'),
            'version_name': factory.make_name('version_name'),
            'path': factory.make_name('path'),
            'os': os,
            'release': release,
            'arch': arch,
            'subarch': subarch,
            'subarches': ','.join(subarches),
            'label': label,
            }
        return item, clean_up_repo_item(item)

    def test_insert_item_adds_item_per_subarch(self):
        boot_images_dict = BootImageMapping()
        dumper = RepoDumper(boot_images_dict)
        subarches = [factory.make_name('subarch') for _ in range(3)]
        item, _ = self.make_item(
            subarch=subarches.pop(), subarches=subarches)
        self.patch(
            download_descriptions, 'products_exdata').return_value = item
        dumper.insert_item(
            sentinel.data, sentinel.src, sentinel.target,
            sentinel.pedigree, sentinel.contentsource)
        image_specs = [
            make_image_spec(
                os=item['os'], release=item['release'],
                arch=item['arch'], subarch=subarch,
                label=item['label'])
            for subarch in subarches
        ]
        self.assertItemsEqual(image_specs, boot_images_dict.mapping.keys())

    def test_insert_item_sets_compat_item_specific_to_subarch(self):
        boot_images_dict = BootImageMapping()
        dumper = RepoDumper(boot_images_dict)
        subarches = [factory.make_name('subarch') for _ in range(5)]
        compat_subarch = subarches.pop()
        item, _ = self.make_item(subarch=subarches.pop(), subarches=subarches)
        second_item, compat_item = self.make_item(
            os=item['os'], release=item['release'], arch=item['arch'],
            subarch=compat_subarch, subarches=[compat_subarch],
            label=item['label'])
        self.patch(
            download_descriptions,
            'products_exdata').side_effect = [item, second_item]
        for _ in range(2):
            dumper.insert_item(
                sentinel.data, sentinel.src, sentinel.target,
                sentinel.pedigree, sentinel.contentsource)
        image_spec = make_image_spec(
            os=item['os'], release=item['release'],
            arch=item['arch'], subarch=compat_subarch,
            label=item['label'])
        self.assertEqual(compat_item, boot_images_dict.mapping[image_spec])

    def test_insert_item_sets_generic_to_release_item_for_hwe(self):
        boot_images_dict = BootImageMapping()
        dumper = RepoDumper(boot_images_dict)
        os = 'ubuntu'
        release = 'precise'
        arch = 'amd64'
        label = 'release'
        hwep_subarch = 'hwe-p'
        hwep_subarches = ['generic', 'hwe-p']
        hwes_subarch = 'hwe-s'
        hwes_subarches = ['generic', 'hwe-p', 'hwe-s']
        hwep_item, compat_item = self.make_item(
            os=os, release=release,
            arch=arch, subarch=hwep_subarch,
            subarches=hwep_subarches, label=label)
        hwes_item, _ = self.make_item(
            os=os, release=release,
            arch=arch, subarch=hwes_subarch,
            subarches=hwes_subarches, label=label)
        self.patch(
            download_descriptions,
            'products_exdata').side_effect = [hwep_item, hwes_item]
        for _ in range(2):
            dumper.insert_item(
                sentinel.data, sentinel.src, sentinel.target,
                sentinel.pedigree, sentinel.contentsource)
        image_spec = make_image_spec(
            os=os, release=release, arch=arch, subarch='generic',
            label=label)
        self.assertEqual(compat_item, boot_images_dict.mapping[image_spec])

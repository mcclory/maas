# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__all__ = []

import copy
from datetime import timedelta
import random

from maasserver.models import VersionedTextFile
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.orm import reload_object
from metadataserver.builtin_scripts import (
    BUILTIN_SCRIPTS,
    load_builtin_scripts,
)
from metadataserver.enum import SCRIPT_TYPE_CHOICES
from metadataserver.models import Script
from provisioningserver.utils.version import get_maas_version


class TestBuiltinScripts(MAASServerTestCase):
    """Test that builtin scripts get properly added and updated."""

    def test_creates_scripts(self):
        load_builtin_scripts()

        for script in BUILTIN_SCRIPTS:
            script_in_db = Script.objects.get(name=script.name)
            self.assertEquals(script.title, script_in_db.title)
            self.assertEquals(script.description, script_in_db.description)
            self.assertEquals(
                "Created by maas-%s" % get_maas_version(),
                script_in_db.script.comment)
            self.assertItemsEqual(script.tags, script_in_db.tags)
            self.assertEquals(script.script_type, script_in_db.script_type)
            self.assertEquals(script.timeout, script_in_db.timeout)
            self.assertEquals(script.destructive, script_in_db.destructive)
            self.assertTrue(script_in_db.default)

    def test_update_script(self):
        load_builtin_scripts()
        update_script_values = random.choice(BUILTIN_SCRIPTS)
        script = Script.objects.get(name=update_script_values.name)
        # Fields which we can update
        script.description = factory.make_string()
        script.script_type = factory.pick_choice(SCRIPT_TYPE_CHOICES)
        script.destructive = not script.destructive
        # Put fake old data in to simulate updating a script.
        old_script = VersionedTextFile.objects.create(
            data=factory.make_string())
        script.script = old_script
        # User changeable fields.
        user_tags = [factory.make_name('tag') for _ in range(3)]
        script.tags = copy.deepcopy(user_tags)
        user_timeout = timedelta(random.randint(0, 1000))
        script.timeout = user_timeout
        script.save()

        load_builtin_scripts()
        script = reload_object(script)

        self.assertEquals(update_script_values.name, script.name)
        self.assertEquals(update_script_values.title, script.title)
        self.assertEquals(
            update_script_values.description, script.description)
        self.assertEquals(
            "Updated by maas-%s" % get_maas_version(),
            script.script.comment)
        self.assertEquals(
            update_script_values.script_type, script.script_type)
        self.assertEquals(
            update_script_values.destructive, script.destructive)
        self.assertEquals(old_script, script.script.previous_version)
        if script.destructive:
            user_tags.append('destructive')
        self.assertEquals(user_tags, script.tags)
        self.assertEquals(user_timeout, script.timeout)
        self.assertTrue(script.default)

    def test_update_doesnt_revert_script(self):
        load_builtin_scripts()
        update_script_index = random.randint(0, len(BUILTIN_SCRIPTS) - 2)
        update_script_values = BUILTIN_SCRIPTS[update_script_index]
        script = Script.objects.get(name=update_script_values.name)
        # Put fake new data in to simulate another MAAS region updating
        # to a newer version.
        updated_description = factory.make_name('description')
        script.description = updated_description
        new_script = factory.make_string()
        script.script = script.script.update(new_script)
        updated_script_type = factory.pick_choice(SCRIPT_TYPE_CHOICES)
        script.script_type = updated_script_type
        updated_destructive = not script.destructive
        script.destructive = updated_destructive

        # Fake user updates
        user_tags = [factory.make_name('tag') for _ in range(3)]
        script.tags = user_tags
        user_timeout = timedelta(random.randint(0, 1000))
        script.timeout = user_timeout
        script.save()

        # Test that subsequent scripts still get updated
        second_update_script_values = BUILTIN_SCRIPTS[update_script_index + 1]
        second_script = Script.objects.get(
            name=second_update_script_values.name)
        # Put fake old data in to simulate updating a script.
        second_script.title = factory.make_string()
        second_script.save()

        load_builtin_scripts()
        script = reload_object(script)

        self.assertEquals(update_script_values.name, script.name)
        self.assertEquals(update_script_values.title, script.title)
        self.assertEquals(updated_description, script.description)
        self.assertEquals(updated_script_type, script.script_type)
        self.assertEquals(updated_destructive, script.destructive)
        self.assertEquals(new_script, script.script.data)
        self.assertEquals(user_tags, script.tags)
        self.assertEquals(user_timeout, script.timeout)
        self.assertTrue(script.default)

        second_script = reload_object(second_script)
        self.assertEquals(
            second_update_script_values.title, second_script.title)

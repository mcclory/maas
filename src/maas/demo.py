# Copyright 2012-2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Django DEMO settings for maas project."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type

from os.path import abspath

from maas import (
    development,
    import_settings,
    settings,
)

# We expect the following settings to be overridden. They are mentioned here
# to silence lint warnings.
MIDDLEWARE_CLASSES = None

# Extend base and development settings.
import_settings(settings)
import_settings(development)

MEDIA_ROOT = abspath("media/demo")

MIDDLEWARE_CLASSES += (
    'debug_toolbar.middleware.DebugToolbarMiddleware',
)

# Connect to the DNS server.
DNS_CONNECT = True

DHCP_CONNECT = True

MAAS_CLI = abspath("bin/maas-region-admin")

# Use the in-branch development version of maas_cluster.conf.
LOCAL_CLUSTER_CONFIG = abspath("etc/demo_maas_cluster.conf")

# For demo purposes, give nodes unauthenticated access to their metadata
# even if we can't pass boot parameters.  This is not safe; do not
# enable it on a production MAAS.
ALLOW_UNSAFE_METADATA_ACCESS = True

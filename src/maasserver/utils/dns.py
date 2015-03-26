# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""DNS-related utilities."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )
import re

from django.core.exceptions import ValidationError
from netaddr import (
    AddrConversionError,
    IPAddress,
)


str = None

__metaclass__ = type
__all__ = [
    'validate_hostname',
    ]


def validate_hostname(hostname):
    """Validator for hostnames.

    :param hostname: Input value for a host name.  May include domain.
    :raise ValidationError: If the hostname is not valid according to RFCs 952
        and 1123.
    """
    # Valid characters within a hostname label: ASCII letters, ASCII digits,
    # hyphens, and underscores.  Not all are always valid.
    # Technically we could write all of this as a single regex, but it's not
    # very good for code maintenance.
    label_chars = re.compile('[a-zA-Z0-9_-]*$')

    if len(hostname) > 255:
        raise ValidationError(
            "Hostname is too long.  Maximum allowed is 255 characters.")
    # A hostname consists of "labels" separated by dots.
    labels = hostname.split('.')
    if '_' in labels[0]:
        # The host label cannot contain underscores; the rest of the name can.
        raise ValidationError(
            "Host label cannot contain underscore: %r." % labels[0])
    for label in labels:
        if len(label) == 0:
            raise ValidationError("Hostname contains empty name.")
        if len(label) > 63:
            raise ValidationError(
                "Name is too long: %r.  Maximum allowed is 63 characters."
                % label)
        if label.startswith('-') or label.endswith('-'):
            raise ValidationError(
                "Name cannot start or end with hyphen: %r." % label)
        if not label_chars.match(label):
            raise ValidationError(
                "Name contains disallowed characters: %r." % label)


def get_ip_based_hostname(ip):
    """Given the specified IP address (which must be suitable to convert to
    a netaddr.IPAddress), creates an automatically generated hostname by
    converting the '.' or ':' characters in it to '-' characters.

    For IPv6 address which represent an IPv4-compatible or IPv4-mapped
    address, the IPv4 representation will be used.

    :param ip: The IPv4 or IPv6 address (can be an integer or string)
    """
    try:
        hostname = unicode(IPAddress(ip).ipv4()).replace('.', '-')
    except AddrConversionError:
        hostname = unicode(IPAddress(ip).ipv6()).replace(':', '-')
    return hostname

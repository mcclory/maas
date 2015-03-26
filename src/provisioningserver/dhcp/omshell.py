# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Python wrapper around the `omshell` utility which amends objects
inside the DHCP server.
"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    "generate_omapi_key",
    "Omshell",
    ]

import os
import re
from subprocess import (
    PIPE,
    Popen,
)
from textwrap import dedent

from provisioningserver.utils import parse_key_value_file
from provisioningserver.utils.fs import tempdir
from provisioningserver.utils.shell import (
    call_and_check,
    ExternalProcessError,
)


bad_key_pattern = re.compile("[+/]no|no[+/]", flags=re.IGNORECASE)


def call_dnssec_keygen(tmpdir):
    path = os.environ.get("PATH", "").split(os.pathsep)
    path.append("/usr/sbin")
    env = dict(os.environ, PATH=os.pathsep.join(path))
    return call_and_check(
        ['dnssec-keygen', '-r', '/dev/urandom', '-a', 'HMAC-MD5',
         '-b', '512', '-n', 'HOST', '-K', tmpdir, '-q', 'omapi_key'],
        env=env)


def run_repeated_keygen(tmpdir):
    # omshell has a bug where if the chars '/' or '+' appear either
    # side of the word 'no' (in any case), it throws an error like
    # "partial base64 value left over".  We check for that here and
    # repeatedly generate a new key until a good one is generated.

    key = None
    while key is None:
        key_id = call_dnssec_keygen(tmpdir)

        # Locate the file that was written and strip out the Key: field in
        # it.
        if not key_id:
            raise AssertionError("dnssec-keygen didn't generate anything")
        key_id = key_id.strip()  # Remove trailing newline.
        key_file_name = os.path.join(tmpdir, key_id + '.private')
        parsing_error = False
        try:
            config = parse_key_value_file(key_file_name)
        except ValueError:
            parsing_error = True
        if parsing_error or 'Key' not in config:
            raise AssertionError(
                "Key field not found in output from dnssec-keygen")

        key = config['Key']
        if bad_key_pattern.search(key) is not None:
            # Force a retry.
            os.remove(key_file_name)  # Stop dnssec_keygen complaints.
            key = None

    return key


def generate_omapi_key():
    """Generate a HMAC-MD5 key by calling out to the dnssec-keygen tool.

    :return: The shared key suitable for OMAPI access.
    :type: string
    """
    # dnssec-keygen writes out files to a specified directory, so we
    # need to make a temp directory for that.
    # This relies on the temporary directory being accessible only to its
    # owner.
    temp_prefix = "%s." % os.path.basename(__file__)
    with tempdir(prefix=temp_prefix) as tmpdir:
        key = run_repeated_keygen(tmpdir)
        return key


class Omshell:
    """Wrap up the omshell utility in Python.

    'omshell' is an external executable that communicates with a DHCP daemon
    and manipulates its objects.  This class wraps up the commands necessary
    to add and remove host maps (MAC to IP).

    :param server_address: The address for the DHCP server (ip or hostname)
    :param shared_key: An HMAC-MD5 key generated by dnssec-keygen like:
        $ dnssec-keygen -r /dev/urandom -a HMAC-MD5 -b 512 -n HOST omapi_key
        $ cat Komapi_key.+*.private |grep ^Key|cut -d ' ' -f2-
        It must match the key set in the DHCP server's config which looks
        like this:

        omapi-port 7911;
        key omapi_key {
            algorithm HMAC-MD5;
            secret "XXXXXXXXX"; #<-The output from the generated key above.
        };
        omapi-key omapi_key;
    """

    def __init__(self, server_address, shared_key):
        self.server_address = server_address
        self.shared_key = shared_key
        self.command = ["omshell"]

    def _run(self, stdin):
        proc = Popen(self.command, stdin=PIPE, stdout=PIPE)
        stdout, stderr = proc.communicate(stdin)
        if proc.poll() != 0:
            raise ExternalProcessError(proc.returncode, self.command, stdout)
        return proc.returncode, stdout

    def create(self, ip_address, mac_address):
        # The "name" is not a host name; it's an identifier used within
        # the DHCP server.  We just happen to use the IP address.
        stdin = dedent("""\
            server {self.server_address}
            key omapi_key {self.shared_key}
            connect
            new host
            set ip-address = {ip_address}
            set hardware-address = {mac_address}
            set hardware-type = 1
            set name = "{ip_address}"
            create
            """)
        stdin = stdin.format(
            self=self, ip_address=ip_address, mac_address=mac_address)

        returncode, output = self._run(stdin)
        # If the call to omshell doesn't result in output containing the
        # magic string 'hardware-type' then we can be reasonably sure
        # that the 'create' command failed.  Unfortunately there's no
        # other output like "successful" to check so this is the best we
        # can do.
        if "hardware-type" in output:
            # Success.
            pass
        elif "can't open object: I/O error" in output:
            # Host map already existed.  Treat as success.
            pass
        else:
            raise ExternalProcessError(returncode, self.command, output)

    def remove(self, ip_address):
        # The "name" is not a host name; it's an identifier used within
        # the DHCP server.  We just happen to use the IP address.
        stdin = dedent("""\
            server {self.server_address}
            key omapi_key {self.shared_key}
            connect
            new host
            set name = "{ip_address}"
            open
            remove
            """)
        stdin = stdin.format(
            self=self, ip_address=ip_address)

        returncode, output = self._run(stdin)

        # If the omshell worked, the last line should reference a null
        # object.  We need to strip blanks, newlines and '>' characters
        # for this to work.
        lines = output.strip('\n >').splitlines()
        try:
            last_line = lines[-1]
        except IndexError:
            last_line = ""
        if "obj: <null" in last_line:
            # Success.
            pass
        elif "can't open object: not found" in output:
            # It was already removed. Consider success.
            pass
        else:
            raise ExternalProcessError(returncode, self.command, output)

    def nullify_lease(self, ip_address):
        """Reset an existing lease so it's no longer valid.

        You can't delete leases with omshell, so we're setting the expiry
        timestamp to the epoch instead.
        """
        stdin = dedent("""\
            server {self.server_address}
            key omapi_key {self.shared_key}
            connect
            new lease
            set ip-address = {ip_address}
            open
            set ends = 00:00:00:00
            update
            """)
        stdin = stdin.format(
            self=self, ip_address=ip_address)

        returncode, output = self._run(stdin)

        if "can't open object: not found" in output:
            # Consider nonexistent leases a success.
            return None

        # Catching "invalid" is a bit like catching a bare exception
        # but omshell is so esoteric that this is probably quite safe.
        # If the update succeeded, "ends = 00:00:00:00" will most certainly
        # be in the output.  If it's not, there's been a failure.
        if "invalid" not in output and "\nends = 00:00:00:00" in output:
            return None

        raise ExternalProcessError(returncode, self.command, output)

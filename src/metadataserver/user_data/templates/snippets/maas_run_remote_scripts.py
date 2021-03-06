#!/usr/bin/env python3
#
# maas-run-remote-scripts - Download a set of scripts from the MAAS region,
#                           execute them, and send the results back.
#
# Author: Lee Trager <lee.trager@canonical.com>
#
# Copyright (C) 2017 Canonical
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import copy
from datetime import timedelta
from io import BytesIO
import json
import os
from subprocess import (
    PIPE,
    Popen,
    TimeoutExpired,
)
import sys
import tarfile
from threading import (
    Event,
    Thread,
)
import time


try:
    from maas_api_helper import (
        geturl,
        MD_VERSION,
        read_config,
        signal,
        SignalException,
        capture_script_output,
    )
except ImportError:
    # For running unit tests.
    from snippets.maas_api_helper import (
        geturl,
        MD_VERSION,
        read_config,
        signal,
        SignalException,
        capture_script_output,
    )


def fail(msg):
    sys.stderr.write("FAIL: %s" % msg)
    sys.exit(1)


def signal_wrapper(*args, **kwargs):
    """Wrapper to output any SignalExceptions to STDERR."""
    try:
        signal(*args, **kwargs)
    except SignalException as e:
        fail(e.error)


def download_and_extract_tar(url, creds, scripts_dir):
    """Download and extract a tar from the given URL.

    The URL may contain a compressed or uncompressed tar.
    """
    binary = BytesIO(geturl(url, creds))

    with tarfile.open(mode='r|*', fileobj=binary) as tar:
        tar.extractall(scripts_dir)


def run_scripts(url, creds, scripts_dir, out_dir, scripts):
    """Run and report results for the given scripts."""
    total_scripts = len(scripts)
    fail_count = 0
    base_args = {
        'url': url,
        'creds': creds,
        'status': 'WORKING',
    }
    for i, script in enumerate(scripts):
        i += 1
        args = copy.deepcopy(base_args)
        args['script_result_id'] = script['script_result_id']
        script_version_id = script.get('script_version_id')
        if script_version_id is not None:
            args['script_version_id'] = script_version_id
        timeout_seconds = script.get('timeout_seconds')

        signal_wrapper(
            error='Starting %s [%d/%d]' % (
                script['name'], i, len(scripts)),
            **args)

        script_path = os.path.join(scripts_dir, script['path'])
        combined_path = os.path.join(out_dir, script['name'])
        stdout_name = '%s.out' % script['name']
        stdout_path = os.path.join(out_dir, stdout_name)
        stderr_name = '%s.err' % script['name']
        stderr_path = os.path.join(out_dir, stderr_name)

        try:
            # This script sets its own niceness value to the highest(-20) below
            # to help ensure the heartbeat keeps running. When launching the
            # script we need to lower the nice value as a child process
            # inherits the parent processes niceness value. preexec_fn is
            # executed in the child process before the command is run. When
            # setting the nice value the kernel adds the current nice value
            # to the provided value. Since the runner uses a nice value of -20
            # setting it to 40 gives the actual nice value of 20.
            proc = Popen(
                script_path, stdout=PIPE, stderr=PIPE,
                preexec_fn=lambda: os.nice(40))
            capture_script_output(
                proc, combined_path, stdout_path, stderr_path, timeout_seconds)
        except OSError as e:
            fail_count += 1
            if isinstance(e.errno, int) and e.errno != 0:
                args['exit_status'] = e.errno
            else:
                # 2 is the return code bash gives when it can't execute.
                args['exit_status'] = 2
            result = str(e).encode()
            if result == b'':
                result = b'Unable to execute script'
            args['files'] = {
                script['name']: result,
                stderr_name: result,
            }
            signal_wrapper(
                error='Failed to execute %s [%d/%d]: %d' % (
                    script['name'], i, total_scripts, args['exit_status']),
                **args)
        except TimeoutExpired:
            fail_count += 1
            args['status'] = 'TIMEDOUT'
            args['files'] = {
                script['name']: open(combined_path, 'rb').read(),
                stdout_name: open(stdout_path, 'rb').read(),
                stderr_name: open(stderr_path, 'rb').read(),
            }
            signal_wrapper(
                error='Timeout(%s) expired on %s [%d/%d]' % (
                    str(timedelta(seconds=timeout_seconds)), script['name'], i,
                    total_scripts),
                **args)
        else:
            if proc.returncode != 0:
                fail_count += 1
            args['exit_status'] = proc.returncode
            args['files'] = {
                script['name']: open(combined_path, 'rb').read(),
                stdout_name: open(stdout_path, 'rb').read(),
                stderr_name: open(stderr_path, 'rb').read(),
            }
            signal_wrapper(
                error='Finished %s [%d/%d]: %d' % (
                    script['name'], i, len(scripts), args['exit_status']),
                **args)

    # Signal failure after running commissioning or testing scripts so MAAS
    # transisitions the node into FAILED_COMMISSIONING or FAILED_TESTING.
    if fail_count != 0:
        signal_wrapper(
            url, creds, 'FAILED', '%d scripts failed to run' % fail_count)

    return fail_count


def run_scripts_from_metadata(url, creds, scripts_dir, out_dir):
    """Run all scripts from a tar given by MAAS."""
    with open(os.path.join(scripts_dir, 'index.json')) as f:
        scripts = json.load(f)['1.0']

    fail_count = 0
    commissioning_scripts = scripts.get('commissioning_scripts')
    if commissioning_scripts is not None:
        fail_count += run_scripts(
            url, creds, scripts_dir, out_dir, commissioning_scripts)
        if fail_count != 0:
            return

    testing_scripts = scripts.get('testing_scripts')
    if testing_scripts is not None:
        # If the node status was COMMISSIONING transition the node into TESTING
        # status. If the node is already in TESTING status this is ignored.
        signal_wrapper(url, creds, 'TESTING')
        fail_count += run_scripts(
            url, creds, scripts_dir, out_dir, testing_scripts)

    # Only signal OK when we're done with everything and nothing has failed.
    if fail_count == 0:
        signal_wrapper(url, creds, 'OK', 'All scripts successfully ran')


class HeartBeat(Thread):
    """Creates a background thread which pings the MAAS metadata service every
    two minutes to let it know we're still up and running scripts. If MAAS
    doesn't hear from us it will assume something has gone wrong and power off
    the node.
    """

    def __init__(self, url, creds):
        super().__init__(name='HeartBeat')
        self._url = url
        self._creds = creds
        self._run = Event()
        self._run.set()

    def stop(self):
        self._run.clear()

    def run(self):
        # Record the relative start time of the entire run.
        start = time.monotonic()
        tenths = 0
        while self._run.is_set():
            # Record the start of this heartbeat interval.
            heartbeat_start = time.monotonic()
            heartbeat_elapsed = 0
            total_elapsed = heartbeat_start - start
            args = [self._url, self._creds, 'WORKING']
            # Log the elapsed time plus the measured clock skew, if this
            # is the second run through the loop.
            if tenths > 0:
                args.append(
                    'Elapsed time (real): %d.%ds; Python: %d.%ds' % (
                        total_elapsed, total_elapsed % 1 * 10,
                        tenths // 10, tenths % 10))
            signal_wrapper(*args)
            # Spin for 2 minutes before sending another heartbeat.
            while heartbeat_elapsed < 120 and self._run.is_set():
                heartbeat_end = time.monotonic()
                heartbeat_elapsed = heartbeat_end - heartbeat_start
                # Wake up every tenth of a second to record clock skew and
                # ensure delayed scheduling doesn't impact the heartbeat.
                time.sleep(0.1)
                tenths += 1


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Download and run scripts from the MAAS metadata service.')
    parser.add_argument(
        "--config", metavar="file", help="Specify config file", default=None)
    parser.add_argument(
        "--ckey", metavar="key", help="The consumer key to auth with",
        default=None)
    parser.add_argument(
        "--tkey", metavar="key", help="The token key to auth with",
        default=None)
    parser.add_argument(
        "--csec", metavar="secret", help="The consumer secret (likely '')",
        default="")
    parser.add_argument(
        "--tsec", metavar="secret", help="The token secret to auth with",
        default=None)
    parser.add_argument(
        "--apiver", metavar="version",
        help="The apiver to use (\"\" can be used)", default=MD_VERSION)
    parser.add_argument(
        "--url", metavar="url", help="The data source to query", default=None)

    parser.add_argument(
        "storage_directory",
        help="Directory to store the extracted data from the metadata service."
    )

    args = parser.parse_args()

    creds = {
        'consumer_key': args.ckey,
        'token_key': args.tkey,
        'token_secret': args.tsec,
        'consumer_secret': args.csec,
        'metadata_url': args.url,
        }

    if args.config:
        read_config(args.config, creds)

    url = creds.get('metadata_url')
    if url is None:
        fail("URL must be provided either in --url or in config\n")
    url = "%s/%s/" % (url, args.apiver)

    # Disable the OOM killer on the runner process, the OOM killer will still
    # go after any tests spawned.
    oom_score_adj_path = os.path.join(
        '/proc', str(os.getpid()), 'oom_score_adj')
    open(oom_score_adj_path, 'w').write('-1000')
    # Give the runner the highest nice value to ensure the heartbeat keeps
    # running.
    os.nice(-20)

    heart_beat = HeartBeat(url, creds)
    heart_beat.start()

    scripts_dir = os.path.join(args.storage_directory, 'scripts')
    os.makedirs(scripts_dir)
    out_dir = os.path.join(args.storage_directory, 'out')
    os.makedirs(out_dir)

    download_and_extract_tar("%s/maas-scripts/" % url, creds, scripts_dir)
    run_scripts_from_metadata(url, creds, scripts_dir, out_dir)

    heart_beat.stop()


if __name__ == '__main__':
    main()

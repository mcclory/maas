# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `provisioningserver.drivers.hardware.vsphere`.
"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []


import random

from maastesting.factory import factory
from maastesting.testcase import (
    MAASTestCase,
    MAASTwistedRunTest,
)
from provisioningserver.drivers.hardware import vsphere
from provisioningserver.drivers.hardware.vsphere import VspherePyvmomi
from provisioningserver.utils.twisted import asynchronous
from testtools import ExpectedException
from testtools.matchers import (
    Equals,
    Is,
    IsInstance,
    Not,
)
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread


try:
    import pyVmomi
    import pyVim.connect as vmomi_api
except ImportError:
    pyVmomi = None
    vmomi_api = None


class FakeVmomiVMSummaryConfig(object):
    def __init__(self, name):
        self.name = name
        self.guestId = random.choice(["otherLinux64Guest", "otherLinuxGuest"])
        has_instance_uuid = random.choice([True, True, False])
        if has_instance_uuid:
            self.instanceUuid = factory.make_UUID()
        self.uuid = factory.make_UUID()


class FakeVmomiVMSummary(object):
    def __init__(self, name):
        self.config = FakeVmomiVMSummaryConfig(name)


class FakeVmomiVMRuntime(object):
    def __init__(self):
        # add an invalid power state into the mix
        self.powerState = random.choice(
            ["poweredOn",
             "poweredOff",
             "suspended",
             "warp9"])


class FakeVmomiVMConfigHardwareDevice(object):
    def __init__(self):
        pass


class FakeVmomiNic(FakeVmomiVMConfigHardwareDevice):
    def __init__(self):
        super(FakeVmomiNic, self).__init__()
        self.macAddress = factory.make_mac_address()


class FakeVmomiVMConfigHardware(object):
    def __init__(self, nics=None):
        self.device = []

        if nics is None:
            nics = random.choice([1, 1, 1, 2, 2, 3])

        for i in range(0, nics):
            self.device.append(FakeVmomiNic())

        # add a few random non-NICs into the mix
        for i in range(0, random.choice([0, 1, 3, 5, 15])):
            self.device.append(FakeVmomiVMConfigHardwareDevice())

        random.shuffle(self.device)


class FakeVmomiVMConfig(object):
    def __init__(self, nics=None):
        self.hardware = FakeVmomiVMConfigHardware(nics=nics)


class FakeVmomiVM(object):
    def __init__(self, name=None, nics=None):

        if name is None:
            self._name = factory.make_hostname()
        else:
            self._name = name

        self.summary = FakeVmomiVMSummary(self._name)
        self.runtime = FakeVmomiVMRuntime()
        self.config = FakeVmomiVMConfig(nics=nics)

    def PowerOn(self):
        self.runtime.powerState = "poweredOn"

    def PowerOff(self):
        self.runtime.powerState = "poweredOff"


class FakeVmomiVmFolder(object):
    def __init__(self, servers=0):
        self.childEntity = []
        for i in range(0, servers):
            vm = FakeVmomiVM()
            self.childEntity.append(vm)


class FakeVmomiDatacenter(object):
    def __init__(self, servers=0):
        self.vmFolder = FakeVmomiVmFolder(servers=servers)


class FakeVmomiRootFolder(object):
    def __init__(self, servers=0):
        self.childEntity = [FakeVmomiDatacenter(servers=servers)]


class FakeVmomiSearchIndex(object):
    def __init__(self, content):
        self.vms_by_instance_uuid = {}
        self.vms_by_uuid = {}

        for child in content.rootFolder.childEntity:
            if hasattr(child, 'vmFolder'):
                datacenter = child
                vm_folder = datacenter.vmFolder
                vm_list = vm_folder.childEntity
                for vm in vm_list:
                    if hasattr(vm.summary.config, 'instanceUuid') \
                            and vm.summary.config.instanceUuid is not None:
                        self.vms_by_instance_uuid[
                            vm.summary.config.instanceUuid] = vm
                    if hasattr(vm.summary.config, 'uuid')\
                            and vm.summary.config.uuid is not None:
                        self.vms_by_uuid[vm.summary.config.uuid] = vm

    def FindByUuid(self, datacenter, uuid, search_vms,
                   search_by_instance_uuid):
        assert datacenter is None
        assert uuid is not None
        assert search_vms is True
        if search_by_instance_uuid:
            if uuid not in self.vms_by_instance_uuid:
                return None
            return self.vms_by_instance_uuid[uuid]
        else:
            if uuid not in self.vms_by_uuid:
                return None
            return self.vms_by_uuid[uuid]


class FakeVmomiContent(object):
    def __init__(self, servers=0):
        self.rootFolder = FakeVmomiRootFolder(servers=servers)
        self.searchIndex = FakeVmomiSearchIndex(self)


class FakeVmomiServiceInstance(object):
    def __init__(self, servers=0):
        self.content = FakeVmomiContent(servers=servers)

    def RetrieveContent(self):
        return self.content


class TestVspherePyvmomi(MAASTestCase):
    """Tests for vSphere probe-and-enlist, and power query/control using
    the python-pyvmomi API."""

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def configure_vmomi_api(self, servers=10):
        mock_vmomi_api = self.patch(vsphere, 'vmomi_api')
        mock_vmomi_api.SmartConnect.return_value = FakeVmomiServiceInstance(
            servers=servers)
        return mock_vmomi_api

    def setUp(self):
        super(TestVspherePyvmomi, self).setUp()
        if vmomi_api is None:
            self.skipTest('cannot test vSphere without python-pyvmomi')

    def test_api_connection(self):
        mock_vmomi_api = self.configure_vmomi_api(servers=0)
        api = VspherePyvmomi(
            factory.make_hostname(),
            factory.make_username(),
            factory.make_username())
        api.connect()
        self.expectThat(
            api.service_instance,
            IsInstance(FakeVmomiServiceInstance))
        self.expectThat(api.is_connected(), Equals(True))
        api.disconnect()
        self.expectThat(mock_vmomi_api.SmartConnect.called, Equals(True))
        self.expectThat(mock_vmomi_api.Disconnect.called, Equals(True))

    def test_api_failed_connection(self):
        mock_vmomi_api = self.patch(vsphere, 'vmomi_api')
        mock_vmomi_api.SmartConnect.return_value = None
        api = VspherePyvmomi(
            factory.make_hostname(),
            factory.make_username(),
            factory.make_username())
        with ExpectedException(vsphere.VsphereError):
            api.connect()
        self.expectThat(api.service_instance, Is(None))
        self.expectThat(api.is_connected(), Equals(False))
        api.disconnect()
        self.expectThat(mock_vmomi_api.SmartConnect.called, Equals(True))
        self.expectThat(mock_vmomi_api.Disconnect.called, Equals(True))

    def test_get_vsphere_servers_empty(self):
        self.configure_vmomi_api(servers=0)
        servers = vsphere.get_vsphere_servers(
            factory.make_hostname(),
            factory.make_username(),
            factory.make_username(),
            port=8443, protocol='https')
        self.expectThat(servers, Equals({}))

    def test_get_vsphere_servers(self):
        self.configure_vmomi_api(servers=10)

        servers = vsphere.get_vsphere_servers(
            factory.make_hostname(),
            factory.make_username(),
            factory.make_username())
        self.expectThat(servers, Not(Equals({})))

    def test_power_control(self):
        mock_vmomi_api = self.configure_vmomi_api(servers=100)

        host = factory.make_hostname()
        username = factory.make_username()
        password = factory.make_username()

        servers = vsphere.get_vsphere_servers(host, username, password)

        # here we're grabbing indexes only available in the private mock object
        search_index = \
            mock_vmomi_api.SmartConnect.return_value.content.searchIndex

        bios_uuids = search_index.vms_by_uuid.keys()
        instance_uuids = search_index.vms_by_instance_uuid.keys()

        # at least one should have a randomly-invalid state (just checking
        # for coverage, but since it's random, don't want to assert)
        for uuid in bios_uuids:
            vsphere.power_query_vsphere(host, username, password, uuid)
        for uuid in instance_uuids:
            vsphere.power_query_vsphere(host, username, password, uuid)

        # turn on a set of VMs, then verify they are on
        for uuid in bios_uuids:
            vsphere.power_control_vsphere(host, username, password, uuid, "on")

        for uuid in bios_uuids:
            state = vsphere.power_query_vsphere(host, username, password, uuid)
            self.expectThat(state, Equals("on"))

        # turn off a set of VMs, then verify they are off
        for uuid in instance_uuids:
            vsphere.power_control_vsphere(host, username, password, uuid,
                                          "off")
        for uuid in instance_uuids:
            state = vsphere.power_query_vsphere(host, username, password, uuid)
            self.expectThat(state, Equals("off"))

        self.expectThat(servers, Not(Equals({})))

    @inlineCallbacks
    def test_probe_and_enlist(self):
        num_servers = 100
        self.configure_vmomi_api(servers=num_servers)
        mock_create_node = self.patch(vsphere, 'create_node')
        system_id = factory.make_name('system_id')
        mock_create_node.side_effect = asynchronous(
            lambda *args, **kwargs: system_id)
        mock_commission_node = self.patch(vsphere, 'commission_node')

        host = factory.make_hostname()
        username = factory.make_username()
        password = factory.make_username()

        yield deferToThread(
            vsphere.probe_vsphere_and_enlist,
            factory.make_username(),
            host,
            username,
            password,
            accept_all=True)

        self.expectThat(mock_create_node.call_count, Equals(num_servers))
        self.expectThat(mock_commission_node.call_count, Equals(num_servers))

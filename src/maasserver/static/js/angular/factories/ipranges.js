/* Copyright 2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS IPRange Manager
 *
 * Manages all of the IPRanges in the browser. The manager uses the
 * RegionConnection to load the IPRanges, update the IPRanges, and listen for
 * notification events about IPRanges.
 */

angular.module('MAAS').factory(
    'IPRangesManager',
    ['$q', '$rootScope', 'RegionConnection', 'Manager',
    function($q, $rootScope, RegionConnection, Manager) {

        function IPRangesManager() {
            Manager.call(this);

            this._pk = "id";
            this._handler = "iprange";

            // Listen for notify events for the iprange object.
            var self = this;
            RegionConnection.registerNotifier("iprange",
                function(action, data) {
                    self.onNotify(action, data);
                });
        }

        IPRangesManager.prototype = new Manager();

        // Delete the VLAN.
        IPRangesManager.prototype.deleteVLAN = function(iprange) {
            return RegionConnection.callMethod(
                "iprange.delete", { "id": vlan.id }, true);
        };

        return new IPRangesManager();
    }]);

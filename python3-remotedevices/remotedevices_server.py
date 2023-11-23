# fMBT, free Model Based Testing tool
# Copyright (c) Intel Corporation.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU Lesser General Public License,
# version 2.1, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for
# more details.
#
# You should have received a copy of the GNU Lesser General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St - Fifth Floor, Boston, MA 02110-1301 USA.

import collections
import glob
import re
import sys
import _thread
import time
import traceback
import types
import os
import random

from python3share.server import daemon_log

DeviceInfo = collections.namedtuple("DeviceInfo", ["id", "type", "sw", "hw", "display"])

whitelist = []
blacklist = []

_g_device_classes = []
_g_device_id_class = {} # device id => device class

_g_plugin_dir = os.path.join(os.path.dirname(__file__), "remotedevices_plugins")

def register_device_class(deviceClassInstance):
    _g_device_classes.append(deviceClassInstance)
    daemon_log(
        f"registered: {deviceClassInstance.__class__.__name__} from {plugin_name(deviceClassInstance)}"
    )

def plugin_name(device_class):
    return device_class.__class__.__module__.split(".",1)[-1]

def list_plugins():
    return [plugin_name(dc) for dc in _g_device_classes]

def load_plugin(deviceClassName):
    __import__(f"remotedevices_plugins.{deviceClassName}")

def load_all_plugins():
    for p in avail_plugins():
        try:
            load_plugin(p)
        except Exception as e:
            daemon_log(f"plugin {p} import failed: {e}")

def avail_plugins():
    return [
        os.path.basename(f)[:-3]
        for f in glob.glob(os.path.join(_g_plugin_dir, "*.py"))
        if not os.path.basename(f).startswith("_")
    ]

def plugin_dir():
    return _g_plugin_dir

class DeviceClass(object):
    """DeviceClass is offers methods for

    1. scanning which devices are available for remote sharing in
       the system (rescan).

    2. adopting a device for sharing; no other instance should share
       the same device.

    3. abandoning devices; abandoned devices can be used used without
       restrictions.
    """
    def __init__(self, maxRefCount=1):
        """
        If maxRefCount is -1, the number of simultaneous users is unlimited.
        """
        self._maxRefCount = maxRefCount

    def maxRefCount(self):
        return self._maxRefCount

    def rescan(self):
        """returns list of detected device identifiers (deviceId)"""
        raise NotImplementedError

    def adopt(self, deviceId):
        """reclaim ownership of the device. only adopted devices can be shared
        to remote peers. returns pair (info, obj), info is DeviceInfo
        instance, obj is a Python object through which the device can
        be accessed.

        info and obj are stored outside DeviceClass, and when they are not
        needed anymore, abandon(info, obj) will be called.
        """
        raise NotImplementedError

    def abandon(self, deviceInfo, deviceObj):
        """free device for others to use. returns None"""
        return NotImplementedError


class Devices(object):
    """
    """
    def __init__(self):
        self._lock = _thread.allocate_lock()
        self.reset()
        self.rescan()

    def reset(self):
        self._devinfo = {} # serial -> device, deviceInfo
        self._refcount = {} # serial -> int
        self._acquirer = {} # acquirer-id -> (serial -> list-of-timestamps)
        self._infos = {} # deviceInfo -> serial

    def rescan(self):
        with self._lock:
            serialNumbers = set([])
            for dc in _g_device_classes:
                try:
                    deviceIds = dc.rescan()
                except Exception as e:
                    deviceIds = []
                    daemon_log(
                        f'error: rescanning "{plugin_name(dc)}" failed: {traceback.format_exc()}'
                    )
                for deviceId in deviceIds:
                    if deviceId in serialNumbers and _g_device_id_class[deviceId] != dc:
                        daemon_log(
                            f'warning: rescan collision: found "{deviceId}" from "{plugin_name(dc)}" and "{plugin_name(_g_device_id_class[deviceId])}"'
                        )
                    serialNumbers.add(deviceId)
                    _g_device_id_class[deviceId] = dc
            if whitelist:
                serialNumbers = serialNumbers.intersection(whitelist)
            if blacklist:
                serialNumbers = serialNumbers - set(blacklist)

            # find new devices
            for serialNumber in sorted(serialNumbers):
                if serialNumber in self._devinfo:
                    daemon_log(f'rescan kept "{serialNumber}"')
                else:
                    try:
                        self._wolock_add(serialNumber)
                        daemon_log(f'rescan found "{serialNumber}"')
                    except Exception as e:
                        daemon_log(f'rescan found but failed connecting "{serialNumber}": {e}')

            # forget detached devices
            for serialNumber in list(self._devinfo.keys()):
                if serialNumber not in serialNumbers:
                    self._wolock_remove(serialNumber)
                    daemon_log(f'rescan forgot "{serialNumber}"')

    def match(self, **matchArgs):
        with self._lock:
            return self._wolock_match(**matchArgs)

    def _wolock_match(self, **matchArgs):
        return {
            self._infos[i]
            for i in list(self._infos.keys())
            if ("id" not in matchArgs or re.match(matchArgs["id"], i.id))
            and ("type" not in matchArgs or re.match(matchArgs["type"], i.type))
            and ("sw" not in matchArgs or re.match(matchArgs["sw"], i.sw))
            and ("hw" not in matchArgs or re.match(matchArgs["hw"], i.hw))
            and (
                "display" not in matchArgs
                or re.match(matchArgs["display"], i.display)
            )
            and (
                "free" not in matchArgs
                or matchArgs["free"].lower()
                == str(self.available(self._infos[i])).lower()
            )
            and (
                "busy" not in matchArgs
                or matchArgs["busy"].lower()
                == str(not self.available(self._infos[i])).lower()
            )
        }

    def available(self, key):
        """
        Returns True if object with id KEY can be acquired.
        """
        self._validate(key)
        maxRefCount = _g_device_id_class[key].maxRefCount()
        return (maxRefCount == -1 or self._refcount[key] < maxRefCount)

    def all(self):
        return list(self._refcount.keys())

    def acquire(self, block=True, acquirer="", **matchArgs):
        """
        Acquire a free device.

        Parameters:
          block (boolean, optional):
                  if True, wait until device becomes available and acquire it.
                  if False, immediately return None if there is no free device.

          acquirer (string, optional):
                  requester id.

          id|sw|display (regexp):
                  acquire only devices where device info matches all
                  defined regexps.

        Returns id of the device. The device remains locked until release.
        Returns None if there are no matching devices.

        Examples:
          d1 = acquire() # acquire any device
          d2 = acquire(id="BE57D3-153") # acquire this particular device
          d3 = acquire(sw="4\.[234]", display="480x800|720x1280")
              # acquire a device with sw version 4.2, 4.3 or 4.4 that has
              # 480x800 or 720x1280 display.
        """
        if acquirer is None:
            acquirer = ""
        while True:
            with self._lock:
                matchingSerials = self._wolock_match(**matchArgs)
                if not matchingSerials:
                    return None
                if matchingAvailable := [
                    s for s in matchingSerials if self.available(s)
                ]:
                    key = random.choice(tuple(matchingAvailable))
                    matchingAvailable.remove(key)
                    self._wolock_acquire(key, acquirer)
                    break
            if not block:
                return None
            daemon_log("acquire blocked")
            time.sleep(1)
        daemon_log(f'{repr(acquirer)} acquired "{key}"')
        return key

    def info(self, key):
        with self._lock:
            self._validate(key)
            return dict(self._devinfo[key][1]._asdict())

    def _validate(self, key):
        if key not in self._refcount:
            raise ValueError(f'unknown device "{key}"')

    def _wolock_acquire(self, key, acquirer=""):
        self._refcount[key] += 1
        timestamp = time.time()
        if acquirer in self._acquirer:
            if key in self._acquirer[acquirer]:
                self._acquirer[acquirer][key].append(timestamp)
            else:
                self._acquirer[acquirer][key] = [timestamp]
        else:
            self._acquirer[acquirer] = {key: [timestamp]}

    def _wolock_release(self, key, acquirer=""):
        if self._refcount[key] == 0:
            raise ValueError(f'device "{key}" not acquired')
        released_ts = time.time()
        if acquirer is None:
            # automatically find acquirer
            for acqid in self._acquirer:
                if len(self._acquirer[acqid].get(key, [])) > 0:
                    acquirer = acqid
                    break
        if (acquirer not in self._acquirer or
            key not in self._acquirer[acquirer] or
            len(self._acquirer[acquirer][key]) == 0):
            raise ValueError(f'"{acquirer}" has not acquired "{key}"')
        self._refcount[key] -= 1
        acquired_ts = self._acquirer[acquirer][key].pop()
        if len(self._acquirer[acquirer][key]) == 0:
            del self._acquirer[acquirer][key]
            if not self._acquirer[acquirer]: # empty dict
                del self._acquirer[acquirer]
        daemon_log('%s released "%s" after %.3f s' % (
            repr(acquirer), key, released_ts - acquired_ts))

    def release(self, key, acquirer=None):
        with self._lock:
            self._validate(key)
            self._wolock_release(key, acquirer)

    def release_all(self, acquirer):
        # release all keys acquired by the acquirer
        if acquirer not in self._acquirer:
            raise ValueError(f'unknown acquirer "{acquirer}"')
        with self._lock:
            for key in list(self._acquirer[acquirer].keys()):
                self._wolock_release(key, acquirer)

    def acquired(self, key):
        if self._refcount[key] > 0:
            return self._devinfo[key][0]
        else:
            raise ValueError(f'device "{key}" not acquired')

    def acquirers(self):
        return sorted(self._acquirer.keys())

    def acquisitions(self):
        retval = []
        with self._lock:
            for acquirer in sorted(self._acquirer.keys()):
                retval.extend(
                    (acquirer, key, self._acquirer[acquirer][key])
                    for key in self._acquirer[acquirer]
                )
        return retval

    def _wolock_add(self, serialNumber):
        # without lock device add. self._lock must be taken by the caller
        d, i = _g_device_id_class[serialNumber].adopt(serialNumber)
        self._devinfo[serialNumber] = d, i
        self._refcount[serialNumber] = 0
        self._infos[i] = serialNumber

    def add(self, serialNumber):
        if serialNumber not in _g_device_id_class:
            raise ValueError(f'device "{serialNumber}" not found')

        with self._lock:
            if serialNumber in blacklist:
                raise ValueError(f'device "{serialNumber}" blacklisted')
            elif serialNumber in self._refcount:
                raise ValueError(f'device "{serialNumber}" already added')
            try:
                self._wolock_add(serialNumber)
            except Exception as e:
                raise ValueError(f'error accessing "{serialNumber}": {e}')

    def api(self, serialNumber):
        self._validate(serialNumber)
        if serialNumber in self._devinfo:
            d, i = self._devinfo[serialNumber]
        doc = []
        for attr in sorted(dir(d)):
            m = getattr(d, attr)
            if isinstance(m, types.MethodType) and not attr.startswith("_"):
                doc.append((attr, m.__func__.__code__.co_varnames[1:m.__func__.__code__.co_argcount]))
            elif isinstance(m, types.FunctionType) and not attr.startswith("_"):
                doc.append((attr, m.__code__.co_varnames[1:m.__code__.co_argcount]))
        methods = [f'{m}({", ".join(args)})' for m, args in doc]
        return "\n".join(methods)

    def _wolock_remove(self, serialNumber):
        self._validate(serialNumber)
        if self._refcount[serialNumber] > 0:
            self._wolock_release(serialNumber, None)
        d, i = self._devinfo[serialNumber]
        _g_device_id_class[serialNumber].abandon(i, d)
        d, i = self._devinfo[serialNumber]
        del self._devinfo[serialNumber]
        del self._refcount[serialNumber]
        del self._infos[i]
        daemon_log(f'removed "{serialNumber}"')

    def remove(self, serialNumber, force=False):
        with self._lock:
            self._validate(serialNumber)
            if self._refcount[serialNumber] > 0 and not force:
                raise ValueError(f'device "{serialNumber}" is busy')
            self._wolock_remove(serialNumber)

    def blacklist_include(self, serialNumber):
        with self._lock:
            if serialNumber in blacklist:
                raise ValueError(f'device "{serialNumber}" already blacklisted')
            else:
                blacklist.append(serialNumber)

    def blacklist_exclude(self, serialNumber):
        with self._lock:
            if serialNumber not in blacklist:
                raise ValueError(f'device "{serialNumber}" not blacklisted')
            blacklist.remove(serialNumber)

    def blacklist(self):
        return blacklist

import logging
import asyncio
import pathlib
import uuid
from typing import Callable, Any, Union, List

from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak.backends.scanner import BaseBleakScanner


logger = logging.getLogger(__name__)
_here = pathlib.Path(__file__).parent


class BleakScannerCoreBluetooth(BaseBleakScanner):
    """The native macOS Bleak BLE Scanner.

    Documentation:
    https://developer.apple.com/documentation/corebluetooth/cbcentralmanager

    CoreBluetooth doesn't explicitly use MAC addresses to identify peripheral
    devices because private devices may obscure their MAC addresses. To cope
    with this, CoreBluetooth utilizes UUIDs for each peripheral. Bleak uses
    this for the BLEDevice address on macOS.

    Keyword Args:
        timeout (double): The scanning timeout to be used, in case of missing
          ``stopScan_`` method.

    """

    def __init__(self, **kwargs):
        super(BleakScannerCoreBluetooth, self).__init__(**kwargs)
        self._callback = None
        self._identifiers = None
        self._manager = CentralManagerDelegate.alloc().init()
        self._timeout = kwargs.get("timeout", 5.0)

    async def start(self):
        try:
            await self._manager.wait_for_powered_on(0.1)
        except asyncio.TimeoutError:
            raise BleakError("Bluetooth device is turned off")

        self._identifiers = {}

        def callback(p, a, r):
            self._identifiers[p.identifier()] = a
            if self._callback:
                self._callback(p, a, r)

        self._manager.callbacks[id(self)] = callback
        self._manager.start_scan({})

    async def stop(self):
        del self._manager.callbacks[id(self)]
        try:
            await self._manager.stop_scan()
        except Exception as e:
            logger.warning("stopScan method could not be called: {0}".format(e))

    async def set_scanning_filter(self, **kwargs):
        raise NotImplementedError(
            "Need to evaluate which macOS versions to support first..."
        )

    async def get_discovered_devices(self) -> List[BLEDevice]:
        found = []
        peripherals = self._manager.central_manager.retrievePeripheralsWithIdentifiers_(
            self._identifiers.keys(),
        )

        for i, peripheral in enumerate(peripherals):
            address = peripheral.identifier().UUIDString()
            name = peripheral.name() or "Unknown"
            details = peripheral

            advertisementData = self._identifiers[peripheral.identifier()]
            manufacturer_binary_data = advertisementData.get(
                "kCBAdvDataManufacturerData"
            )
            manufacturer_data = {}
            if manufacturer_binary_data:
                manufacturer_id = int.from_bytes(
                    manufacturer_binary_data[0:2], byteorder="little"
                )
                manufacturer_value = bytes(manufacturer_binary_data[2:])
                manufacturer_data = {manufacturer_id: manufacturer_value}

            uuids = [
                # converting to lower case to match other platforms
                str(u).lower()
                for u in advertisementData.get("kCBAdvDataServiceUUIDs", [])
            ]

            found.append(
                BLEDevice(
                    address,
                    name,
                    details,
                    uuids=uuids,
                    manufacturer_data=manufacturer_data,
                )
            )

        return found

    def register_detection_callback(self, callback: Callable):
        self._callback = callback

    @classmethod
    async def find_specific_device(
        cls, device_identifier: str, timeout: float = 10.0, **kwargs
    ) -> Union[BLEDevice, None]:
        """A convenience method for obtaining a ``BLEDevice`` object specified by macOS UUID address.

        Args:
            device_identifier (str): The MAC address of the Bluetooth peripheral.
            timeout (float): Optional timeout to wait for detection of specified peripheral. Defaults to 10.0 seconds.

        Returns:
            The ``BLEDevice`` sought or ``None`` if not detected.

        """
        loop = asyncio.get_event_loop()
        stop_scanning_event = asyncio.Event()
        device_identifier = device_identifier.lower()

        def stop_if_detected(peripheral, advertisementData, RSSI):
            if str(peripheral.identifier().UUIDString()).lower() == device_identifier:
                loop.call_soon_threadsafe(stop_scanning_event.set)

        scanner = cls(timeout=timeout)
        scanner.register_detection_callback(stop_if_detected)

        await scanner.start()
        try:
            await asyncio.wait_for(stop_scanning_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            device = None
        else:
            device = next(
                d
                for d in await scanner.get_discovered_devices()
                if d.address.lower() == device_identifier.lower()
            )
        finally:
            await scanner.stop()

        return device

    # macOS specific methods

    @property
    def is_scanning(self):
        # TODO: Evaluate if newer macOS than 10.11 has isScanning.
        try:
            return self._manager.isScanning_
        except:
            return None

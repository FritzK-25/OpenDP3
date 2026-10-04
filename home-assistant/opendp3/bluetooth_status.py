"""Read-only BlueZ status for the configured batteries; no payloads or keys logged."""
import asyncio
import json
from pathlib import Path

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus


async def main():
    from openpowerstation.config import load_config
    address = load_config(Path("/data/config.json")).address.upper()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    previous = None
    try:
        while True:
            reply = await bus.call(Message(destination="org.bluez", path="/",
                                           interface="org.freedesktop.DBus.ObjectManager",
                                           member="GetManagedObjects"))
            if reply.message_type == MessageType.ERROR:
                print("BlueZ diagnostic unavailable:", reply.error_name, flush=True)
            else:
                summary = {}
                for path, interfaces in reply.body[0].items():
                    adapter = interfaces.get("org.bluez.Adapter1")
                    if adapter:
                        summary[path.rsplit("/", 1)[-1]] = {
                            key: adapter[key].value for key in ("Powered", "Discovering") if key in adapter}
                    device = interfaces.get("org.bluez.Device1")
                    if device:
                        values = {key: value.value for key, value in device.items()}
                        name = str(values.get("Name", "")).upper()
                        kind = ("ecoflow" if values.get("Address", "").upper() == address else
                                "jackery-candidate" if name.startswith(("HT", "JACKERY", "JK", "EXPLORER")) else None)
                        if kind:
                            summary[kind] = {key: values[key] for key in
                                             ("Connected", "ServicesResolved", "RSSI") if key in values}
                rendered = json.dumps(summary, sort_keys=True)
                if rendered != previous:
                    print("BlueZ status:", rendered, flush=True)
                    previous = rendered
            await asyncio.sleep(20)
    finally:
        bus.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

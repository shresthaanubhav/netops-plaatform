#!/usr/bin/env python3
"""OSPF monitoring via Netmiko, exposed as Prometheus metrics."""

import os
import re
import sys
import time
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from prometheus_client import start_http_server, Gauge

DEVICES_FILE = Path(os.environ.get("DEVICES_FILE", "/app/devices.yaml"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
VALID_NEIGHBOR_STATES = {"FULL/DR", "FULL/BDR", "FULL/DROTHER"}

NEIGHBOR_CMD = "show ip ospf neighbor"
OSPF_INTERFACE_CMD = "show ip ospf interface"
INTERFACES_CMD = "show interfaces"
INTERFACE_CONFIG_CMD = "show running-config | section ^interface"

NEIGHBOR_LINE = re.compile(r"^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$")
OSPF_INTERFACE_LINE = re.compile(r"^(\S+) is (up|down)")
INTERFACE_MTU_LINE = re.compile(r"^\s+MTU (\d+) bytes")
INTERFACE_CONFIG_HEADER = re.compile(r"^interface (\S+)")

# --- Prometheus metrics ---
ospf_device_reachable = Gauge(
    "ospf_device_reachable", "Whether the device responded to SSH", ["router"]
)
ospf_neighbor_up = Gauge(
    "ospf_neighbor_up", "1 if neighbor is in a valid FULL state, else 0",
    ["router", "neighbor_id", "interface"],
)
ospf_neighbor_count = Gauge(
    "ospf_neighbor_count", "Number of OSPF neighbors seen", ["router"]
)
ospf_interface_mtu = Gauge(
    "ospf_interface_mtu", "Interface MTU in bytes", ["router", "interface"]
)
ospf_mtu_ignore_configured = Gauge(
    "ospf_mtu_ignore_configured",
    "1 if 'ip ospf mtu-ignore' is set on the interface (MTU validation disabled)",
    ["router", "interface"],
)


def load_devices(path: Path) -> list[dict]:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("devices", [])


def device_params(entry: dict) -> dict:
    return {
        "host": entry["hostname"],
        "username": entry["username"],
        "password": os.environ.get("NET_PASSWORD", entry.get("password", "")),
        "secret": os.environ.get("NET_SECRET", entry.get("secret", "")),
        "device_type": entry.get("device_type", "cisco_ios"),
        "conn_timeout": 10,
    }


def parse_ospf_neighbors(output: str) -> list[dict]:
    neighbors = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Neighbor ID"):
            continue
        match = NEIGHBOR_LINE.match(line)
        if not match:
            continue
        neighbor_id, pri, state, dead_time, address, interface = match.groups()
        neighbors.append({
            "neighbor_id": neighbor_id, "state": state,
            "interface": interface.strip(),
        })
    return neighbors


def parse_interface_mtus(output: str) -> dict[str, int]:
    mtus = {}
    current_iface = None
    for line in output.splitlines():
        iface_match = OSPF_INTERFACE_LINE.match(line.strip())
        if iface_match:
            current_iface = iface_match.group(1)
            continue
        if current_iface:
            mtu_match = INTERFACE_MTU_LINE.match(line)
            if mtu_match:
                mtus[current_iface] = int(mtu_match.group(1))
                current_iface = None
    return mtus


def parse_mtu_ignore_config(output: str) -> dict[str, bool]:
    mtu_ignore = {}
    current_iface = None
    for line in output.splitlines():
        header = INTERFACE_CONFIG_HEADER.match(line.strip())
        if header:
            current_iface = header.group(1)
            mtu_ignore.setdefault(current_iface, False)
            continue
        if current_iface and line.strip() == "ip ospf mtu-ignore":
            mtu_ignore[current_iface] = True
    return mtu_ignore


def poll_device(entry: dict) -> None:
    """Runs in a worker thread. Sets Gauge values directly —
    prometheus_client's C-backed metrics ARE thread-safe for .set(),
    which is why this pattern is safe without redirect_stdout/StringIO."""
    name = entry.get("name", entry["hostname"])

    try:
        with ConnectHandler(**device_params(entry)) as conn:
            conn.enable()
            ospf_device_reachable.labels(router=name).set(1)

            neighbor_output = conn.send_command(NEIGHBOR_CMD)
            neighbors = parse_ospf_neighbors(neighbor_output)
            ospf_neighbor_count.labels(router=name).set(len(neighbors))

            for n in neighbors:
                is_up = 1 if n["state"] in VALID_NEIGHBOR_STATES else 0
                ospf_neighbor_up.labels(
                    router=name, neighbor_id=n["neighbor_id"], interface=n["interface"]
                ).set(is_up)

            interface_mtus = parse_interface_mtus(
                conn.send_command(INTERFACES_CMD, read_timeout=60)
            )
            mtu_ignore = parse_mtu_ignore_config(
                conn.send_command(INTERFACE_CONFIG_CMD, read_timeout=60)
            )
            for iface, mtu in interface_mtus.items():
                ospf_interface_mtu.labels(router=name, interface=iface).set(mtu)
            for iface, ignored in mtu_ignore.items():
                ospf_mtu_ignore_configured.labels(
                    router=name, interface=iface
                ).set(1 if ignored else 0)

    except Exception as exc:
        ospf_device_reachable.labels(router=name).set(0)
        print(f"ERROR: {name}: {exc}", file=sys.stderr, flush=True)


def poll_loop() -> None:
    devices = load_devices(DEVICES_FILE)
    if not devices:
        print(f"ERROR: no devices in {DEVICES_FILE}", file=sys.stderr)
        sys.exit(1)

    while True:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(poll_device, e): e for e in devices}
            for future in as_completed(futures):
                future.result()  # exceptions already handled inside poll_device
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    start_http_server(8000)
    print(f"OSPF exporter listening on :8000, polling every {POLL_INTERVAL}s")
    poll_loop()

#!/usr/bin/env python3
"""DHCP health monitoring via Netmiko, exposed as Prometheus metrics."""

import os
import sys
import time
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from prometheus_client import start_http_server, Gauge

DEVICES_FILE = Path(os.environ.get("DEVICES_FILE", "/app/devices.yaml"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

# --- Prometheus metrics ---
dhcp_device_reachable = Gauge(
    "dhcp_device_reachable", "Whether the device responded to SSH", ["router"]
)
dhcp_pool_remaining = Gauge(
    "dhcp_pool_remaining", "IPs remaining in DHCP pool", ["router", "pool"]
)
dhcp_pool_exhausted = Gauge(
    "dhcp_pool_exhausted", "1 if pool has 10 or fewer IPs remaining", ["router", "pool"]
)
dhcp_conflict_count = Gauge(
    "dhcp_conflict_count", "Number of DHCP address conflicts detected", ["router"]
)
dhcp_missing_helper = Gauge(
    "dhcp_missing_helper",
    "1 if this relay interface is missing ip helper-address",
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
        "global_delay_factor": 2,
    }


def parse_dhcp_pools(output: str) -> list[dict]:
    pools = []
    current_pool = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Pool"):
            if current_pool:
                pools.append(current_pool)
            current_pool = {"name": line.split()[1]}
        elif "Total addresses" in line:
            current_pool["total"] = int(line.split(":")[-1].strip())
        elif "Leased addresses" in line:
            current_pool["leased"] = int(line.split(":")[-1].strip())
    if current_pool:
        pools.append(current_pool)
    return pools


def parse_dhcp_conflicts(output: str) -> list[str]:
    conflicts = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("IP address"):
            continue
        parts = line.split()
        if len(parts) >= 1:
            conflicts.append(parts[0])
    return conflicts


def parse_helper_address(output: str, expected_interfaces: list[str]) -> list[str]:
    missing = []
    current_iface = None
    has_helper = False
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("interface"):
            if current_iface and current_iface in expected_interfaces and not has_helper:
                missing.append(current_iface)
            current_iface = line.split()[1]
            has_helper = False
        elif "ip helper-address" in line:
            has_helper = True
    if current_iface and current_iface in expected_interfaces and not has_helper:
        missing.append(current_iface)
    return missing


def poll_device(entry: dict) -> None:
    name = entry.get("name", entry["hostname"])

    if not entry.get("check_dhcp", False):
        return

    try:
        with ConnectHandler(**device_params(entry)) as conn:
            conn.enable()
            dhcp_device_reachable.labels(router=name).set(1)

            dhcp_output = conn.send_command("show ip dhcp pool")
            pools = parse_dhcp_pools(dhcp_output)
            for pool in pools:
                remaining = pool["total"] - pool["leased"]
                dhcp_pool_remaining.labels(router=name, pool=pool["name"]).set(remaining)
                dhcp_pool_exhausted.labels(router=name, pool=pool["name"]).set(
                    1 if remaining <= 10 else 0
                )

            conflict_output = conn.send_command("show ip dhcp conflict")
            conflicts = parse_dhcp_conflicts(conflict_output)
            dhcp_conflict_count.labels(router=name).set(len(conflicts))
            if conflicts and entry.get("dhcp_auto_clear_conflicts", False):
                conn.send_command("clear ip dhcp conflict *")

            config_output = conn.send_command("show run | section interface")
            expected = entry.get("dhcp_relay_interfaces", [])
            missing = parse_helper_address(config_output, expected)
            for iface in expected:
                dhcp_missing_helper.labels(router=name, interface=iface).set(
                    1 if iface in missing else 0
                )

    except Exception as exc:
        dhcp_device_reachable.labels(router=name).set(0)
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
                future.result()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    start_http_server(8002)
    print(f"DHCP exporter listening on :8002, polling every {POLL_INTERVAL}s")
    poll_loop()

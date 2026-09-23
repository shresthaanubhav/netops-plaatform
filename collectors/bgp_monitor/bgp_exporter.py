#!/usr/bin/env python3
"""BGP monitoring via Netmiko, exposed as Prometheus metrics."""

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

SUMMARY_CMD = "show bgp summary"
BGP_CMD = "show bgp"

BGP_SUMMARY_LINE = re.compile(
    r"^(\S+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\S+)\s+(\S+)\s*$"
)

# --- Prometheus metrics ---
bgp_device_reachable = Gauge(
    "bgp_device_reachable", "Whether the device responded to SSH", ["router"]
)
bgp_neighbor_established = Gauge(
    "bgp_neighbor_established", "1 if BGP neighbor session is established, else 0",
    ["router", "neighbor"],
)
bgp_neighbor_count = Gauge(
    "bgp_neighbor_count", "Number of BGP neighbors seen in summary", ["router"]
)
bgp_has_best_path = Gauge(
    "bgp_has_best_path", "1 if at least one best-path route found", ["router"]
)
bgp_neighbor_ping_ok = Gauge(
    "bgp_neighbor_ping_ok", "1 if ping to BGP neighbor succeeded, else 0",
    ["router", "neighbor_ip"],
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
        "global_delay_factor": 3,
    }


def parse_bgp_summary(output: str) -> list[dict]:
    neighbors = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Neighbor") or line.startswith("BGP "):
            continue
        match = BGP_SUMMARY_LINE.match(line)
        if not match:
            continue
        neighbor, up_down, state_or_pfxrcd = match.groups()
        neighbors.append({
            "neighbor": neighbor,
            "established": state_or_pfxrcd.isdigit(),
        })
    return neighbors


def has_best_path(output: str) -> bool:
    return "*>" in output


def ping_failed(output: str) -> bool:
    stripped = output.strip()
    if not stripped:
        return True
    if "Success rate is 0 percent" in output:
        return True
    if "!" not in output and "Success rate is 100 percent" not in output:
        return True
    return False


def poll_device(entry: dict) -> None:
    name = entry.get("name", entry["hostname"])
    bgp_neighbors = entry.get("bgp_neighbors", [])

    try:
        with ConnectHandler(**device_params(entry)) as conn:
            conn.enable()
            bgp_device_reachable.labels(router=name).set(1)

            summary_output = conn.send_command(SUMMARY_CMD, read_timeout=30)
            neighbors = parse_bgp_summary(summary_output)
            bgp_neighbor_count.labels(router=name).set(len(neighbors))

            for n in neighbors:
                bgp_neighbor_established.labels(
                    router=name, neighbor=n["neighbor"]
                ).set(1 if n["established"] else 0)

            bgp_output = conn.send_command(BGP_CMD, read_timeout=60)
            bgp_has_best_path.labels(router=name).set(1 if has_best_path(bgp_output) else 0)

            for neighbor_ip in bgp_neighbors:
                ping_output = conn.send_command(f"ping {neighbor_ip}")
                bgp_neighbor_ping_ok.labels(
                    router=name, neighbor_ip=neighbor_ip
                ).set(0 if ping_failed(ping_output) else 1)

    except Exception as exc:
        bgp_device_reachable.labels(router=name).set(0)
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
    start_http_server(8001)
    print(f"BGP exporter listening on :8001, polling every {POLL_INTERVAL}s")
    poll_loop()

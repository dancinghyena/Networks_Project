#!/usr/bin/env python3
import socket
import threading
import json
import time
import csv
import random
import zlib
from collections import deque
from copy import deepcopy

# -----------------------
# Config
# -----------------------
HOST = "0.0.0.0"
PORT = 0  # 0 = let OS choose a free UDP port (avoids WinError 10048 conflicts)

GRID_N = 5
UPDATE_RATE = 20              # snapshots per second
FULL_EVERY = 10               # send full snapshot every N snapshots
REDUNDANCY_K = 2              # include last K snapshot IDs as redundant metadata

HEARTBEAT_TIMEOUT = 5         # seconds

LOG_FILE = "server_log.csv"

# -----------------------
# Network setup (MUST be before any recv/send)
# -----------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((HOST, PORT))

# Actual chosen port
PORT = sock.getsockname()[1]
print(f"[SERVER] Listening on {HOST}:{PORT}")

# Write port so clients can read it (optional; you also use manual prompt in client)
with open("server_port.txt", "w") as f:
    f.write(str(PORT))

# -----------------------
# State
# -----------------------
grid = [["UNCLAIMED" for _ in range(GRID_N)] for _ in range(GRID_N)]
grid_lock = threading.Lock()

clients = {}  # addr -> {"id": int, "last_heartbeat": float}
client_colors = {}  # client_id -> rgb tuple

next_client_id = 1
seq = 0

# Snapshot history (for redundancy metadata / debugging)
snapshot_history = deque(maxlen=200)  # store dicts with {snapshot_id, server_timestamp_ms, full, changes_count}


# -----------------------
# Helpers
# -----------------------
def stable_json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def encode_with_checksum(packet: dict) -> bytes:
    """
    CRC32 checksum over the packet without the checksum field.
    """
    tmp = dict(packet)
    tmp.pop("checksum", None)
    raw = stable_json_bytes(tmp)
    tmp["checksum"] = zlib.crc32(raw) & 0xFFFFFFFF
    return stable_json_bytes(tmp)


def assign_color(client_id: int):
    random.seed(client_id * 99991)
    return (random.randint(70, 255), random.randint(70, 255), random.randint(70, 255))


def compute_changes(prev_grid, new_grid):
    """
    Return list of cell changes: [{"cell": idx, "value": owner_id_or_UNCLAIMED}, ...]
    """
    changes = []
    for r in range(GRID_N):
        for c in range(GRID_N):
            if prev_grid[r][c] != new_grid[r][c]:
                changes.append({"cell": r * GRID_N + c, "value": new_grid[r][c]})
    return changes


def check_game_over(cur_grid):
    """
    If all cells claimed, return winner list (ties allowed). Else None.
    """
    owners = []
    for r in range(GRID_N):
        for c in range(GRID_N):
            if cur_grid[r][c] == "UNCLAIMED":
                return None
            owners.append(cur_grid[r][c])

    counts = {}
    for o in owners:
        counts[o] = counts.get(o, 0) + 1

    maxv = max(counts.values())
    winners = [k for k, v in counts.items() if v == maxv]
    return winners


# -----------------------
# Logging
# -----------------------
server_csv = open(LOG_FILE, "w", newline="")
server_writer = csv.DictWriter(
    server_csv,
    fieldnames=[
        "snapshot_id",
        "server_timestamp_ms",
        "full",
        "changes_count",
        "clients_count",
    ],
)
server_writer.writeheader()
server_csv.flush()


# -----------------------
# Incoming handler
# -----------------------
def handle_incoming():
    """
    Handles:
      - Init / Init_ACK
      - HEARTBEAT
      - Event (reliable) -> ACK
      - RESYNC_REQ -> send full SNAPSHOT immediately

    Note: Windows can throw ConnectionResetError on UDP recvfrom if peer closes.
          We ignore it.
    """
    global next_client_id

    while True:
        try:
            data, addr = sock.recvfrom(65536)
        except ConnectionResetError:
            # Windows UDP quirk when a peer closes; safe to ignore
            continue

        try:
            msg = json.loads(data.decode(errors="ignore"))
        except Exception:
            continue

        mtype = msg.get("type")

        # INIT handshake
        if mtype == "Init":
            if addr not in clients:
                client_id = next_client_id
                next_client_id += 1
                clients[addr] = {"id": client_id, "last_heartbeat": time.time()}
                client_colors[client_id] = assign_color(client_id)
                sock.sendto(stable_json_bytes({"type": "Init_ACK", "client_id": client_id}), addr)
                print(f"[SERVER] New client: {addr} -> ID {client_id}")
            else:
                sock.sendto(stable_json_bytes({"type": "Init_ACK", "client_id": clients[addr]["id"]}), addr)
            continue

        # ignore unknown clients
        if addr not in clients:
            continue

        # update heartbeat for any known message
        clients[addr]["last_heartbeat"] = time.time()
        client_id = clients[addr]["id"]

        if mtype == "HEARTBEAT":
            continue

        # Reliable event from client (claim a cell)
        if mtype == "Event":
            try:
                cell = int(msg.get("cell"))
            except Exception:
                continue

            # ACK immediately
            sock.sendto(stable_json_bytes({"type": "ACK", "cell": cell}), addr)

            r = cell // GRID_N
            c = cell % GRID_N

            with grid_lock:
                if 0 <= r < GRID_N and 0 <= c < GRID_N:
                    if grid[r][c] == "UNCLAIMED":
                        grid[r][c] = str(client_id)
            continue

        # Client requests re-sync (send a full snapshot immediately)
        if mtype == "RESYNC_REQ":
            server_ts = int(time.time() * 1000)
            with grid_lock:
                cur_grid = deepcopy(grid)

            full_packet = {
                "type": "SNAPSHOT",
                "snapshot_id": seq,
                "seq": seq,
                "server_timestamp_ms": server_ts,
                "full": True,
                "changes": [],
                "redundant": [],
                "grid": cur_grid,
            }
            sock.sendto(encode_with_checksum(full_packet), addr)
            continue


# -----------------------
# Snapshot broadcaster
# -----------------------
def broadcast_loop():
    """
    Broadcasts:
      - Full snapshot every FULL_EVERY ticks
      - Delta snapshots otherwise
    """
    global seq

    prev = deepcopy(grid)
    interval = 1.0 / UPDATE_RATE

    while True:
        start = time.time()
        server_ts = int(start * 1000)

        with grid_lock:
            cur = deepcopy(grid)

        seq += 1
        is_full = (seq % FULL_EVERY == 0)

        if is_full:
            packet = {
                "type": "SNAPSHOT",
                "snapshot_id": seq,
                "seq": seq,
                "server_timestamp_ms": server_ts,
                "full": True,
                "changes": [],
                "redundant": [h["snapshot_id"] for h in list(snapshot_history)[-REDUNDANCY_K:]],
                "grid": cur,
            }
            changes_count = GRID_N * GRID_N
        else:
            changes = compute_changes(prev, cur)
            packet = {
                "type": "SNAPSHOT",
                "snapshot_id": seq,
                "seq": seq,
                "server_timestamp_ms": server_ts,
                "full": False,
                "changes": changes,
                "redundant": [h["snapshot_id"] for h in list(snapshot_history)[-REDUNDANCY_K:]],
            }
            changes_count = len(changes)

        payload = encode_with_checksum(packet)

        # send to all clients
        for addr in list(clients.keys()):
            try:
                sock.sendto(payload, addr)
            except Exception:
                pass

        # Log snapshot metadata
        server_writer.writerow(
            {
                "snapshot_id": seq,
                "server_timestamp_ms": server_ts,
                "full": int(is_full),
                "changes_count": changes_count,
                "clients_count": len(clients),
            }
        )
        server_csv.flush()

        snapshot_history.append(
            {
                "snapshot_id": seq,
                "server_timestamp_ms": server_ts,
                "full": bool(is_full),
                "changes_count": changes_count,
            }
        )

        # game over check
        winners = check_game_over(cur)
        if winners is not None:
            game_over_pkt = stable_json_bytes({"type": "GAME_OVER", "winner": winners})
            for addr in list(clients.keys()):
                try:
                    sock.sendto(game_over_pkt, addr)
                except Exception:
                    pass

        prev = cur

        # sleep remainder
        elapsed = time.time() - start
        if elapsed < interval:
            time.sleep(interval - elapsed)


# -----------------------
# Heartbeat checker
# -----------------------
def heartbeat_checker():
    while True:
        now = time.time()
        for addr in list(clients.keys()):
            if now - clients[addr]["last_heartbeat"] > HEARTBEAT_TIMEOUT:
                print(f"[SERVER] Client timed out: {addr} (ID {clients[addr]['id']})")
                del clients[addr]
        time.sleep(1)


# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    print(f"Server starting on {HOST}:{PORT} | Grid={GRID_N}x{GRID_N}")

    threading.Thread(target=handle_incoming, daemon=True).start()
    threading.Thread(target=broadcast_loop, daemon=True).start()
    threading.Thread(target=heartbeat_checker, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down server.")
        server_csv.close()
        sock.close()

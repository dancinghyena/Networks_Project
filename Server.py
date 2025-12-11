#!/usr/bin/env python3
"""
server.py
UDP authoritative server for Grid Clash.
- Accepts INIT from clients, assigns incremental IDs and replies with ACK + full grid.
- Accepts ACQUIRE_REQUEST from clients (uses client_id sent by client).
- Periodically broadcasts SNAPSHOT packets (full every FULL_EVERY snapshots).
- Keeps REDUNDANCY_K previous snapshots' changes in 'redundant'.
- When grid is full, calculates winner, broadcasts GAME_OVER and exits.
- Logs broadcast activity to server_log.csv.
"""

import socket
import threading
import json
import time
import csv
from collections import deque
from copy import deepcopy

HOST = "0.0.0.0"
PORT = 5000
GRID_N = 5
UPDATE_RATE = 20               # snapshots per second
FULL_EVERY = 10                # send full grid every N snapshots
REDUNDANCY_K = 2               # include up to K previous snapshots' changes
MAX_PACKET_BYTES = 1200
LOG_FILE = "server_log.csv"

# server state
id_counter = 1
players = {}           # addr -> assigned_id
clients = set()        # set of (ip, port)
lock = threading.Lock()
seq = 0

grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
history = deque(maxlen=200)  # stores recent snapshot entries

# CSV logging
server_csv = open(LOG_FILE, "w", newline="")
server_writer = csv.DictWriter(server_csv, fieldnames=[
    'log_time_ms', 'snapshot_id', 'seq', 'clients_count', 'bytes_sent_total'
])
server_writer.writeheader()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.setblocking(True)

def compute_changes(prev_grid, cur_grid):
    """Return list of [r,c,owner] that changed from prev_grid -> cur_grid"""
    changes = []
    for r in range(GRID_N):
        for c in range(GRID_N):
            if prev_grid[r][c] != cur_grid[r][c]:
                changes.append([r, c, cur_grid[r][c]])
    return changes

def handle_incoming():
    """Receives INIT and ACQUIRE_REQUEST packets, registers clients, sends ACK to INIT."""
    global grid, id_counter
    print("Incoming handler started.")
    while True:
        try:
            data, addr = sock.recvfrom(65536)
        except Exception:
            continue

        try:
            msg = json.loads(data.decode())
        except Exception:
            continue

        msg_type = msg.get('type')

        # INIT handshake
        if msg_type == 'INIT':
            with lock:
                if addr not in players:
                    assigned_id = id_counter
                    players[addr] = assigned_id
                    id_counter += 1
                else:
                    assigned_id = players[addr]
                clients.add(addr)

            ack = {
                'type': 'ACK',
                'assigned_id': assigned_id,
                'grid': grid,
                'server_timestamp_ms': int(time.time() * 1000)
            }
            try:
                sock.sendto(json.dumps(ack).encode(), addr)
                print(f"[SERVER] Sent ACK to {addr} with ID {assigned_id}")
            except Exception:
                pass
            continue

        # register client if not known
        with lock:
            clients.add(addr)

        # ACQUIRE request handling
        if msg_type == 'ACQUIRE_REQUEST':
            try:
                cell = int(msg.get('cell'))
                client_id = msg.get('client_id')
                r = cell // GRID_N
                c = cell % GRID_N
                if 0 <= r < GRID_N and 0 <= c < GRID_N:
                    with lock:
                        if grid[r][c] == 'UNCLAIMED':
                            grid[r][c] = client_id
            except Exception:
                continue

def broadcast_loop():
    """Periodically send snapshots (deltas + redundancy) to all clients."""
    global seq
    prev_snapshot_grid = deepcopy(grid)
    print("Broadcast loop started.")
    while True:
        time.sleep(1.0 / UPDATE_RATE)
        with lock:
            cur_grid = deepcopy(grid)

        snapshot_id = seq
        server_ts = int(time.time() * 1000)

        changes = compute_changes(prev_snapshot_grid, cur_grid)
        full_flag = (snapshot_id % FULL_EVERY == 0) or (snapshot_id == 0)
        entry = {
            'snapshot_id': snapshot_id,
            'server_timestamp_ms': server_ts,
            'changes': changes,
            'full': full_flag
        }
        history.append(entry)

        # build redundancy
        redundant = []
        hist_list = list(history)
        if len(hist_list) >= 2:
            # grab up to REDUNDANCY_K previous snapshots
            for h in hist_list[max(0, len(hist_list) - 1 - REDUNDANCY_K): -1]:
                if h:
                    redundant.append({'snapshot_id': h['snapshot_id'], 'changes': h['changes']})

        packet = {
            'type': 'SNAPSHOT',
            'snapshot_id': snapshot_id,
            'seq': snapshot_id,
            'server_timestamp_ms': server_ts,
            'full': full_flag,
            'changes': changes,
            'redundant': redundant
        }
        if full_flag:
            packet['grid'] = cur_grid

        payload = json.dumps(packet).encode()
        bytes_sent_total = 0
        with lock:
            targets = list(clients)

        for c in targets:
            try:
                sock.sendto(payload, c)
                bytes_sent_total += len(payload)
            except Exception:
                pass

        server_writer.writerow({
            'log_time_ms': int(time.time() * 1000),
            'snapshot_id': snapshot_id,
            'seq': snapshot_id,
            'clients_count': len(targets),
            'bytes_sent_total': bytes_sent_total
        })
        server_csv.flush()

        # WIN DETECTION
        with lock:
            flat = [cur_grid[r][c] for r in range(GRID_N) for c in range(GRID_N)]
            if all(cell != 'UNCLAIMED' for cell in flat):
                # Count ownership
                counts = {}
                for owner in flat:
                    if owner != 'UNCLAIMED':
                        counts[owner] = counts.get(owner, 0) + 1

                # choose winner (most cells)
                winner = None
                max_count = -1
                for owner, cnt in counts.items():
                    if cnt > max_count:
                        max_count = cnt
                        winner = owner

                win_msg = {
                    'type': 'GAME_OVER',
                    'winner': winner,
                    'final_grid': cur_grid,
                    'server_timestamp_ms': int(time.time() * 1000)
                }
                payload_win = json.dumps(win_msg).encode()
                for c in targets:
                    try:
                        sock.sendto(payload_win, c)
                    except Exception:
                        pass

                print(f"[SERVER] GAME OVER → Winner: {winner}")
                # tidy up and exit
                server_csv.close()
                sock.close()
                return

        prev_snapshot_grid = cur_grid
        seq += 1

if __name__ == "__main__":
    print("Server starting on %s:%d  Grid=%dx%d  rate=%d/s  full_every=%d  redundancy=%d"
          % (HOST, PORT, GRID_N, GRID_N, UPDATE_RATE, FULL_EVERY, REDUNDANCY_K))
    threading.Thread(target=handle_incoming, daemon=True).start()
    threading.Thread(target=broadcast_loop, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down server.")
        try:
            server_csv.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
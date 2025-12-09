#!/usr/bin/env python3

import socket, threading, json, time, csv
from collections import deque
from copy import deepcopy

HOST = "0.0.0.0"
PORT = 5000
GRID_N = 20
UPDATE_RATE = 20              
FULL_EVERY = 10               
REDUNDANCY_K = 2              
MAX_PACKET_BYTES = 1200       
LOG_FILE = "server_log.csv"

grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
clients = set()  # set of (ip,port)
lock = threading.Lock()
seq = 0

history = deque(maxlen=200)  


server_csv = open(LOG_FILE, "w", newline="")
server_writer = csv.DictWriter(server_csv, fieldnames=[
    'log_time_ms','snapshot_id','seq','clients_count','bytes_sent_total'
])
server_writer.writeheader()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.setblocking(True)

def compute_changes(prev_grid, cur_grid):
    """Return list of (r,c,owner) that changed from prev_grid -> cur_grid"""
    changes = []
    for r in range(GRID_N):
        for c in range(GRID_N):
            if prev_grid[r][c] != cur_grid[r][c]:
                changes.append([r, c, cur_grid[r][c]])
    return changes

def handle_incoming():
    """Receives ACQUIRE_REQUESTs and registers clients. Runs in its own thread."""
    global grid
    while True:
        data, addr = sock.recvfrom(65536)
        try:
            msg = json.loads(data.decode())
        except Exception:
            continue
        clients.add(addr)
        if msg.get('type') == 'ACQUIRE_REQUEST':
            cell = int(msg.get('cell'))
            ts = float(msg.get('timestamp', time.time()))
            client_id = msg.get('client_id')
            r = cell // GRID_N
            c = cell % GRID_N
            with lock:
            
                if grid[r][c] == 'UNCLAIMED':
                    grid[r][c] = client_id

def broadcast_loop():
    """Periodically create snapshot (delta) and broadcast to all known clients.
       Each packet includes:
         - header: type, snapshot_id, seq, server_timestamp_ms, full (bool)
         - if full: 'grid' (full 2D list)
         - 'changes' : list of atomic changes for this snapshot
         - 'redundant' : list of previous snapshots' 'changes' (up to REDUNDANCY_K)
    """
    global seq
    prev_snapshot_grid = deepcopy(grid)
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

        redundant = []
        hist_list = list(history)
        if len(hist_list) >= 2:
            for h in hist_list[-1-REDUNDANCY_K: -1]:
                if h: redundant.append({'snapshot_id': h['snapshot_id'], 'changes': h['changes']})

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
        server_csv.close()
        sock.close()

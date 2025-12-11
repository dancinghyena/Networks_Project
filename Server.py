#!/usr/bin/env python3
import socket, threading, json, time, csv
from collections import deque
from copy import deepcopy
import random

HOST = "0.0.0.0"
PORT = 5000
GRID_N = 5
UPDATE_RATE = 20
FULL_EVERY = 10
REDUNDANCY_K = 2
LOG_FILE = "server_log.csv"
HEARTBEAT_TIMEOUT = 5

grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
clients = {}  # { (ip,port) : {'id': n, 'last_heartbeat': t } }
client_colors = {}  # client_id -> RGB
lock = threading.Lock()
seq = 0
next_client_id = 1
history = deque(maxlen=200)

BASE_COLORS = [
    (255, 0, 0), (0, 200, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (128,128,0), (128,0,128)
]

server_csv = open(LOG_FILE, "w", newline="")
server_writer = csv.DictWriter(server_csv, fieldnames=[
    'log_time_ms','snapshot_id','seq','clients_count','bytes_sent_total'
])
server_writer.writeheader()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.setblocking(True)

def assign_color(client_id):
    if client_id <= len(BASE_COLORS):
        return BASE_COLORS[client_id-1]
    return (random.randint(50,255), random.randint(50,255), random.randint(50,255))

def compute_changes(prev_grid, cur_grid):
    changes = []
    for r in range(GRID_N):
        for c in range(GRID_N):
            if prev_grid[r][c] != cur_grid[r][c]:
                changes.append([r, c, cur_grid[r][c]])
    return changes

def handle_incoming():
    global grid, next_client_id
    while True:
        try:
            data, addr = sock.recvfrom(65536)
            msg = json.loads(data.decode())
        except:
            continue

        with lock:
            # New client
            if addr not in clients and msg.get('type') == 'Init':
                client_id = next_client_id
                next_client_id += 1
                clients[addr] = {'id': client_id, 'last_heartbeat': time.time()}
                client_colors[client_id] = assign_color(client_id)
                sock.sendto(json.dumps({'type':'Init_ACK','client_id':client_id}).encode(), addr)
                print(f"[SERVER] New client connected: ID {client_id}")
                continue

            if addr in clients:
                clients[addr]['last_heartbeat'] = time.time()

                if msg.get('type') == 'HEARTBEAT':
                    continue

                elif msg.get('type') == 'Event':
                    cell = int(msg.get('cell'))
                    client_id = clients[addr]['id']
                    r = cell // GRID_N
                    c = cell % GRID_N
                    previous_owner = grid[r][c]
                    if previous_owner == 'UNCLAIMED':
                        grid[r][c] = client_id
                        new_owner = client_id
                    else:
                        new_owner = previous_owner
                    # ACK
                    ack_msg = {'type':'ACK','cell':cell,'owner':new_owner}
                    sock.sendto(json.dumps(ack_msg).encode(), addr)

def broadcast_loop():
    global seq
    prev_snapshot_grid = deepcopy(grid)
    while True:
        time.sleep(1.0 / UPDATE_RATE)
        with lock:
            cur_grid = deepcopy(grid)

            # Check if game over
            flat = [cur_grid[r][c] for r in range(GRID_N) for c in range(GRID_N)]
            if all(cell != 'UNCLAIMED' for cell in flat):
                counts = {}
                for owner in flat:
                    counts[owner] = counts.get(owner,0)+1
                max_score = max(counts.values())
                winners = [k for k,v in counts.items() if v==max_score]
                win_msg = {
                    'type':'GAME_OVER',
                    'winner': winners,  # list of winners
                    'final_grid': deepcopy(cur_grid),
                    'server_timestamp_ms': int(time.time()*1000)
                }
                for c in clients.keys():
                    try:
                        sock.sendto(json.dumps(win_msg).encode(), c)
                    except:
                        pass
                print(f"[SERVER] GAME OVER → Winners: {winners}")
                server_csv.close()
                sock.close()
                return

        snapshot_id = seq
        server_ts = int(time.time() * 1000)
        changes = compute_changes(prev_snapshot_grid, cur_grid)
        full_flag = (snapshot_id % FULL_EVERY == 0) or (snapshot_id==0)
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
            for h in hist_list[-1-REDUNDANCY_K:-1]:
                if h: redundant.append({'snapshot_id':h['snapshot_id'],'changes':h['changes']})

        packet = {
            'type':'SNAPSHOT',
            'snapshot_id':snapshot_id,
            'seq':snapshot_id,
            'server_timestamp_ms':server_ts,
            'full':full_flag,
            'changes':changes,
            'redundant':redundant
        }
        if full_flag:
            packet['grid'] = cur_grid

        payload = json.dumps(packet).encode()
        bytes_sent_total = 0
        with lock:
            targets = list(clients.keys())
        for c in targets:
            try:
                sock.sendto(payload, c)
                bytes_sent_total += len(payload)
            except:
                pass

        server_writer.writerow({
            'log_time_ms': int(time.time()*1000),
            'snapshot_id': snapshot_id,
            'seq': snapshot_id,
            'clients_count': len(targets),
            'bytes_sent_total': bytes_sent_total
        })
        server_csv.flush()
        prev_snapshot_grid = cur_grid
        seq += 1

def heartbeat_checker():
    while True:
        time.sleep(1)
        now = time.time()
        with lock:
            to_remove = [addr for addr,data in clients.items() if now - data['last_heartbeat'] > HEARTBEAT_TIMEOUT]
            for addr in to_remove:
                print(f"[Server] Client {clients[addr]['id']} timed out")
                del clients[addr]

if __name__ == "__main__":
    print(f"Server starting on {HOST}:{PORT} Grid={GRID_N}x{GRID_N}")
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

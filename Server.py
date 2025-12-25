#!/usr/bin/env python3
import socket, threading, json, time, csv, os, sys
from collections import deque
from copy import deepcopy
import random

HOST = "0.0.0.0"

# Determine port dynamically in this order:
# 1) command-line first arg (integer)
# 2) environment variable `SERVER_PORT`
# 3) contents of `server_port.txt` in the script directory
# 4) 0 to let the OS pick an ephemeral port
def _read_port_file(path):
    try:
        with open(path, 'r') as f:
            txt = f.read().strip()
            if txt:
                return int(txt)
    except Exception:
        return None

script_dir = os.path.dirname(os.path.abspath(__file__))
port_file = os.path.join(script_dir, 'server_port.txt')

def get_desired_port():
    # Gather potential sources for a default candidate (CLI arg > env > port file)
    candidate = None
    if len(sys.argv) > 1:
        try:
            candidate = int(sys.argv[1])
        except Exception:
            candidate = None
    if candidate is None:
        envp = os.getenv('SERVER_PORT')
        if envp:
            try:
                candidate = int(envp)
            except Exception:
                candidate = None
    if candidate is None:
        pf = _read_port_file(port_file)
        if pf:
            candidate = pf

    # If running interactively, always prompt the user and show the candidate as default.
    try:
        if sys.stdin and sys.stdin.isatty():
            prompt_default = str(candidate) if candidate else 'blank (OS selects)'
            for _ in range(3):
                try:
                    s = input(f"Choose server port [{prompt_default}]: ").strip()
                except EOFError:
                    s = ''
                if s == '':
                    # Use candidate if available, otherwise let OS pick
                    return candidate if candidate else 0
                try:
                    p = int(s)
                    if 1 <= p <= 65535:
                        return p
                except Exception:
                    pass
                print("Invalid port. Enter a number 1-65535 or blank to accept default.")
            # fallback if user fails to provide valid input
            return candidate if candidate else 0
    except Exception:
        pass

    # Non-interactive: use candidate if present, otherwise let OS pick
    return candidate if candidate else 0

PORT = get_desired_port()
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
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((HOST, PORT))
# If we bound to port 0, or if we read the desired port from file/env/cli,
# determine the actual bound port and persist it to `server_port.txt` for clients.
actual_port = sock.getsockname()[1]
if actual_port != PORT:
    PORT = actual_port
try:
    with open(port_file, 'w') as pf:
        pf.write(str(PORT))
except Exception:
    pass

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

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    print(f"Server starting on {HOST}:{PORT} Grid={GRID_N}x{GRID_N}")
    threading.Thread(target=handle_incoming, daemon=True).start()
    threading.Thread(target=broadcast_loop, daemon=True).start()
    threading.Thread(target=heartbeat_checker, daemon=True).start()
    try:
        # Main thread idles until interrupted
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[SERVER] KeyboardInterrupt received, shutting down...")
    finally:
        try:
            server_csv.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
        print("[SERVER] Stopped")

#!/usr/bin/env python3
"""NetRush Server - Grid Clash Game"""
import socket, threading, time, csv
from collections import deque
from copy import deepcopy
from protocol import MsgType, unpack_packet, make_init_ack, make_ack, make_snapshot, make_game_over

# Try psutil for CPU monitoring
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Config
HOST, PORT = "0.0.0.0", 5000
GRID_N = 5
UPDATE_RATE = 20
FULL_EVERY, REDUNDANCY_K = 10, 2

# State
grid = [['UNCLAIMED']*GRID_N for _ in range(GRID_N)]
clients = {}  # {addr: {'id': int, 'last_recv': float}}
pending_claims = []
history = deque(maxlen=200)
lock = threading.Lock()
seq, next_cid, running = 0, 1, True
bytes_sent_total, start_time = 0, time.time()

# Logging
csv_file = open("server_log.csv", "w", newline="")
writer = csv.DictWriter(csv_file, fieldnames=[
    'log_time_ms', 'snapshot_id', 'seq_num', 'clients_count', 
    'bytes_sent', 'bandwidth_kbps', 'cpu_percent'
])
writer.writeheader()

# Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.settimeout(0.1)

def get_cpu():
    return psutil.cpu_percent(interval=None) if HAS_PSUTIL else 0.0

def compute_changes(prev, cur):
    return [[r,c,cur[r][c]] for r in range(GRID_N) for c in range(GRID_N) if prev[r][c] != cur[r][c]]

def process_claims():
    global pending_claims, grid
    if not pending_claims: return []
    pending_claims.sort(key=lambda x: x['ts'])
    acks = []
    for claim in pending_claims:
        r, c = claim['cell'] // GRID_N, claim['cell'] % GRID_N
        if 0 <= r < GRID_N and 0 <= c < GRID_N:
            owner = grid[r][c] if grid[r][c] != 'UNCLAIMED' else claim['cid']
            if grid[r][c] == 'UNCLAIMED': grid[r][c] = claim['cid']
            acks.append({'addr': claim['addr'], 'cell': claim['cell'], 'owner': owner})
    pending_claims = []
    return acks

def handle_incoming():
    global next_cid, running
    while running:
        try: 
            data, addr = sock.recvfrom(65536)
        except socket.timeout: 
            continue
        except OSError:
            break
        
        hdr, payload = unpack_packet(data, GRID_N)
        if not hdr: continue
        
        with lock:
            # INIT - register new client or keep-alive for existing
            if hdr['msg_type'] == MsgType.INIT:
                if addr not in clients:
                    clients[addr] = {'id': next_cid, 'last_recv': time.time()}
                    print(f"[SERVER] Client {next_cid} connected from {addr}")
                    next_cid += 1
                else:
                    clients[addr]['last_recv'] = time.time()  # Keep-alive
                try: 
                    sock.sendto(make_init_ack(clients[addr]['id']), addr)
                except: pass
                    
            elif addr in clients:
                clients[addr]['last_recv'] = time.time()
                if hdr['msg_type'] == MsgType.EVENT:
                    pending_claims.append({
                        'cell': payload['cell'], 
                        'cid': clients[addr]['id'], 
                        'ts': payload.get('ts', hdr['timestamp_ms']), 
                        'addr': addr
                    })

def broadcast_loop():
    global seq, running, bytes_sent_total
    prev_grid = deepcopy(grid)
    if HAS_PSUTIL: psutil.cpu_percent(interval=None)
    
    while running:
        time.sleep(1.0 / UPDATE_RATE)
        
        try:
            with lock:
                acks = process_claims()
                cur_grid = deepcopy(grid)
                flat = [cur_grid[r][c] for r in range(GRID_N) for c in range(GRID_N)]
                game_over = all(c != 'UNCLAIMED' for c in flat)
                targets = list(clients.keys())
            
            # Send ACKs
            for a in acks:
                try: sock.sendto(make_ack(a['cell'], a['owner'], seq), a['addr'])
                except: pass
            
            # Game over
            if game_over:
                counts = {}
                for o in flat: counts[o] = counts.get(o, 0) + 1
                mx = max(counts.values()) if counts else 0
                winners = [k for k,v in counts.items() if v == mx]
                pkt = make_game_over(winners, cur_grid)
                
                for _ in range(3):  # Send 3 times for reliability
                    for addr in targets:
                        try: sock.sendto(pkt, addr)
                        except: pass
                    time.sleep(0.05)
                
                print(f"[SERVER] GAME OVER! Winners: {winners}")
                running = False
                break
            
            # Build and send snapshot
            changes = compute_changes(prev_grid, cur_grid)
            full = (seq % FULL_EVERY == 0)
            history.append({'sid': seq, 'changes': changes, 'full': full, 'grid': deepcopy(cur_grid) if full else None})
            
            # Get redundant updates
            hist_list = list(history)
            redundant = []
            if len(hist_list) > 1:
                for h in hist_list[max(0, len(hist_list)-REDUNDANCY_K-1):len(hist_list)-1]:
                    if h: redundant.append({'snapshot_id': h['sid'], 'changes': h['changes']})
            
            try: 
                pkt = make_snapshot(seq, cur_grid if full else [], changes, full, redundant)
            except ValueError:
                pkt = make_snapshot(seq, cur_grid if full else [], changes, full, [])
            
            bytes_sent = 0
            for addr in targets:
                try: 
                    sock.sendto(pkt, addr)
                    bytes_sent += len(pkt)
                except: pass
            bytes_sent_total += bytes_sent
            
            # Log metrics
            elapsed = max(time.time() - start_time, 0.001)
            bandwidth_kbps = (bytes_sent_total * 8 / 1000) / elapsed
            
            writer.writerow({
                'log_time_ms': int(time.time() * 1000),
                'snapshot_id': seq,
                'seq_num': seq,
                'clients_count': len(targets),
                'bytes_sent': bytes_sent,
                'bandwidth_kbps': round(bandwidth_kbps, 2),
                'cpu_percent': round(get_cpu(), 2)
            })
            csv_file.flush()
            
            prev_grid = cur_grid
            seq += 1
            
        except Exception as e:
            print(f"[SERVER] Error in broadcast: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    print(f"[SERVER] Starting on {HOST}:{PORT}, Grid={GRID_N}x{GRID_N}, Rate={UPDATE_RATE}Hz")
    print(f"[SERVER] No heartbeat required - clients stay connected while window is open")
    
    threading.Thread(target=handle_incoming, daemon=True).start()
    threading.Thread(target=broadcast_loop, daemon=True).start()
    # Removed timeout_check - clients don't need to send traffic to stay connected
    
    try:
        while running: time.sleep(0.5)
    except KeyboardInterrupt: 
        running = False
    
    csv_file.close()
    sock.close()
    print("[SERVER] Stopped")
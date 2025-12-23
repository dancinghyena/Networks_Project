#!/usr/bin/env python3

import socket
import threading
import json
import time
import csv
import struct
import zlib
import psutil
import os
from collections import deque
from copy import deepcopy

HOST = "0.0.0.0"
PORT = 5000
GRID_N = 5
UPDATE_RATE = 20              
FULL_EVERY = 10                
REDUNDANCY_K = 2              
MAX_PACKET_BYTES = 1200
LOG_FILE = "server_log.csv"

# NetRush Protocol Constants
PROTOCOL_ID = b"NRSH"
VERSION = 1
MSG_TYPE_INIT = 0
MSG_TYPE_DATA = 1
MSG_TYPE_EVENT = 2
MSG_TYPE_ACK = 3
HEADER_SIZE = 28 
id_counter = 1
players = {}           # addr -> assigned_id
clients = set()        # set of (ip, port)
lock = threading.Lock()
seq = 0

grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
history = deque(maxlen=200)  # stores recent snapshot entries

# Performance metrics
process = psutil.Process(os.getpid())
packets_sent_count = 0
packets_received_count = 0
total_bytes_sent = 0
update_frequency_actual = 0
last_frequency_check = time.time()

# CSV logging
server_csv = open(LOG_FILE, "w", newline="")
server_writer = csv.DictWriter(server_csv, fieldnames=[
    'log_time_ms', 'snapshot_id', 'seq', 'clients_count', 
    'bytes_sent_total', 'packets_sent', 'packets_received',
    'cpu_percent', 'update_frequency_hz', 'memory_mb'
])
server_writer.writeheader()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.setblocking(True)

def create_header(msg_type, snapshot_id, seq_num, payload):
    """Create NetRush protocol header"""
    server_timestamp = int(time.time() * 1000)
    payload_len = len(payload)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    
    header = struct.pack(
        '!4s B B I I Q H I',
        PROTOCOL_ID,      # 4 bytes
        VERSION,          # 1 byte
        msg_type,         # 1 byte
        snapshot_id,      # 4 bytes
        seq_num,          # 4 bytes
        server_timestamp, # 8 bytes
        payload_len,      # 2 bytes
        checksum          # 4 bytes
    )
    return header

def parse_header(data):
    """Parse NetRush protocol header, return dict or None"""
    if len(data) < HEADER_SIZE:
        return None
    
    try:
        protocol_id, version, msg_type, snapshot_id, seq_num, server_timestamp, payload_len, checksum = struct.unpack(
            '!4s B B I I Q H I',
            data[:HEADER_SIZE]
        )
        
        if protocol_id != PROTOCOL_ID:
            return None
        
        return {
            'protocol_id': protocol_id,
            'version': version,
            'msg_type': msg_type,
            'snapshot_id': snapshot_id,
            'seq_num': seq_num,
            'server_timestamp': server_timestamp,
            'payload_len': payload_len,
            'checksum': checksum
        }
    except:
        return None

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
    global grid, id_counter, packets_received_count
    print("Incoming handler started.")
    while True:
        try:
            data, addr = sock.recvfrom(65536)
            packets_received_count += 1
        except Exception as e:
            print(f"[SERVER] Error receiving data: {e}")
            continue

        print(f"[SERVER] Received {len(data)} bytes from {addr}")
        
        # Parse header
        header = parse_header(data)
        if not header:
            print(f"[SERVER] Failed to parse header from {addr}")
            if len(data) >= 4:
                print(f"[SERVER] First 4 bytes: {data[:4]}")
            continue
        
        print(f"[SERVER] Parsed header - msg_type: {header['msg_type']}, snapshot_id: {header['snapshot_id']}")
        
        # Extract payload
        payload = data[HEADER_SIZE:HEADER_SIZE + header['payload_len']]
        
        # Verify checksum
        calculated_checksum = zlib.crc32(payload) & 0xFFFFFFFF
        if calculated_checksum != header['checksum']:
            print(f"[SERVER] Checksum mismatch! Expected: {header['checksum']}, Got: {calculated_checksum}")
            continue
        
        print(f"[SERVER] Checksum valid, payload length: {len(payload)}")
        
        try:
            msg = json.loads(payload.decode())
        except Exception:
            continue

        msg_type_name = msg.get('type')

        # INIT handshake (MSG_TYPE_INIT = 0)
        if header['msg_type'] == MSG_TYPE_INIT:
            with lock:
                if addr not in players:
                    assigned_id = id_counter
                    players[addr] = assigned_id
                    id_counter += 1
                else:
                    assigned_id = players[addr]
                clients.add(addr)

            ack_payload = {
                'type': 'ACK',
                'assigned_id': assigned_id,
                'grid': grid,
                'server_timestamp_ms': int(time.time() * 1000)
            }
            ack_payload_bytes = json.dumps(ack_payload).encode()
            ack_header = create_header(MSG_TYPE_ACK, 0, 0, ack_payload_bytes)
            
            try:
                sock.sendto(ack_header + ack_payload_bytes, addr)
                print(f"[SERVER] Sent ACK to {addr} with ID {assigned_id}")
            except Exception:
                pass
            continue

        # register client if not known
        with lock:
            clients.add(addr)

        # ACQUIRE request handling (MSG_TYPE_EVENT = 2)
        if header['msg_type'] == MSG_TYPE_EVENT and msg_type_name == 'ACQUIRE_REQUEST':
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
    global seq, packets_sent_count, total_bytes_sent, update_frequency_actual, last_frequency_check
    prev_snapshot_grid = deepcopy(grid)
    snapshot_counter = 0
    print("Broadcast loop started.")
    
    while True:
        loop_start = time.time()
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

        packet_data = {
            'type': 'SNAPSHOT',
            'snapshot_id': snapshot_id,
            'seq': snapshot_id,
            'server_timestamp_ms': server_ts,
            'full': full_flag,
            'changes': changes,
            'redundant': redundant
        }
        if full_flag:
            packet_data['grid'] = cur_grid

        payload = json.dumps(packet_data).encode()
        header = create_header(MSG_TYPE_DATA, snapshot_id, snapshot_id, payload)
        full_packet = header + payload
        
        bytes_sent_total = 0
        with lock:
            targets = list(clients)

        for c in targets:
            try:
                sock.sendto(full_packet, c)
                bytes_sent_total += len(full_packet)
                packets_sent_count += 1
            except Exception:
                pass

        total_bytes_sent += bytes_sent_total
        snapshot_counter += 1
        
        # Calculate actual update frequency every second
        now = time.time()
        if now - last_frequency_check >= 1.0:
            update_frequency_actual = snapshot_counter / (now - last_frequency_check)
            snapshot_counter = 0
            last_frequency_check = now
        
        # Collect CPU and memory metrics
        cpu_percent = process.cpu_percent(interval=0.01)
        memory_mb = process.memory_info().rss / (1024 * 1024)

        server_writer.writerow({
            'log_time_ms': int(time.time() * 1000),
            'snapshot_id': snapshot_id,
            'seq': snapshot_id,
            'clients_count': len(targets),
            'bytes_sent_total': bytes_sent_total,
            'packets_sent': packets_sent_count,
            'packets_received': packets_received_count,
            'cpu_percent': round(cpu_percent, 2),
            'update_frequency_hz': round(update_frequency_actual, 2),
            'memory_mb': round(memory_mb, 2)
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
                win_payload = json.dumps(win_msg).encode()
                win_header = create_header(MSG_TYPE_EVENT, snapshot_id, snapshot_id, win_payload)
                payload_win = win_header + win_payload
                
                for c in targets:
                    try:
                        sock.sendto(payload_win, c)
                    except Exception:
                        pass

                print(f"[SERVER] GAME OVER → Winner: {winner}")
                print(f"[SERVER] Final stats - Packets sent: {packets_sent_count}, Packets received: {packets_received_count}")
                print(f"[SERVER] Total bytes sent: {total_bytes_sent}, Avg CPU: {cpu_percent:.2f}%")
                # tidy up and exit
                server_csv.close()
                sock.close()
                return

        prev_snapshot_grid = cur_grid
        seq += 1

if __name__ == "__main__":
    print("Server starting on %s:%d  Grid=%dx%d  rate=%d/s  full_every=%d  redundancy=%d"
          % (HOST, PORT, GRID_N, GRID_N, UPDATE_RATE, FULL_EVERY, REDUNDANCY_K))
    print("Using NetRush protocol: NRSH v%d" % VERSION)
    print(f"Performance monitoring enabled - logging to {LOG_FILE}")
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


        
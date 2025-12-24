# NetRush Protocol - Grid Clash Game

A UDP-based multiplayer game state synchronization protocol for the Grid Clash game, implementing Project 2 requirements for Computer Networks.

## Protocol Overview

**NetRush (NRSH)** is a custom binary protocol designed for low-latency game state synchronization over UDP.

### Binary Header Format (28 bytes)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | protocol_id | `b"NRSH"` |
| 4 | 1 | version | Protocol version (1) |
| 5 | 1 | msg_type | Message type (0-6) |
| 6 | 4 | snapshot_id | Snapshot identifier |
| 10 | 4 | seq_num | Sequence number |
| 14 | 8 | timestamp | Milliseconds since epoch |
| 22 | 2 | payload_len | Payload length |
| 24 | 4 | checksum | CRC32 checksum |

### Message Types

| Code | Type | Description |
|------|------|-------------|
| 0 | INIT | Client connection request |
| 1 | INIT_ACK | Server connection acknowledgment |
| 2 | SNAPSHOT | Game state update |
| 3 | EVENT | Cell claim request |
| 4 | ACK | Event acknowledgment |
| 5 | HEARTBEAT | Keep-alive |
| 6 | GAME_OVER | Game end notification |

## Requirements

- Python 3.9+
- pygame (`pip install pygame`)
- psutil (optional, for CPU monitoring): `pip install psutil`

## Quick Start

### 1. Start the Server

```bash
python Server.py
```

Expected output:
```
[SERVER] NetRush Server starting on 0.0.0.0:5000
[SERVER] Grid=20x20, UpdateRate=20Hz
[SERVER] Binary protocol with CRC32 checksum enabled
```

### 2. Start Client(s)

In separate terminals:

```bash
python client.py
```

Each client will:
1. Connect to the server
2. Receive a unique player ID
3. Display the game grid
4. Allow clicking cells to claim them

## Configuration

Edit the constants at the top of each file:

### Server.py
```python
HOST = "0.0.0.0"      # Listen address
PORT = 5000           # UDP port
GRID_N = 20           # Grid size (20x20)
UPDATE_RATE = 20      # Snapshots per second
```

### client.py
```python
SERVER_IP = "127.0.0.1"  # Server address
SERVER_PORT = 5000       # Server port
GRID_N = 20              # Must match server
```

## Game Rules (Grid Clash)

1. All players see a shared 20×20 grid
2. Cells start as "unclaimed" (gray)
3. Click a cell to claim it (turns your color)
4. First-come-first-served (ties resolved by timestamp)
5. Game ends when all cells are claimed
6. Winner has the most cells

## Protocol Features

| Feature | Implementation |
|---------|----------------|
| Transport | UDP |
| Reliability | Selective ACK for events, redundant updates for snapshots |
| Loss Tolerance | K=2 redundant changes per packet |
| Ordering | Snapshot ID + client-side reordering |
| Smoothing | 100ms interpolation delay |
| Conflict Resolution | Server-authoritative, earliest timestamp wins |
| Checksum | CRC32 on all packets |

## Metrics Logged

### Server (server_log.csv)
- `snapshot_id`, `seq`, `clients_count`
- `bytes_sent_total`, `bandwidth_per_client_kbps`
- `cpu_percent`

### Client (client_N_log.csv)
- `snapshot_id`, `server_timestamp_ms`, `recv_time_ms`
- `latency_ms`, `jitter_ms`
- `perceived_position_error`, `cpu_percent`

## Running Tests (Linux)

Use the provided test script with netem:

```bash
chmod +x run_all_tests.sh
sudo ./run_all_tests.sh eth0
```

This runs:
1. Baseline (no impairment)
2. 2% packet loss
3. 5% packet loss
4. 100ms delay
5. 100ms delay + 10ms jitter

## Manual netem Commands

```bash
# Add 5% loss
sudo tc qdisc add dev eth0 root netem loss 5%

# Add 100ms delay with 10ms jitter
sudo tc qdisc add dev eth0 root netem delay 100ms 10ms

# Remove impairment
sudo tc qdisc del dev eth0 root
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Port already in use | Change PORT in both files, or kill existing process |
| No connection | Check firewall, ensure same IP/port |
| psutil warning | Install with `pip install psutil` (optional) |
| Packet too large | Reduce REDUNDANCY_K or grid size |

## Project Structure

```
project/
├── Server.py           # Game server
├── client.py           # Game client (pygame)
├── protocol.py         # Shared binary protocol module
├── run_all_tests.sh    # Automated test runner
├── server_log.csv      # Server metrics output
├── client_*_log.csv    # Client metrics output
└── README.md           # This file
```

## Authors

Computer Networks - Project 2

## Video Demo

[Link to demo video: TODO - add your link here]

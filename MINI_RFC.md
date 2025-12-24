# NetRush Protocol (NRSH) v1 - Mini-RFC

**Protocol Name:** NetRush (NRSH)  
**Version:** 1  
**Date:** December 2024  
**Authors:** [Your Team Name]

---

## 1. Introduction

NetRush is a lightweight, real-time, UDP-based multiplayer game synchronization protocol designed for the "Grid Clash" game — a competitive cell-claiming game where players race to acquire cells on a shared grid.

### 1.1 Purpose
NetRush enables low-latency game state synchronization between a central server and multiple clients. Unlike TCP, which enforces guaranteed delivery at the cost of latency, NetRush prioritizes responsiveness while implementing selective reliability only for critical gameplay events.

### 1.2 Assumptions & Constraints
| Parameter | Value | Justification |
|-----------|-------|---------------|
| Transport | UDP (IPv4) | Low latency for real-time games |
| Max packet size | 1200 bytes | Below typical MTU to avoid fragmentation |
| Update rate | 20 Hz (50ms) | Balance between responsiveness and bandwidth |
| Max clients | 4 | Lab testbed requirement |
| Loss tolerance | Up to 5% | Redundancy covers moderate loss |

### 1.3 Why a New Protocol?
Existing protocols (MQTT-SN, CoAP) are designed for IoT telemetry, not real-time game sync. Game engines like ENet and RakNet are external libraries not permitted by assignment constraints. NetRush is custom-built for Grid Clash's specific requirements.

---

## 2. Protocol Architecture

### 2.1 Entities
| Component | Role |
|-----------|------|
| **Server** | Authoritative game state, broadcasts snapshots at 20 Hz |
| **Client** | Sends player actions, receives updates, renders game |

### 2.2 Communication Model
```
┌──────────┐                          ┌──────────┐
│  Client  │◄─── SNAPSHOT (20 Hz) ────│  Server  │
│          │                          │          │
│          │──── INIT ───────────────►│          │
│          │◄─── INIT_ACK ────────────│          │
│          │                          │          │
│          │──── EVENT ──────────────►│          │
│          │◄─── ACK ─────────────────│          │
│          │                          │          │
│          │◄─── GAME_OVER ───────────│          │
└──────────┘                          └──────────┘
```

### 2.3 Finite State Machine (Client)

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
    ┌───────────┐  INIT   ┌───────────┐  INIT_ACK  ┌─────┴─────┐
    │DISCONNECTED├───────►│CONNECTING ├───────────►│  PLAYING  │
    └───────────┘         └─────┬─────┘            └─────┬─────┘
         ▲                      │                        │
         │                  timeout                  GAME_OVER
         │                      │                        │
         │                      ▼                        ▼
         │                ┌───────────┐            ┌───────────┐
         └────────────────┤   RETRY   │            │ GAME_OVER │
                          └───────────┘            └───────────┘
```

### 2.4 Finite State Machine (Server)

```
    ┌────────────┐           ┌─────────────┐
    │   IDLE     ├──────────►│   RUNNING   │
    └────────────┘  start    └──────┬──────┘
                                    │
                              all cells claimed
                                    │
                                    ▼
                             ┌─────────────┐
                             │  GAME_OVER  │
                             └─────────────┘
```

---

## 3. Message Formats

### 3.1 Binary Header (28 bytes)

| Offset | Field | Size | Type | Description |
|--------|-------|------|------|-------------|
| 0 | protocol_id | 4 bytes | ASCII | `"NRSH"` - Protocol identifier |
| 4 | version | 1 byte | uint8 | Protocol version (1) |
| 5 | msg_type | 1 byte | uint8 | Message type enum |
| 6 | snapshot_id | 4 bytes | uint32 | Snapshot sequence number |
| 10 | seq_num | 4 bytes | uint32 | Packet sequence number |
| 14 | timestamp_ms | 8 bytes | uint64 | Milliseconds since epoch |
| 22 | payload_len | 2 bytes | uint16 | Payload length in bytes |
| 24 | checksum | 4 bytes | uint32 | CRC32 of header + payload |

**Total header size:** 28 bytes

### 3.2 Struct Pack Format
```python
# Python struct format (network byte order)
HEADER_FORMAT = "!4sBBIIQHI"

# Packing example:
header = struct.pack(HEADER_FORMAT,
    b"NRSH",           # 4s - protocol_id
    1,                 # B  - version
    msg_type,          # B  - msg_type
    snapshot_id,       # I  - snapshot_id (unsigned int)
    seq_num,           # I  - seq_num
    timestamp_ms,      # Q  - timestamp (unsigned long long)
    payload_len,       # H  - payload_len (unsigned short)
    checksum           # I  - checksum
)
```

### 3.3 Message Types

| Type | Value | Direction | Description | Payload |
|------|-------|-----------|-------------|---------|
| INIT | 0 | Client → Server | Connection request | Empty |
| INIT_ACK | 1 | Server → Client | Connection accepted | `{client_id: int}` |
| SNAPSHOT | 2 | Server → Clients | Game state update | `{full, grid?, changes, redundant?}` |
| EVENT | 3 | Client → Server | Cell claim request | `{cell, client_id, ts}` |
| ACK | 4 | Server → Client | Event acknowledgment | `{cell, owner}` |
| GAME_OVER | 5 | Server → Clients | Game end | `{winner, final_grid}` |

### 3.4 Payload Encoding

**Original Mechanism #1: Compact Grid Encoding**

Instead of sending a full 2D array (expensive), only claimed cells are encoded:
```
Format: "row,col,owner;row,col,owner;..."
Example: "0,1,1;2,3,2;4,4,1" (3 claimed cells)
Empty grid: "" (0 bytes instead of 400+ bytes)
```

**Original Mechanism #2: Conditional Compression**

Full snapshots use zlib compression (level 6) when payload exceeds threshold:
```python
if len(payload) > MAX_PAYLOAD or full_snapshot:
    payload = b'\x01' + zlib.compress(raw, 6)  # Compressed
else:
    payload = b'\x00' + raw  # Uncompressed
```

First byte indicates compression status (0=raw, 1=compressed).

---

## 4. Communication Procedures

### 4.1 Session Start
1. Client sends `INIT` packet
2. Server assigns unique `client_id` (1, 2, 3, ...)
3. Server responds with `INIT_ACK` containing assigned ID
4. Client enters PLAYING state
5. If no response in 500ms, client retries INIT

### 4.2 Normal Gameplay
1. Server broadcasts `SNAPSHOT` at 20 Hz to all clients
2. Every 10th snapshot is a "full" snapshot (complete grid state)
3. Other snapshots are "delta" (only changes since last full)
4. Each snapshot includes K=2 redundant previous changes

### 4.3 Critical Event (Cell Claim)
1. Client clicks cell → sends `EVENT` with cell_id and timestamp
2. Client shows PENDING state (light peach color)
3. Server resolves conflicts (earliest timestamp wins)
4. Server sends `ACK` with actual owner
5. If no ACK in 500ms, client retransmits (max 3 retries)
6. Client updates cell to confirmed owner color

### 4.4 Game End
1. Server detects all cells claimed
2. Server calculates winner(s)
3. Server broadcasts `GAME_OVER` 3 times (reliability)
4. Clients display winner overlay

### 4.5 Keep-Alive
Client periodically sends `INIT` every 3 seconds as keep-alive. Server updates last-seen timestamp for each client.

---

## 5. Reliability & Performance Features

### 5.1 Redundant Updates (Strategy #1)
Each snapshot includes the last K=2 changes, compensating for packet loss:
```python
redundant = [
    {'snapshot_id': prev_sid, 'changes': prev_changes},
    {'snapshot_id': prev2_sid, 'changes': prev2_changes}
]
```

### 5.2 Selective Reliability for Events
| Message | Reliability | Reason |
|---------|-------------|--------|
| SNAPSHOT | Fire-and-forget | Next snapshot replaces lost one |
| EVENT | ACK + Retransmit | Critical game action |
| GAME_OVER | Send 3× | Must be received |

### 5.3 Retransmission Timer
```
RDT_TIMEOUT = 500ms
MAX_RETRIES = 3

if now - last_sent > RDT_TIMEOUT:
    if retries >= MAX_RETRIES:
        give_up()
    else:
        retransmit()
        retries += 1
```

### 5.4 Duplicate Detection
Clients maintain a set of seen snapshot IDs:
```python
if snapshot_id in seen_ids:
    discard()  # Duplicate
else:
    seen_ids.add(snapshot_id)
    process()
```

### 5.5 Smoothing Technique
**Linear Color Interpolation (Lerp)**

When cell state changes, display smoothly transitions over 200ms:
```python
def lerp_color(c1, c2, t):
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t)
    )
```

---

## 6. Experimental Evaluation Plan

### 6.1 Metrics to Collect

| Metric | Source | Description |
|--------|--------|-------------|
| client_id | Client | Player identifier |
| snapshot_id | Both | Snapshot sequence |
| seq_num | Both | Packet sequence |
| server_timestamp_ms | Header | When server sent |
| recv_time_ms | Client | When client received |
| latency_ms | Client | recv_time - server_timestamp |
| jitter_ms | Client | Variation in inter-arrival time |
| perceived_position_error | Client | Cells different from server |
| cpu_percent | Both | CPU utilization |
| bandwidth_kbps | Server | Bytes sent per second |

### 6.2 Test Scenarios

| Scenario | netem Command | Acceptance Criteria |
|----------|---------------|---------------------|
| Baseline | None | 20 updates/sec, latency ≤50ms, CPU <60% |
| Loss 2% | `sudo tc qdisc add dev eth0 root netem loss 2%` | Error ≤0.5 units, smooth interpolation |
| Loss 5% | `sudo tc qdisc add dev eth0 root netem loss 5%` | Events ≥99% delivered in 200ms |
| Delay 100ms | `sudo tc qdisc add dev eth0 root netem delay 100ms` | Clients function, no visible misbehavior |
| Delay + Jitter | `sudo tc qdisc add dev eth0 root netem delay 100ms 10ms` | Visual smoothing consistent |

### 6.3 Test Automation Script

```bash
#!/bin/bash
# run_test.sh <scenario_name> <netem_command>

INTERFACE="eth0"
TEST_DIR="results_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$TEST_DIR"

# Apply network impairment
if [ -n "$2" ]; then
    eval "$2"
fi

# Start packet capture
sudo tcpdump -i $INTERFACE -w "$TEST_DIR/capture.pcap" udp port 5000 &

# Start server and clients
python3 Server.py &
sleep 2
for i in 1 2 3 4; do
    python3 client.py &
done

# Run for 60 seconds
sleep 60

# Cleanup
pkill -f "python.*Server.py"
pkill -f "python.*client.py"
sudo tc qdisc del dev $INTERFACE root 2>/dev/null

# Collect results
cp server_log.csv client_*_log.csv "$TEST_DIR/"
```

### 6.4 Windows Alternative
Use **Clumsy** (https://jagt.github.io/clumsy/) GUI to simulate:
- Lag (delay)
- Drop (packet loss)
- Throttle (bandwidth limit)

---

## 7. Example Use Case Walkthrough

### 7.1 Session Trace

| Time (ms) | From | To | Message | Details |
|-----------|------|----|---------|---------||
| 0 | Client1 | Server | INIT | Connection request |
| 3 | Server | Client1 | INIT_ACK | `{client_id: 1}` |
| 50 | Server | All | SNAPSHOT | `{sid: 1, full: true, grid: all UNCLAIMED}` |
| 100 | Server | All | SNAPSHOT | `{sid: 2, changes: []}` |
| 125 | Client1 | Server | EVENT | `{cell: 12, client_id: 1, ts: 125}` |
| 128 | Server | Client1 | ACK | `{cell: 12, owner: 1}` |
| 150 | Server | All | SNAPSHOT | `{sid: 3, changes: [[2,2,1]], redundant: [sid:2]}` |
| ... | ... | ... | ... | ... |
| 15000 | Server | All | GAME_OVER | `{winner: [1], final_grid: ...}` |

### 7.2 Packet Capture Example (Wireshark)

```
Frame 42: 85 bytes on wire
UDP Src Port: 5000  Dst Port: 54321
Data (57 bytes):
  4e 52 53 48  01 02 00 00  00 03 00 00  00 04  // NRSH, v1, SNAPSHOT, sid=3
  00 00 01 8d  3b 2a 5c 80  00 1d        // timestamp, payload_len=29
  a1 b2 c3 d4                            // checksum
  00 7b 22 66  75 6c 6c 22  ...          // payload: {"full":false...}
```

---

## 8. Limitations & Future Work

### 8.1 Current Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| No encryption | Packets readable in plaintext | Not required for LAN demo |
| No NAT traversal | Requires direct connectivity | Use port forwarding |
| Single server | No fault tolerance | Not in scope |
| Grid compression less effective >50% | Larger packets | Still under 1200 bytes |

### 8.2 Future Improvements

1. **DTLS Encryption** - Secure all packets
2. **Server Redundancy** - Multiple servers with state sync
3. **Spectator Mode** - Read-only clients
4. **Adaptive Update Rate** - Lower rate under congestion
5. **Chat Messages** - Add DATA message type

---

## 9. References

1. **RFC 768** - User Datagram Protocol (UDP)  
   https://tools.ietf.org/html/rfc768

2. **Valve Developer Wiki** - Source Multiplayer Networking  
   https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking

3. **Gaffer On Games** - Networked Physics  
   https://gafferongames.com/post/introduction_to_networked_physics/

4. **Python struct module**  
   https://docs.python.org/3/library/struct.html

5. **Python zlib module**  
   https://docs.python.org/3/library/zlib.html

6. **CRC32 Algorithm**  
   https://en.wikipedia.org/wiki/Cyclic_redundancy_check

---

## Appendix A: netem Commands

```bash
# Add 5% packet loss
sudo tc qdisc add dev eth0 root netem loss 5%

# Add 100ms delay with 10ms jitter
sudo tc qdisc add dev eth0 root netem delay 100ms 10ms

# Combine rate limiting and delay
sudo tc qdisc add dev eth0 root handle 1: tbf rate 2mbit burst 32k latency 400ms
sudo tc qdisc add dev eth0 parent 1:1 netem delay 50ms

# Remove all impairments
sudo tc qdisc del dev eth0 root
```

## Appendix B: Environment

```
OS: Windows 11 / Ubuntu 22.04
Python: 3.11+
Dependencies: pygame (display only), psutil (optional for CPU)
Tested Interfaces: localhost, eth0, WiFi
```

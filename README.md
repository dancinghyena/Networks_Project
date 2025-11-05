# Networks Project — UDP Demo (Server & Client)

This repository contains a minimal **UDP-based** networking demo with a `Server.py` and a `client.py` that exchange messages using a compact header inspired by a mini RFC.

> **Quick start:** open **two terminals**. In one, run the server; in the other, run the client. By default both bind/connect to `127.0.0.1:5005`.

---

## Contents

- `Server.py` – UDP server that receives `INIT` and `DATA` messages and sends `ACK`s.
- `client.py` – UDP client that sends `INIT` and periodic `DATA` messages and listens for `ACK`s.
- `tests/baseline_local.md` – A step-by-step **Baseline local** scenario to verify everything works on your machine.

---

## Requirements

- Python **3.9+** (standard library only: `socket`, `struct`, `time`, `zlib`, `threading`).
- OS: Linux, macOS, or Windows.

No external packages are needed.

---

## Configuration

Both scripts expose simple constants at the top of the files:

```python
SERVER_IP = "127.0.0.1"
SERVER_PORT = 5005
```

You can edit these values directly in both `Server.py` and `client.py` to change where the server listens and where the client sends.

> Tip: Keep them on `127.0.0.1` for local testing first.

---

## Protocol (high level)

The header format matches the comment in the code:

```
!4s B B I I Q H I
└── ─ ─ ─ ─ ─ ─ ─
  protocol_id (4s) = b"NRSH"
  version     (B)  = 1
  msg_type    (B)  = 0=INIT, 1=DATA, 3=ACK
  snapshot_id (I)  = reserved (0 in baseline)
  seq_num     (I)  = sequence number
  timestamp   (Q)  = milliseconds epoch
  payload_len (H)  = bytes in payload
  checksum    (I)  = CRC32 of header-without-checksum + payload
```

Server behavior (summary):
- Prints each `INIT`/`DATA` packet.
- Replies with `ACK` using the same `seq_num`.

Client behavior (summary):
- Sends one `INIT`, then sends periodic `DATA` messages (e.g., a simple text payload).
- Listens on a background thread for `ACK`s and prints them.

---

## How to Run

### 1) Start the server

```bash
cd Networks_Project-main
python Server.py
```

You should see logs like:

```
[Server] listening on 127.0.0.1:5005 ...
```

### 2) Run the client (in a second terminal)

```bash
cd Networks_Project-main
python client.py
```

Expected output includes lines like:

```
[Client] INIT sent, seq 1
[Client] DATA sent, seq 2: "hello"
[Client] ACK received for seq 1
[Client] ACK received for seq 2
```

> If you get a firewall prompt on Windows/macOS, allow Python to receive incoming UDP for the server.

---

## Troubleshooting

- **Address already in use**: change `SERVER_PORT` to a free port (e.g., 5006) in both files.
- **No ACKs show up**: ensure the server is running, and that both files have the same IP/port; check firewall.
- **Garbled decode**: `payload.decode(errors="ignore")` drops undecodable bytes. Keep payloads as UTF‑8 text for the demo.

---

## Next steps (nice to have)

- CLI args (e.g., `--ip`, `--port`) using `argparse` instead of editing constants.
- Add retransmission/timeout logic on the client if an `ACK` isn’t received.
- Persist basic metrics (latency from `timestamp`, packet count, loss).

---

*Generated on 2025-11-04 22:56 *

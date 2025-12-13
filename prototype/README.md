# Networks Project — NetRush (UDP) — Phase 2

This repo contains a **UDP-based client/server game demo** (NetRush) plus **Phase 2** testing artifacts:
- **netem command list** (Linux) for loss/delay/jitter scenarios
- **pcap traces** captured during tests
- **raw measurement CSV logs** from server + clients
- **updated Mini-RFC** (sections 4–7: procedures, reliability, experimental plan)

> **Important:** Phase 2 network impairment testing (NetEm + tcpdump) must be run on **Linux** (recommended: Ubuntu VM).

---

## Repository layout

- `Server.py` — UDP server (game authority + snapshot broadcaster)
- `client.py` — UDP client (renders, sends events, handles reliability + resync)
- `Mini RFC phase 2.pdf` — Mini-RFC document (update required for Phase 2)
- `baseline_local.md` — baseline smoke test instructions

### Phase 2 artifacts

- `tests/netem/`
  - `netem_commands.sh` — list of NetEm scenarios (loss/delay/jitter)
  - `reset_netem.sh` — remove any applied NetEm qdisc
- `tests/pcaps/` — `.pcapng` captures per scenario
- `tests/results/<scenario>/` — raw CSV logs per scenario

---

## Requirements

### Runtime (game)
- Python **3.9+**
- `pygame` for the client UI
  - Windows: `pip install pygame`
  - Ubuntu: `sudo apt install -y python3-pygame`

### Phase 2 testing (Linux only)
- `tc` / NetEm (from `iproute2`)
- `tcpdump`

Ubuntu VM install:
```bash
sudo apt update
sudo apt install -y iproute2 tcpdump python3-pygame

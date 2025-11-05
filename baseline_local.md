# Baseline local — Smoke Test

This scenario verifies the UDP demo works **locally** on `127.0.0.1:5005`.

## Preconditions
- Python 3.9+ installed
- The two files `Server.py` and `client.py` are present
- Local firewall allows Python to bind UDP on port 5005

## Steps

1. **Open Terminal A** and start the server:

   ```bash
   cd Networks_Project-main
   python Server.py
   ```

   **Expected:** A line indicating the server is listening on `127.0.0.1:5005`.

2. **Open Terminal B** and start the client:

   ```bash
   cd Networks_Project-main
   python client.py
   ```

3. **Observe logs**

   On **Terminal B (client)**, you should see:
   - `INIT sent` (with a sequence number)
   - At least one `DATA sent` line
   - One or more `ACK received for seq <n>`

   On **Terminal A (server)**, you should see:
   - `INIT received from 127.0.0.1:<port>`
   - `DATA from 127.0.0.1:<port>: <payload>`
   - Implicit ACKs being sent back

4. **Acceptance criteria**

    Client prints at least one `ACK received` within 2–3 seconds.

    Server prints at least one `INIT` and one `DATA` event.

    No unhandled exceptions in either process.

## Variations (optional)

- **Change port** to `5006` in both files and repeat the test.
- **Send custom payloads** by editing the message string in `client.py` (keep it UTF‑8).

## Cleanup
- Press `Ctrl+C` in both terminals to stop the processes.
- No files or services persist after exit.

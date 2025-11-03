import socket

client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Send a message to the server
client_socket.sendto(b"Hello, server!", ("127.0.0.1", 9999))

# Wait for reply
data, _ = client_socket.recvfrom(1024)
print("Server replied:", data.decode())

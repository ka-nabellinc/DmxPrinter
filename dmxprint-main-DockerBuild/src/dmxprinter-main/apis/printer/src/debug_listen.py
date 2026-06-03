import os
import socket
import time


def listen_commands(port=50002, by_encode='utf-8', delay=0.1):
    try:
        # Get environment variables
        print_host = os.getenv('PRINT_HOST', 'localhost')
        print_port = int(os.getenv('PRINT_PORT', port))
        by_encode = os.getenv('BY_ENCODE', by_encode)
        delay = float(os.getenv('DELAY', delay))

        # Create a socket connection
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((print_host, print_port))
            s.listen()
            print(f"Listening for commands on {print_host}:{print_port}...")

            while True:
                conn, addr = s.accept()
                with conn:
                    print(f"Connected by {addr}")
                    data = conn.recv(1024)
                    if not data:
                        break
                    command = data.decode(by_encode)
                    print(f"Received command: {command}")
                    time.sleep(delay)
                    
    except KeyboardInterrupt as e:
        print("Command sending interrupted by user.")
    except Exception as e:
        print(f"Error listening for commands: {e}")
    finally:
        print(f"Listening stopped on {print_host}:{print_port} with encoding {by_encode}")

if __name__ == "__main__":
    # Example usage
    listen_commands()
    print("Listening for commands successfully.")
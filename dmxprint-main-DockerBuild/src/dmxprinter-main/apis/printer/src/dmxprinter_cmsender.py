import socket
import sys
import os
import json
import time
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)
from envprinter import read
from Command import Command

class Printer:
    def __init__(self):
        """
        Printerクラスの初期化メソッド。
        現在は特に初期化処理は行っていません。
        """
        self.main_host = self.setenviroment('MAIN_HOST')
        self.main_port = int(self.setenviroment('MAIN_PORT'))
        self.print_host = self.setenviroment('PRINT_HOST')
        self.print_port = int(self.setenviroment('PRINT_PORT'))
        self.timeout = 5.0


    def setenviroment(self,name):
        """
        環境変数を設定します。
        """
        env = read(name)
        print(env)
        return env

    def send_fp1000_command(self,command_bytes: bytes, ip: str, port: int, timeout: float = 5.0) -> bytes:
        """
        指定されたIPとポートのFP-1000へコマンドを送信し、応答を返します。
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                print(f"Connecting to FP-1000 at {ip}:{port}...")
                s.connect((ip, port))
                print(f"Connected. Sending command: {command_bytes.hex()}")
                s.sendall(command_bytes)

                # FP-1000からの応答を受信する場合
                response = s.recv(1024) 
                print(f"Received response: {response.decode('utf-8', errors='ignore')}")
                s.close()
                return response
                
        except socket.timeout:
            print(f"Error: Connection or send timed out to {ip}:{port}")
            s.close()
            return b'time'
        except ConnectionRefusedError:
            print(f"Error: Connection refused. Check IP, port, and FP-1000 status.")
            s.close()
            return b'connect'
        except KeyboardInterrupt:
            print("Command sending for printer interrupted by user.")
            s.close()
            return b'interrupted'
        except Exception as e:
            print(f"An error occurred: {e}")
            s.close()
            return b'e'


    def listen_main_commands(self):
        """
        Mainからコマンドを受信するためのソケットをリッスンします。
        """
        try:
            # Get environment variables
            by_encode = 'utf-8'
            delay = 0.1
            received_data = b""
            command = ""
            id = ""
            # Create a socket connection
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.print_host, self.print_port))
                s.listen()
                conn, addr = s.accept() # クライアントからの接続を受け入れる
                with conn:
                    print(f"クライアントが接続しました: {addr}")
                    while True:
                        chunk = conn.recv(1024) # 1024バイトずつ受信
                        if not chunk:
                            break
                        received_data += chunk
                        if b"\n" in received_data:
                            break
                    json_string = received_data.decode(by_encode).strip()  # 受信データをデコードして改行を削除
                    print(f"受信したJSON文字列: {json_string}")
                    data = json.loads(json_string)
                    print(f"Received data: {data.get('command')}, {data.get('job_id')}")
                    #data = conn.recv(1024)
                return data  # コマンドを返す。デフォルトは'none'
        except KeyboardInterrupt as e:
            print("Command listening interrupted by user.")
            return None
        except socket.timeout:
            print(f"Error listening for commands: timed out")
            return None
        except Exception as e:
            print(f"Error listening for commands: {e}")
            return None
        finally:
            print(f"Listening stopped on {self.main_host}:{self.main_port} with encoding {by_encode}")

    def send_main_command(self,command: str):
        """
        Mainにコマンドを送信します。
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.main_host, self.main_port))
                s.sendall(command.encode('ascii', errors='ignore'))
                print(f"Sent command: {command}")
        
        except KeyboardInterrupt:
            print("Command sending for Main interrupted by user.")
        except Exception as e:
            print(f"Senter command: {command}")
            print(f"Sent port: {self.main_port}")
            print(f"Error sending command: {e}")
        finally:
            s.close()

if __name__ == "__main__":
    # Example usage
    self=Printer()
    Pcmd = None
    __host = self.setenviroment('PRINTER_HOST')
    __port = int(self.setenviroment('PRINTER_PORT'))

    command = ""
    job_id = ""
    while True:
       
        try:
            json_obj = self.listen_main_commands()
            if json_obj is None:
                print("None:Error")
                time.sleep(0.001)#負荷軽減用
                continue

            command = json_obj.get('command')
            job_id = json_obj.get('job_id')
            uuid = json_obj.get('uuid')
            #command = command.decode('utf-8', errors='ignore')
            print(f"{command}:{job_id}")

            if command == 'none':
                print("No command received. Exiting.")
            elif command == 'START':
                Pcmd = Command.printer_start_command()
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'STOP':
                Pcmd = Command.printer_stop_command()
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'STATUS':
                Pcmd = Command.printer_status_command()
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'BLOCK_WRITE':
                Pcmd = Command.block_write_command(uuid,job_id)
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'COUNT':
                Pcmd = Command.count_check_command()
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'BLOCK_READ':
                Pcmd = Command.block_read_command(job_id)
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'JOB_ID':
                Pcmd = Command.job_change_command(job_id)
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command == 'PRINTING':
                Pcmd = Command.printing_command()
                print(Pcmd.decode('utf-8', errors='ignore'))
            elif command:
                print(f"Command received: {command}")
            else:
                print("No valid command received. Continuing to listen.")

            if Pcmd:
                responce=self.send_fp1000_command(Pcmd,__host, __port)
                print(f"Response from FP-1000: {responce.decode('utf-8', errors='ignore')}")
                self.send_main_command(responce.decode('utf-8', errors='ignore'))

        except KeyboardInterrupt:
            print("CMSENDER interrupted by user.")
            break
        except Exception as e:
            print(f"Error processing command: {e}")
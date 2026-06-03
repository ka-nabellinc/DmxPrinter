import uuid
import socket
import os
import sys
import csv

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)
from envprinter import read
from uuid2csv import UUID2CSV

class UUIDGenerator:
    def __init__(self):
        self.main_host = self.setenviroment('MAIN_HOST')
        self.main_port = int(self.setenviroment('MAIN_PORT'))
        self.uuid_host = self.setenviroment('UUID_HOST')
        self.uuid_port = int(self.setenviroment('UUID_PORT'))
        self.uuid2csv = UUID2CSV()
        

    def setenviroment(self,name):
        """
        環境変数を設定します。
        """
        env = read(name)
        print(env)
        return env

    def generate_uuid(self) -> str:
        """
        UUIDを生成します。
        """
        return str(uuid.uuid4())
    
    def uuid_to_csv(self,uuid_string: str):
        """
        UUIDをCSVファイルに書き込みます
        """
        self.uuid2csv.write_uuid_to_csv(uuid_string, self.uuid2csv.filename)
 
        return f'{uuid_string}'

    def listen_uuid_commands(self):
        """
        UUID生成コマンドを待ち受ける
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self.uuid_host, self.uuid_port))
                s.listen()
                print(f"Listening for UUID commands on {self.uuid_host}:{self.uuid_port}...")
                while True:
                    conn, addr = s.accept()
                    with conn:
                        # Mainからコマンドを受け付ける
                        print(f"Connected by {addr}")
                        data = conn.recv(1024)
                        if not data:
                            pass
                        elif data.decode('utf-8') == 'STOP':
                            print("end command received. Stopping UUID generation.")
                        else:
                            # UUID生成
                            uuid_string = self.generate_uuid()
                            # CSV追記 一時無効化
                            # uuid_csv = self.uuid_to_csv(uuid_string)
                            conn.sendall(uuid_string.encode('utf-8'))
                            
        except KeyboardInterrupt:
            print("UUID command listening interrupted by user.")
        except Exception as e:
            print(f"Error listening for UUID commands: {e}")
    

if __name__ == "__main__":
    # Example usage
    self = UUIDGenerator()
    UUIDGenerator.listen_uuid_commands(self)
    
 
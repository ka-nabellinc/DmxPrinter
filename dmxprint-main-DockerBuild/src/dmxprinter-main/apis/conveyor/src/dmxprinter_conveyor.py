import os
import socket
import time
import sys
from gpiozero import LED

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)
from envprinter import read
#from printer.dmxprinter_cmsender import Printer

class Convayor:
    def __init__(self):
        self.pin = int(self.setenviroment('CMOTER_PORT'))
        self.__host = self.setenviroment('PRINTER_HOST')
        self.__port = int(self.setenviroment('PRINTER_PORT'))
        self.main_host = self.setenviroment('MAIN_HOST')
        self.main_port = int(self.setenviroment('MAIN_PORT'))
        self.convayor_host = self.setenviroment('CONVAYOR_HOST')
        self.convayor_port = int(self.setenviroment('CONVAYOR_PORT'))
        self.conveyor_motor = LED(self.pin)
        self.timeout = 5.0

    def setenviroment(self,name:str):
        """
        環境変数を設定します。
        """
        env = read(name)
        print(env)
        return env

    def starter_command(self):
        """
        コンベア起動処理
        """
        try:
            # GPIOピンの設定
            self.conveyor_motor.on()  # コンベアを起動
            print("Conveyor started.")
        except Exception as e:
            pass
        return 'START'

    def stop_command(self):
        """
        コンベア停止処理
        """
        try:
            # GPIOピンの設定
            self.conveyor_motor.off()  # コンベアを起動
            print("Conveyor started.")
        except Exception as e:
            pass
        return 'STOP'
    
    def starter(self):
        """
        コンベアを起動する 
        """

        print("Printer is ready. Starting conveyor...")
        print("Conveyor started.")
        "コンベア起動処理"
        Ccommand = self.starter_command()
      
        return Ccommand
    
    def stoper(self):
        """
        コンベアを停止する
        """
        "コンベア停止処理"
        Ccommand = self.stop_command()
        return Ccommand
        
    def listen_response(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
                s.settimeout(self.timeout)
                s.bind((self.convayor_host, self.convayor_port))
                s.listen()
                print(f"Listening for UUID commands on {self.main_host}:{self.main_port}...")
                conn, addr = s.accept()
                with conn:
                    print(f"Connected by {addr}")
                    data = conn.recv(1024)
                    if data is None:
                        data = "N"
                    data_str = data.decode('utf-8',errors='ignore')
                    print(data_str)
                    s.close()
                    return data_str
        except KeyboardInterrupt:
            print("listening interrupted by user.")
            print(f"Listening for Conveyor commands on {self.main_host}:{self.main_port}...")
        except Exception as e:
            print(f"Error listening for Conveyor commands: {e}")
        
    def sender(self,mes):
        """
        コンベアを起動する
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.main_host, self.main_port))
                start_command = f'{mes}'
                start_command = start_command.encode('ascii', errors='ignore')
                s.sendall(start_command)
                s.close()
                print(f"Sent start command to conveyor at {self.main_host}:{self.main_port}")
        except Exception as e:
            print(f"Error starting conveyor: {e}")

if __name__ == "__main__":
    # Example usage
    convayor = Convayor()
    while True:
        main_command = convayor.listen_response()

        if main_command is None:
            main_command = 'none'

        if main_command == 'START':
            # コンベアを起動
            convayor_command = convayor.starter()
            print(f"Conveyor command: {convayor_command}")  
        elif main_command == 'STOP':
            # コンベアを停止
            convayor_command = convayor.stoper()
            print(f"Conveyor stop command: {convayor_command}")
        else:
            convayor_command ='none'
            print(f'Convayor none:{convayor_command}')
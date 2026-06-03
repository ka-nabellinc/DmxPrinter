import uuid
import socket
import os
import sys
import csv
import datetime

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)
from envprinter import read

class UUID2CSV:
    def __init__(self):
        self.time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.env = self.setenviroment('CSV_DIR')
        self.filename = self.generate_csv_filename(self.time)

    def setenviroment(self, name: str) -> str:
        """
        環境変数を設定します。
        """
        env = read(name)
        print(env)
        return env



    def generate_csv_filename(self,dtime):
        """
        現在の日時を含むCSVファイル名を生成します。
        """
        time = dtime
        filename = os.path.join(self.env, f"Result_{time}.csv")
        
        return filename

    def write_uuid_to_csv(self, uuid_string: str,filename) -> None:
        """
        UUIDをCSVファイルに書き込みます。
        """
        with open(filename, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([uuid_string])
        print(f"UUID {uuid_string} written to {self.filename}")

if __name__ == "__main__":
    # Example usage
    uuid_to_csv = UUID2CSV()
    uuid_to_csv.write_uuid_to_csv("test", uuid_to_csv.filename)
    
    print(f"CSV file {uuid_to_csv.filename} created with UUID")
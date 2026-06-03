


class Command:
    def block_read_command(job_id:str):
        block = f"RX,JOB={job_id},BLK=01,CharacterString\r"
        return block.encode('ascii', errors='ignore')

    def block_write_command(uuid_string:str, job_id:str):
        """
        UUIDとJOB IDをブロック形式に変換します。
        UUIDをブロック毎に変換
        (BLK=0:UUID前半,BLK=1:DMX,BLK=2:UUID後半)
        スリーブのレイアウトを指定
        (JOB=0:状態A,JOB=1:状態B,JOB=2状態C,JOB=3:状態D)
        命令フォーマットに変換
        （WX,JOB=,BLK=00,CharacterString={UUID前半},BLK=01,CharacterString={UUID},BLK=02,CharacterString={UUID後半}\r）
        """
        # UUIDの文字列長を取得します
        uuid_length = len(uuid_string)
        
        # 中央のインデックスを計算します
        # 例えば、36文字のUUIDの場合、half_length は 18 になります。
        half_length = uuid_length // 2
        
        # 文字列を前半部分と後半部分に分割します
        first_half = uuid_string[:half_length]
        second_half = uuid_string[half_length:]

        block = f"WX,JOB={job_id},BLK=00,CharacterString={first_half},BLK=01,CharacterString={uuid_string},BLK=02,CharacterString={second_half}\r"
        return block.encode('ascii', errors='ignore')
   
    def printer_status_command():
        """
        プリンターのステータスを取得するコマンドを生成します。
        """
        command = "RX,Ready\r"
        return command.encode('ascii', errors='ignore')

    def printer_stop_command():
        """
        プリンター印字を停止するコマンドを生成します。
        """
        command = "WX,TriggerLock=1\r"
        return command.encode('ascii', errors='ignore')

    def printer_start_command():
        """ 
        プリンター印字を開始するコマンドを生成します。
        """
        command = "WX,TriggerLock=0\r"
        return command.encode('ascii', errors='ignore')

    def job_change_command(job_id: str):
        """
        ジョブIDを変更するコマンドを生成します。
        """
        command = f"WX,JobNo={job_id}\r"
        return command.encode('ascii', errors='ignore')

    def count_check_command():
        command =f"RX,CumulativeMarkingCount\r"
        return command.encode('ascii', errors='ignore')
    
    def printing_command():
        command = f"WX,StartMarking\r"
        return command.encode('ascii', errors='ignore')
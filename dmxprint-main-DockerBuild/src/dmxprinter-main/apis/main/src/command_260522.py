import socket
import json
import os
import configparser
import sys
import codecs
import threading
import time
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)

from envprinter import read
from sleevePrinterInterface import sleevePrinterInterface

config_ini = configparser.ConfigParser()
path = os.path.join(os.path.dirname(__file__), 'config.ini')
config_ini.read(path,encoding='utf-8')

class Command:
    """
    各コンテナへのコマンドを管理するクラス
    """
    def __init__(self):
        self.print_host = config_ini['PRINTER']['PRINTER_HOST']
        self.print_port = int(config_ini.get('PRINTER','PRINTER_PORT'))
        self.printRank = config_ini['PRINTER']['PRINTER_RANK']

        self.uuid_host = config_ini['UUID']['UUID_HOST']
        self.uuid_port = int(config_ini.get('UUID','UUID_PORT'))

        self.conveyor_host = config_ini['CONVEYOR']['CONVEYOR_HOST']
        self.conveyor_port = int(config_ini.get('CONVEYOR','CONVEYOR_PORT'))

        # NG検査装置（Ethernet/TCP）
        # config.ini [NG_SENSOR] と環境変数で上書き可能
        if config_ini.has_section('NG_SENSOR'):
            self.ng_sensor_host = os.getenv(
                'NG_SENSOR_HOST',
                config_ini.get('NG_SENSOR', 'NG_HOST', fallback=''),
            ).strip()
            self.ng_sensor_port = int(os.getenv(
                'NG_SENSOR_PORT',
                config_ini.get('NG_SENSOR', 'NG_PORT', fallback='9004'),
            ))
            self.ng_sensor_send = os.getenv(
                'NG_SENSOR_SEND',
                config_ini.get('NG_SENSOR', 'NG_SEND', fallback=''),
            )
        else:
            self.ng_sensor_host = os.getenv('NG_SENSOR_HOST', '').strip()
            self.ng_sensor_port = int(os.getenv('NG_SENSOR_PORT', '9004'))
            self.ng_sensor_send = os.getenv('NG_SENSOR_SEND', '')

        # config.ini で 'utf-8' のようにクォート付きでも動くように正規化
        self.by_encode = str(config_ini['DEBUG']['DEBUG_BY_ENCODE']).strip().strip("'\"")
        self.delay = float(config_ini.get('DEBUG','DEBUG_DELAY'))
        self.timeout = float(config_ini.get('DEBUG','DEBUG_TIMEOUT'))
        self.main_host = config_ini['DEBUG']['DEBUG_HOST']
        self.main_port = int(config_ini.get('DEBUG','DEBUG_PORT'))

        # NG センサー LON 応答タイムアウト（秒）
        # 初回のみ長めに待ち（センサー起動・接続確立のバッファ）、
        # 2回目以降は短いタイムアウトでキュー詰まりを防ぐ
        # 環境変数で個別に上書き可能
        # TCP 接続タイムアウト（DEBUG_TIMEOUT とは独立）
        self.ng_connect_timeout   = float(os.getenv('NG_CONNECT_TIMEOUT',   '0.5'))
        # LON 応答タイムアウト：ワーク1枚目 / 2枚目以降で切り替え
        self.ng_lon_timeout_first = float(os.getenv('NG_LON_TIMEOUT_FIRST', '4.5'))
        self.ng_lon_timeout       = float(os.getenv('NG_LON_TIMEOUT',       '3.0'))
        self._ng_first_poll       = True   # ワーク1枚目フラグ（stop()でリセット）

        # NG センサ 永続接続
        # SR-X は同時 TCP 接続を 1 本しか受け付けないため接続を使い回す
        self._ng_sock      = None
        self._ng_sock_lock = threading.Lock()
        self._ng_recv_buf  = bytearray()


    # ------------------------------------------------------------------
    # NG センサ 永続接続ヘルパー
    # ------------------------------------------------------------------

    def _ng_connect(self) -> bool:
        """センサへ接続（既に接続済みなら何もしない）。成功時 True。"""
        if self._ng_sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.ng_connect_timeout)   # DEBUG_TIMEOUT から独立した短いタイムアウト
            s.connect((self.ng_sensor_host, self.ng_sensor_port))
            self._ng_sock     = s
            self._ng_recv_buf = bytearray()
            print(f"NG sensor: 接続完了 {self.ng_sensor_host}:{self.ng_sensor_port}", flush=True)
            # 接続成功 = このワークが「1枚目」として扱われる → 次回から 2枚目以降タイムアウト
            if self._ng_first_poll:
                print(
                    f"NG sensor: ワーク1枚目接続確立 → LON {self.ng_lon_timeout_first}s待機、"
                    f"以降 {self.ng_lon_timeout}s に切り替わります",
                    flush=True,
                )
            return True
        except Exception as e:
            print(f"NG sensor: 接続失敗 ({self.ng_connect_timeout}s TO) — {e}", flush=True)
            self._ng_sock = None
            return False

    def _ng_disconnect(self):
        """センサとの接続を閉じる。"""
        if self._ng_sock is not None:
            try:
                self._ng_sock.close()
            except Exception:
                pass
            self._ng_sock     = None
            self._ng_recv_buf = bytearray()

    def _ng_send(self, cmd: bytes) -> bool:
        """コマンドを送信。失敗時は切断して False を返す。"""
        try:
            self._ng_sock.sendall(cmd)
            return True
        except Exception as e:
            print(f"NG sensor: 送信エラー — {e}")
            self._ng_disconnect()
            return False

    def _ng_recv_line(self, timeout: float | None = None) -> str | None:
        """
        [CR] 区切りで 1 行読む。
        タイムアウト / 切断時は None を返し、ソケットを閉じる。
        timeout: None の場合は self.timeout（DEBUG_TIMEOUT）を使用
        """
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            if b"\r" in self._ng_recv_buf:
                idx  = self._ng_recv_buf.index(b"\r")
                line = self._ng_recv_buf[:idx].decode('utf-8', errors='ignore').strip()
                del self._ng_recv_buf[:idx + 1]
                return line

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("NG sensor: 受信タイムアウト", flush=True)
                # ソケットは切断しない — 呼び出し元が引き続き LOFF を送信できるよう保持する
                return None

            self._ng_sock.settimeout(min(remaining, 0.5))
            try:
                chunk = self._ng_sock.recv(1024)
                if not chunk:
                    print("NG sensor: 接続切断を検知")
                    self._ng_disconnect()
                    return None
                self._ng_recv_buf.extend(chunk)
            except socket.timeout:
                pass
            except OSError as e:
                print(f"NG sensor: 受信エラー — {e}")
                self._ng_disconnect()
                return None

    def _ng_send_reset(self) -> str | None:
        """
        タイムアウト / ERROR 後のリカバリー処理。

        SR-X は TCP 切断後も LON（読み取り開始）状態が継続するため、
        RESET だけでは白ランプが消えず通常動作に戻らない。
        正しい順序:
          1. 既存ソケットをそのまま使って LOFF\r を送信（再接続しない）
             ※ LON タイムアウト時はソケットを切断せずに保持しているため
               そのまま送信できる
          2. LOFF\r への応答を読む
             センサー仕様: 応答が ERROR の場合は不良品確定
          3. RESET\r を送信して統計・エラー状態をクリア（応答は読み捨て）
          4. ソケットを閉じて次回 poll で必ず再接続させる

        戻り値: LOFF への応答文字列 / None（送信失敗・受信タイムアウト）
        """
        print("NG sensor: リカバリー開始 (LOFF → RESET)", flush=True)

        # LOFF 送信（既存ソケットを使用・再接続しない）
        print("NG sensor: LOFF\\r 送信", flush=True)
        if not self._ng_send(b"LOFF\r"):
            # 送信失敗 = ソケットが破損 → リカバリー不可
            print("NG sensor: LOFF 送信失敗 — ソケット破損", flush=True)
            return None

        # LOFF への応答を読む（短いタイムアウトで待つ）
        loff_response = self._ng_recv_line(timeout=0.5)
        if loff_response is not None:
            print(f"NG sensor: LOFF 応答 {loff_response!r}", flush=True)
        else:
            print("NG sensor: LOFF 応答なし（タイムアウト）", flush=True)

        # RESET 送信（応答は読み捨て・短いタイムアウト）
        if self._ng_send(b"RESET\r"):
            self._ng_recv_line(timeout=0.5)

        # ソケットを閉じて次回 poll で新規接続させる
        self._ng_disconnect()
        print("NG sensor: リカバリー完了 — 次回 poll で再接続します", flush=True)

        return loff_response

    # ------------------------------------------------------------------
    # NG センサ ポーリング
    # ------------------------------------------------------------------

    def ng_sensor_poll(self):
        """
        NG検査装置へ 1 回問い合わせる（永続接続方式）。

        動作:
          1. 未接続なら自動接続する。
          2. LON\\r を送信してセンサの応答を待つ。
          3. タイムアウト時 → RESET\\r を送信してセンサ状態をクリア → None を返す。
          4. ERROR 受信時  → RESET\\r を送信してセンサ状態をクリア → 'ERROR' を返す。
          5. 正常受信時    → デコード済み文字列を返す。

        返り値: デコード済み文字列 / None（未設定・接続失敗・タイムアウト）
        """
        if not self.ng_sensor_enabled:
            return None

        with self._ng_sock_lock:
            # 未接続なら接続を試みる
            if not self._ng_connect():
                return None

            # LON 送信（失敗時は再接続して1回だけリトライ）
            print("NG sensor: LON\\r 送信", flush=True)
            if not self._ng_send(b"LON\r"):
                if not self._ng_connect():
                    return None
                if not self._ng_send(b"LON\r"):
                    return None

            # 応答受信（初回は長めのタイムアウト、2回目以降は短いタイムアウト）
            if self._ng_first_poll:
                lon_timeout = self.ng_lon_timeout_first
                print(
                    f"NG sensor: LON待機 {lon_timeout}s [初回]"
                    f" → 次回から {self.ng_lon_timeout}s に切り替わります",
                    flush=True,
                )
                self._ng_first_poll = False
            else:
                lon_timeout = self.ng_lon_timeout
                print(f"NG sensor: LON待機 {lon_timeout}s [2回目以降]", flush=True)
            result = self._ng_recv_line(timeout=lon_timeout)

            # タイムアウト（result is None）→ LOFF を送信して応答を確認
            # センサー仕様: LOFF 応答が ERROR → 不良品確定
            if result is None:
                loff_resp = self._ng_send_reset()
                if loff_resp is not None and "ERROR" in loff_resp.upper():
                    print(
                        f"NG sensor: LONタイムアウト後 LOFF応答 {loff_resp!r} → NG確定",
                        flush=True,
                    )
                    return loff_resp   # "ERROR" を返す → main.py が stop() を呼ぶ
                return None            # 接続失敗 / LOFF も無応答 → main.py が None で stop()

            # ERROR 受信 → RESET してセンサ状態をクリア
            if result.upper() == "ERROR":
                print("NG sensor: ERROR受信 → RESET\\r 送信", flush=True)
                self._ng_send_reset()
                return result

            # 正常終了: 読取モードを終了（次回 LON を受け付け可能にする）
            print(f"NG sensor: 読取結果 {result!r} → LOFF\\r", flush=True)
            if self._ng_send(b"LOFF\r"):
                # センサーは LOFF に応答しないことがあるため、タイムアウトは最小限にする
                self._ng_recv_line(timeout=0.1)

            return result

    def ng_reset_first_poll(self):
        """
        ジョブ停止（stop）時に呼び出す。
        次のジョブの1枚目ワークで再び長いタイムアウトが使われるようにリセットする。
        """
        self._ng_first_poll = True
        print("NG sensor: 初回タイムアウトをリセット → 次の1枚目は"
              f" {self.ng_lon_timeout_first}s で待機します", flush=True)

    @property
    def ng_sensor_enabled(self):
        return bool(self.ng_sensor_host)


    def printer_status(self):
        """
        dmxprinterに印刷機のステータスを問い合わせる
        """
        command_bytes = {
                    'command': 'STATUS',
                    'job_id':'none'
                }
        command_bytes = json.dumps(command_bytes).encode(self.by_encode)
        print(command_bytes)
        try:
            # dmxprinter-cmsenderに印刷機の状態確認コマンドを送信
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                print(f"Connecting to FP-1000 at {self.print_host}:{self.print_port}...")
                s.connect((self.print_host, self.print_port))
                print(f"Connected. Sending command: {command_bytes.hex()}")
                s.sendall(command_bytes)

                return command_bytes
        except socket.timeout:
            print(f"Error: Connection or send timed out to {self.print_host}:{self.print_port}")
            s.close()
            return b't'
        except ConnectionRefusedError:
            print(f"Error: Connection refused. Check IP, port, and FP-1000 status.")
            s.close()
            return b'c'
        except KeyboardInterrupt:
            print("Command sending for printer interrupted by user.")
            s.close()
            return b'i'
        except Exception as e:
            print(f"An error occurred: {e}")
            s.close()
            return b'e'
        finally:
            # クリーンアップ
            s.close()

    def user_status(self,qty,jobNum):
        """
        印刷情報を取得する
        """
        status = str(jobNum)
        count = str(qty)
        try:
            while status not in self.printRank:
                input("Press Enter")
                status = str(jobNum)
                if status.isdigit() and 0 < int(status) <= 6:
                    print(f"状態{status}を選択しました。")
                else:
                    print("無効な入力です。1から5の数字を入力してください。")

            while int(count) < 1:
                print("印刷枚数を入力してください:")
                count = str(qty)
                if count.isdigit() and int(count) > 0:
                    print(f"印刷枚数{count}を選択しました。")
                else:
                    print("無効な入力です。1以上の数字を入力してください。")
        except KeyboardInterrupt:
            print("User input interrupted by user.")
            exit()
        except Exception as e:
            print(f"An error occurred while getting user status: {e}")
        return [status,count]

    def start_conveyor(self):
        """
        コンベアを起動する
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.conveyor_host, self.conveyor_port))
                start_command = b'START'
                s.sendall(start_command)
                s.close()
                print(f"Sent start command to conveyor at {self.conveyor_host}:{self.conveyor_port}")
        except Exception as e:
            print(f"Error starting conveyor: {e}")

    def stop_conveyor(self):
        """
        コンベアを停止する
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.conveyor_host, self.conveyor_port))
                stop_command = b'STOP'
                s.sendall(stop_command)
                s.close()
                print(f"Sent stop command to conveyor at {self.conveyor_host}:{self.conveyor_port}")
        except Exception as e:
            print(f"Error stopping conveyor: {e}")

    def printer_send_command(self, command: str,job_id: str,uuid: str):
        """
        dmxprinterにコマンドを送信する
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.connect((self.print_host, self.print_port))
                command_bytes = {
                    'command': command,
                    'job_id': job_id,
                    'uuid': uuid
                }
                command_bytes = json.dumps(command_bytes).encode(self.by_encode)

                message_bytes = command_bytes
                s.sendall(message_bytes)
        except KeyboardInterrupt:
            print("Command sending interrupted by user.")
        except Exception as e:
            print(f"Error sending command to printer: {e}")
        finally:
            # クリーンアップ
            s.close()

    def uuid_gen(self,command: str):
        """
        UUID生成を依頼し受信する、CSV生成コンテナにも並行して送信する
        コマンド内訳：GEN <UUID生成コマンド> STOP <UUID停止コマンド>
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.uuid_host, self.uuid_port))
                uuid_command = command.encode(self.by_encode)
                s.sendall(uuid_command)
                print(f"Sent UUID command to {self.uuid_host}:{self.uuid_port}")

                # UUIDの応答を受信する場合
                response = s.recv(1024)
                response = response.decode('utf-8', errors='ignore')
                return response
        except Exception as e:
            print(f"Error sending UUID command: {e}")

    def listen_response(self):
        """
        各コンテナからのレスポンスを取得
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.main_host, self.main_port))
                s.listen()
                print(f"Listening commands on {self.main_host}:{self.main_port}...")
                conn, addr = s.accept()
                with conn:
                    print(f"Connected by {addr}")
                    data = conn.recv(1024)
                    if data is None:
                        data = "none"
                    data_str = data.decode('utf-8',errors='ignore')
                    print(data_str)
                    s.close()
                    return data_str
        except KeyboardInterrupt:
            print("listening interrupted by user.")
            print(f"Listening for commands on {self.main_host}:{self.main_port}...")
        except Exception as e:
            print(f"Error listening for commands: {e}")
        finally:
            s.close()

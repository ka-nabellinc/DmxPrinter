from gpiozero import Button
import time
import os
import configparser
import sys
import threading
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)

from command import Command
from sleevePrinterInterface import sleevePrinterInterface

config_ini = configparser.ConfigParser()
path = os.path.join(os.path.dirname(__file__), 'config.ini')
config_ini.read(path, encoding='utf-8')

INPUT_PIN  = int(os.getenv("INPUT_PIN",  "23"))   # スリーブ検知センサー
INPUT_PIN2 = int(os.getenv("INPUT_PIN2", "24"))   # NGセンサー
PULL_UP_RESISTOR = False
# 23ピン割り込みでマーカーフラグを立てる前に、直近の印刷完了から最低空ける秒数
MARKER_MIN_INTERVAL_AFTER_PRINT_SEC = float(
    os.getenv("MARKER_MIN_INTERVAL_AFTER_PRINT_SEC", "1.2")
)
# ジョブ開始直後は電源立ち上がり・コンベア起動ノイズで偽検知しやすいため、この秒数はマーカーを無視
MARKER_IGNORE_AFTER_JOB_START_SEC = float(
    os.getenv("MARKER_IGNORE_AFTER_JOB_START_SEC", "2.5")
)

COUNTER_LOCK = threading.Lock()
FLAGMENT = 0


class main:
    global FLAGMENT

    def __init__(self):
        self.print_host    = config_ini['PRINTER']['PRINTER_HOST']
        self.print_port    = int(config_ini.get('PRINTER', 'PRINTER_PORT'))
        self.printRank     = config_ini['PRINTER']['PRINTER_RANK']
        self.uuid_host     = config_ini['UUID']['UUID_HOST']
        self.uuid_port     = int(config_ini.get('UUID', 'UUID_PORT'))
        self.conveyor_host = config_ini['CONVEYOR']['CONVEYOR_HOST']
        self.conveyor_port = int(config_ini.get('CONVEYOR', 'CONVEYOR_PORT'))
        self.by_encode     = config_ini['DEBUG']['DEBUG_BY_ENCODE']
        self.delay         = float(config_ini.get('DEBUG', 'DEBUG_DELAY'))
        self.timeout       = float(config_ini.get('DEBUG', 'DEBUG_TIMEOUT'))

        self.Commander = Command()

        self.count     = 0
        self.printed   = 0
        self.flag      = 0
        self.lastid    = "none"
        self.starter   = 0
        self.initstate = 0

        self.spi = sleevePrinterInterface.sleevePrinterInterface(
            port      = 8989,
            startFunc = self.start,
            stopFunc  = self.stop,
        )

        # ★修正①: Button を __init__ で一度だけ生成
        # run() 内で毎回 Button() を new するとインスタンス生成の
        # わずかな時間で短パルスを取りこぼす。
        self._sensor_lock     = threading.Lock()
        self._marker_detected = False
        self._ng_detected     = False
        self._last_print_complete_monotonic = None  # time.monotonic() at last print completion
        self._marker_ignore_until = 0.0  # time.monotonic() 未満はマーカー無視

        self.marker_button = Button(INPUT_PIN,  pull_up=PULL_UP_RESISTOR)
        self.ng_button     = Button(INPUT_PIN2, pull_up=PULL_UP_RESISTOR)

        # ★修正②: when_pressed コールバックを登録
        # gpiozero の内部スレッドが GPIO 割り込みを監視し、
        # センサーが一瞬でも ON になった瞬間にコールバックが呼ばれる。
        # is_pressed のポーリングと違い、短パルスも確実に捕捉できる。
        self.marker_button.when_pressed = self._on_marker_detected
        self.ng_button.when_pressed     = self._on_ng_detected

    def _on_marker_detected(self):
        """スリーブ検知センサーが反応したらフラグを立てる（印刷完了直後は間隔が空くまで無視）"""
        with self._sensor_lock:
            now = time.monotonic()
            if now < self._marker_ignore_until:
                return
            if self._last_print_complete_monotonic is not None:
                if (
                    now - self._last_print_complete_monotonic
                    < MARKER_MIN_INTERVAL_AFTER_PRINT_SEC
                ):
                    return
            self._marker_detected = True

    def _on_ng_detected(self):
        """NG検知センサーが反応したらフラグを立てる"""
        with self._sensor_lock:
            self._ng_detected = True

    def start(self):
        self.starter = 1
        with self._sensor_lock:
            self._marker_detected = False
            self._marker_ignore_until = (
                time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
            )
        print("DMX Printer started.")

    def stop(self):
        self.stoping()
        self.count   = 0
        self.printed = 0
        self.flag    = 0
        self.lastid  = "none"
        with self._sensor_lock:
            self._last_print_complete_monotonic = None
            self._marker_ignore_until = 0.0
        print(self.count)

    def getPrinted(self):
        printed = int(input("Printed: "))
        self.spi.job.printed = printed
        if printed >= self.spi.job.qty:
            self.spi.job.finished = True

    def run(self):
        job_id = str(self.spi.job.jobNum)
        print(f"Job ID: {job_id}")

        if self.initstate == 0:
            printer_status = self.Commander.printer_status()
            listen = self.Commander.listen_response()
            print(f"Printer status: {printer_status.decode('utf-8', errors='ignore')}")
            print(listen)

            command = "JOB_ID"
            self.Commander.printer_send_command(command, job_id, 'none')
            print(f"Job ID changed to {job_id}")
            time.sleep(0.1)

            command = "START"
            self.Commander.printer_send_command(command, job_id, 'none')

            self.Commander.start_conveyor()
            print(f"DMX Printer job started. Conveyor started.:{listen}")
            self.initstate = 1
            # 初回コンベア起動直後のノイズで立ったマーカーを捨て、無視期間を延長
            with self._sensor_lock:
                self._marker_detected = False
                until = time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
                if until > self._marker_ignore_until:
                    self._marker_ignore_until = until
            time.sleep(0.1)

        uuid = "none"

        if self.starter == 1:
            self.Commander.start_conveyor()

        try:
            print("-----------STATUS received------------")
            self.Commander.printer_send_command("STATUS", job_id, uuid)
            self.status_responce = self.Commander.listen_response()
            self.status_responce = self.status_responce.split(',')[2]
            print(f"Bresponce:{self.status_responce}")
            print(f"flag:{self.flag}")

            if self.status_responce == 'none':
                print("STATUS not received. Retrying...")
                return

            elif self.status_responce == '0\r':
                if self.flag == 0:
                    # ★修正③: is_pressed ポーリングをやめ、フラグで判定
                    # フラグを読んだ後は必ずクリアする（1パルス1処理）
                    with self._sensor_lock:
                        marker_hit = self._marker_detected
                        if marker_hit:
                            self._marker_detected = False

                    if marker_hit:
                        uuid = self.Commander.uuid_gen('GEN')
                        print(f"UUID:{uuid}")

                        job_id = str(self.spi.job.jobNum)
                        self.Commander.printer_send_command("BLOCK_WRITE", job_id, uuid)
                        responce = self.Commander.listen_response()
                        print(f"responce:{responce}")
                        time.sleep(0.1)

                        print("印刷開始")
                        self.flag = 1
                        self.Commander.printer_send_command("PRINTING", job_id, uuid)
                        responce_print = self.Commander.listen_response()
                        print(f"responce:{responce_print}")
                        if responce_print and "WX,OK" in responce_print:
                            self.printed += 1
                            self.spi.job.printed = self.printed
                        else:
                            self.flag = 0
                else:
                    self.flag = 0

            elif self.status_responce == '1\r':
                if self.flag == 1:
                    with self._sensor_lock:
                        self._last_print_complete_monotonic = time.monotonic()
                self.flag = 0

            elif self.status_responce == '2\r':
                if self.flag == 1:
                    with self._sensor_lock:
                        self._last_print_complete_monotonic = time.monotonic()
                self.flag = 0
                # ★修正④: NG検知も同様にフラグで判定
                with self._sensor_lock:
                    ng_hit = self._ng_detected
                    if ng_hit:
                        self._ng_detected = False

                if ng_hit:
                    time.sleep(0.2)
                    self.Commander.stop_conveyor()
                    print("Conveyor stopped.")
                    self.starter = 0

                self.spi.job.printed = self.printed

            time.sleep(0.05)

        except KeyboardInterrupt:
            print("DMX Printer interrupted by user.")
            exit()
        except Exception:
            pass

    def stoping(self):
        self.Commander.uuid_gen('STOP')
        print(f"UUID generation stopped.")
        self.Commander.printer_send_command("STOP", self.spi.job.jobNum, 'none')
        self.Commander.stop_conveyor()
        print("Conveyor stopped.")
        self.starter   = 0
        self.initstate = 0


if __name__ == "__main__":
    main_instance = main()

    while True:
        try:
            if main_instance.spi.job.finished == True:
                main_instance.initstate = 0

            if main_instance.starter == 1:
                main_instance.run()

            if main_instance.spi.job.qty > 0 and main_instance.printed >= main_instance.spi.job.qty:
                time.sleep(2)
                main_instance.stoping()
                main_instance.spi.job.finished = True

        except KeyboardInterrupt:
            print(f"EXIT")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            continue   # ★ break → continue（エラーで即終了しない）

    print("DMX Printer successfully.")
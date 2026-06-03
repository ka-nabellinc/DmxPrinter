from gpiozero import Button
import time
import os
import configparser
import sys
import threading
import queue
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)

from command import Command
from sleevePrinterInterface import sleevePrinterInterface

config_ini = configparser.ConfigParser()
path = os.path.join(os.path.dirname(__file__), 'config.ini')
config_ini.read(path, encoding='utf-8')

INPUT_PIN  = int(os.getenv("INPUT_PIN",  "23"))   # スリーブ検知センサー（GPIO）
PULL_UP_RESISTOR = False
# 23ピン割り込みでマーカーフラグを立てる前に、直近の印刷完了から最低空ける秒数
MARKER_MIN_INTERVAL_AFTER_PRINT_SEC = float(
    os.getenv("MARKER_MIN_INTERVAL_AFTER_PRINT_SEC", "0.6")
)
# ジョブ開始直後は電源立ち上がり・コンベア起動ノイズで偽検知しやすいため、この秒数はマーカーを無視
MARKER_IGNORE_AFTER_JOB_START_SEC = float(
    os.getenv("MARKER_IGNORE_AFTER_JOB_START_SEC", "0.6")
)
# 印字トリガー（marker_hit）から NG 読み取り（LON\r）までの遅延秒数
# ワーク1枚目（センサー・コンベア起動バッファ）と2枚目以降で別々に設定
NG_DELAY_AFTER_MARKER_SEC_FIRST = float(os.getenv("NG_DELAY_AFTER_MARKER_SEC_FIRST", "5.3"))
NG_DELAY_AFTER_MARKER_SEC       = float(os.getenv("NG_DELAY_AFTER_MARKER_SEC",       "2.2"))
# NG検査キューの最大深さ
# 上限を超えた場合は最古エントリを破棄して最新エントリを優先する
NG_QUEUE_MAX = int(os.getenv("NG_QUEUE_MAX", "1"))

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
        self._sensor_lock        = threading.Lock()
        self._marker_detected    = False
        self._marker_detected_at = 0.0   # GPIO 発火時刻（time.monotonic()）
        self._last_print_complete_monotonic = None  # time.monotonic() at last print completion
        self._marker_ignore_until = 0.0  # time.monotonic() 未満はマーカー無視

        # NG検査（Ethernet/NIC）キュー方式
        # - マーカー検知ごとに (検知時刻) をキューに積む
        # - ワーカースレッドが順番に取り出し、残り delay を計算して LON を送信
        # - SR-X は同時接続 1 本のため、シングルワーカーで直列処理する
        self._ng_queue       = queue.Queue()
        self._stop_lock      = threading.Lock()
        self._ng_delay_first = True  # ワーク1枚目フラグ（Job ID 更新 / stop() でリセット）

        self._ng_worker_thread = threading.Thread(
            target=self._ng_worker, daemon=True
        )
        self._ng_worker_thread.start()

        self.marker_button = Button(INPUT_PIN,  pull_up=PULL_UP_RESISTOR)

        # ★修正②: when_pressed コールバックを登録
        # gpiozero の内部スレッドが GPIO 割り込みを監視し、
        # センサーが一瞬でも ON になった瞬間にコールバックが呼ばれる。
        # is_pressed のポーリングと違い、短パルスも確実に捕捉できる。
        self.marker_button.when_pressed = self._on_marker_detected

    def _ng_worker(self):
        """
        NG 検査ワーカースレッド（常駐）

        キューからマーカー検知時刻を取り出し、
        残り delay を計算してから LON を送信する。
        SR-X は同時接続 1 本のため直列処理で問題ない。
        """
        while True:
            detected_at = self._ng_queue.get()
            try:
                # 1枚目 / 2枚目以降でディレイを切り替える
                if self._ng_delay_first:
                    delay_sec = NG_DELAY_AFTER_MARKER_SEC_FIRST
                    print(
                        f"NG検査 遅延 {delay_sec}s [初回]"
                        f" → 次回から {NG_DELAY_AFTER_MARKER_SEC}s に切り替わります",
                        flush=True,
                    )
                    self._ng_delay_first = False
                else:
                    delay_sec = NG_DELAY_AFTER_MARKER_SEC
                    print(f"NG検査 遅延 {delay_sec}s [2回目以降]", flush=True)

                # 検知時刻からの経過を考慮した残り delay
                elapsed         = time.monotonic() - detected_at
                remaining_delay = max(0.0, delay_sec - elapsed)
                if remaining_delay > 0:
                    print(f"NG検査 遅延待機: {remaining_delay:.2f}s", flush=True)
                    time.sleep(remaining_delay)

                if self.initstate != 1:
                    print("NG検査 スキップ: ジョブ停止中", flush=True)
                    continue

                print(
                    f"NG検査 開始: {self.Commander.ng_sensor_host}:"
                    f"{self.Commander.ng_sensor_port}",
                    flush=True,
                )
                res = self.Commander.ng_sensor_poll()
                print(f"NG検査 応答: {res!r}", flush=True)

                if res is None:
                    print("NG検査: 応答なし/接続失敗 — ライン停止", flush=True)
                    self.stop()
                elif "ERROR" in res.upper():
                    print(f"NG検査 OUT (ERROR): {res!r} — ライン停止", flush=True)
                    self.stop()

            except Exception as e:
                print(f"NG検査 例外: {e}", flush=True)
            finally:
                self._ng_queue.task_done()

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
            self._marker_detected    = True
            self._marker_detected_at = now  # GPIO 発火時刻を正確に記録

    def _schedule_ng_inspection(self):
        """マーカー検知時刻をキューに積む（ワーカーが順番に処理する）"""
        if self.initstate != 1:
            print("NG検査 予約スキップ: initstate!=1", flush=True)
            return
        if not self.Commander.ng_sensor_enabled:
            print("NG検査 予約スキップ: NG_HOST 未設定", flush=True)
            return

        # GPIO 発火時刻を使う（run() の処理遅延を除外するため）
        with self._sensor_lock:
            detected_at = self._marker_detected_at

        schedule_lag = time.monotonic() - detected_at
        if schedule_lag > 0.05:
            print(f"NG検査 スケジュール遅延: {schedule_lag:.3f}s (listen_response 待ち)", flush=True)

        # キューが上限に達している場合は古いエントリを捨てて最新を優先する
        # 古い検品より直近のワークを確実に検査するための措置
        while self._ng_queue.qsize() >= NG_QUEUE_MAX:
            try:
                old_at = self._ng_queue.get_nowait()
                self._ng_queue.task_done()
                age = time.monotonic() - old_at
                print(
                    f"NG検査 キュー上限 ({NG_QUEUE_MAX}): 古いエントリを破棄"
                    f" (経過={age:.1f}s)",
                    flush=True,
                )
            except queue.Empty:
                break

        self._ng_queue.put(detected_at)
        print(
            f"NG検査 キュー追加: GPIO発火から{time.monotonic()-detected_at:.2f}s経過"
            f" (キュー残={self._ng_queue.qsize()})",
            flush=True,
        )

    def start(self):
        self.starter = 1
        with self._sensor_lock:
            self._marker_detected = False
            self._marker_ignore_until = (
                time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
            )
        print("DMX Printer started.")

    def stop(self):
        with self._stop_lock:
            if self.starter == 0 and self.initstate == 0:
                return
            self.stoping()
            self.count   = 0
            self.printed = 0
            self.flag    = 0
            self.lastid  = "none"
            with self._sensor_lock:
                self._last_print_complete_monotonic = None
                self._marker_ignore_until = 0.0
            # stop() でもリセット（Job ID 更新前に stop が呼ばれるケースのフォールバック）
            self.Commander.ng_reset_first_poll()
            self._ng_delay_first = True
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
            _t = time.monotonic()
            listen = self.Commander.listen_response()
            print(f"[listen_response] printer_status: {time.monotonic()-_t:.3f}s")
            print(f"Printer status: {printer_status.decode('utf-8', errors='ignore')}")
            print(listen)

            command = "JOB_ID"
            self.Commander.printer_send_command(command, job_id, 'none')
            print(f"Job ID changed to {job_id}")
            # Job ID 更新 = 新しいジョブ開始 → 1枚目フラグをリセット
            self.Commander.ng_reset_first_poll()
            self._ng_delay_first = True
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
            _t = time.monotonic()
            self.status_responce = self.Commander.listen_response()
            print(f"[listen_response] STATUS: {time.monotonic()-_t:.3f}s")
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
                        # GPIO 発火から印刷開始までの総遅延計測
                        with self._sensor_lock:
                            _gpio_at = self._marker_detected_at
                        _gpio_lag = time.monotonic() - _gpio_at
                        print(f"[timing] GPIO→マーカー処理: {_gpio_lag:.3f}s")

                        self._schedule_ng_inspection()

                        uuid = self.Commander.uuid_gen('GEN')
                        print(f"UUID:{uuid}")

                        job_id = str(self.spi.job.jobNum)
                        self.Commander.printer_send_command("BLOCK_WRITE", job_id, uuid)
                        _t = time.monotonic()
                        responce = self.Commander.listen_response()
                        print(f"[listen_response] BLOCK_WRITE: {time.monotonic()-_t:.3f}s")
                        print(f"responce:{responce}")
                        time.sleep(0.1)

                        print("印刷開始")
                        self.flag = 1
                        self.Commander.printer_send_command("PRINTING", job_id, uuid)
                        _t = time.monotonic()
                        responce_print = self.Commander.listen_response()
                        _print_rtt = time.monotonic() - _t
                        print(f"[listen_response] PRINTING: {_print_rtt:.3f}s")
                        print(f"[timing] GPIO→PRINTING完了: {time.monotonic()-_gpio_at:.3f}s")
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
                print_just_finished = self.flag == 1
                if self.flag == 1:
                    with self._sensor_lock:
                        self._last_print_complete_monotonic = time.monotonic()
                self.flag = 0

                self.spi.job.printed = self.printed

            time.sleep(0.05)

        except KeyboardInterrupt:
            print("DMX Printer interrupted by user.")
            exit()
        except Exception:
            pass

    def stoping(self):
        # ★先に starter / initstate を 0 にする
        # こうすることで、uuid_gen・printer_send・stop_conveyor の実行中に
        # main ループが run() を呼んで start_conveyor() を送り込むのを防ぐ
        self.starter   = 0
        self.initstate = 0
        self.Commander.uuid_gen('STOP')
        print(f"UUID generation stopped.")
        self.Commander.printer_send_command("STOP", self.spi.job.jobNum, 'none')
        self.Commander.stop_conveyor()
        print("Conveyor stopped.")


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
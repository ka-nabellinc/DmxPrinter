from gpiozero import Button
import datetime
from kv_dmx_printer.kv_dmx_printer import kvDmxPrinter
import time
import os
import configparser
import sys
import threading
import queue
import urllib.request

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
    os.getenv("MARKER_MIN_INTERVAL_AFTER_PRINT_SEC", "0.1")
)
# ジョブ開始直後は電源立ち上がり・コンベア起動ノイズで偽検知しやすいため、この秒数はマーカーを無視
MARKER_IGNORE_AFTER_JOB_START_SEC = float(
    os.getenv("MARKER_IGNORE_AFTER_JOB_START_SEC", "0.1")
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

# 供給スリーブが途切れたと判断する秒数
# この時間 GPIO が発火しなかった場合、次の再供給を1枚目扱い（NG初回タイムアウト）にする
SLEEVE_TIMEOUT_SEC = float(os.getenv("SLEEVE_TIMEOUT_SEC", "20.0"))

# GPIO 発火から WX,StartMarking 送信までの最小待機秒数
# コンベア速度とセンサ〜印字ヘッド間の物理距離によって決まる値。
# プリフェッチで BLOCK_WRITE が省略された場合でもこの時間は必ず確保する。
# 調整方法:
#   早すぎる（DataMatrix が前にズレる）→ 値を大きくする
#   遅すぎる（DataMatrix が後ろにズレる）→ 値を小さくする
PRINT_TRIGGER_DELAY_SEC = float(os.getenv("PRINT_TRIGGER_DELAY_SEC", "1.05"))


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

        self.oee_host = config_ini['OEE']['OEE_HOST']
        self.oee_port = int(config_ini.get('OEE', 'OEE_PORT'))

        BUZZER_HOST = os.getenv("BUZZER_HOST", "dmx-printer-buzzer")
        BUZZER_PORT = int(os.getenv("BUZZER_PORT", "1919"))
        self._buzzer_url = f"http://{BUZZER_HOST}:{BUZZER_PORT}"
        
        self.by_encode     = config_ini['DEBUG']['DEBUG_BY_ENCODE']
        self.delay         = float(config_ini.get('DEBUG', 'DEBUG_DELAY'))
        self.timeout       = float(config_ini.get('DEBUG', 'DEBUG_TIMEOUT'))

        self.Commander = Command()

        self.count     = 0
        self.printed   = 0
        self.lastid    = "none"
        self.starter   = 0
        self.initstate = 0

        # ─── Bug① 対策: ジョブID変化時のみカウントをリセット ────────────
        # 同一ジョブ中の一時停止（スリーブ補充など）→再開では
        # count / printed を保持し、印字済み枚数を引き継ぐ。
        self._last_init_job_id = None   # 最後に初期化したジョブID

        # ─── Bug① 対策: スリーブ供給タイムアウト検出 ─────────────────────
        # SLEEVE_TIMEOUT_SEC 秒以上 GPIO が発火しなかったら次の供給を「1枚目」扱いにする
        self._last_gpio_at     = 0.0    # 最後の GPIO 発火時刻 (monotonic)
        self._sleeve_timed_out = False  # タイムアウト通知済みフラグ

        # ─── Bug② 対策: ジョブ未設定時コンベアのみ起動 ──────────────────
        # spi.job.qty == 0 の状態で START された場合、FP-1000 を初期化せず
        # コンベアのみ動作させる（払い出し用）
        self._conveyor_only_started = False  # コンベアのみ起動済みフラグ

        self.spi = sleevePrinterInterface.sleevePrinterInterface(
            port      = 8989,
            startFunc = self.start,
            stopFunc  = self.stop,
        )

        self._sensor_lock        = threading.Lock()
        self._marker_detected    = False
        self._marker_detected_at = 0.0   # GPIO 発火時刻（time.monotonic()）
        self._last_print_complete_monotonic = None
        self._marker_ignore_until = 0.0

        # NG検査キュー方式（変更なし）
        self._ng_queue       = queue.Queue()
        self._stop_lock      = threading.Lock()
        self._ng_delay_first = True

        self._ng_worker_thread = threading.Thread(
            target=self._ng_worker, daemon=True
        )
        self._ng_worker_thread.start()

        # レスポンスキューを RX（STATUS）/ WX（印刷）で分離
        # これにより _print_worker が2秒ブロックしていても
        # run() の STATUS ポーリングが影響を受けない
        self._status_queue = queue.Queue()   # RX,... 応答専用
        self._wx_queue     = queue.Queue()   # WX,... 応答専用（BLOCK_WRITE / PRINTING）

        self._listener_thread = threading.Thread(
            target=self._persistent_listener, daemon=True
        )
        self._listener_thread.start()

        # 印刷ジョブキュー
        # run() がジョブを投入し、_print_worker が BLOCK_WRITE → PRINTING を実行する
        # _print_flag: 0=アイドル, 1=印刷実行中
        # ジョブタプル形式: (job_id, uuid, gpio_at, skip_bw)
        #   skip_bw=True のとき BLOCK_WRITE を省略（プリフェッチ済み）
        self._print_job_queue   = queue.Queue(maxsize=1)
        self._print_flag        = 0
        self._print_result_lock = threading.Lock()

        # プリフェッチ状態
        # _print_worker が PRINTING 完了直後に次ワーク分の UUID + BLOCK_WRITE を先行実行し、
        # _prefetch_done=True になっていれば run() が PRINTING のみを投入する。
        # これにより GPIO → PRINTING の所要時間を ~71ms → ~5ms に短縮できる。
        self._prefetch_lock    = threading.Lock()
        self._prefetch_done    = False   # BLOCK_WRITE 先行完了フラグ
        self._prefetch_uuid    = None    # 先行取得した UUID
        self._prefetch_job_id  = None    # プリフェッチ時のジョブID（ジョブ切替検知用）

        self._print_worker_thread = threading.Thread(
            target=self._print_worker, daemon=True
        )
        self._print_worker_thread.start()

        self.marker_button = Button(INPUT_PIN, pull_up=PULL_UP_RESISTOR)
        self.marker_button.when_pressed = self._on_marker_detected

        self.dmx_printer = kvDmxPrinter(
            host=self.oee_host,
            port=self.oee_port
        )
        self.buzzer_on_flag = 0
    # ------------------------------------------------------------------
    # リスナー：レスポンスをプレフィックスで振り分け
    # ------------------------------------------------------------------
    def _persistent_listener(self):
        """
        ポート5001を常時待ち受ける唯一のリスナースレッド。
        RX で始まる応答（STATUS）→ _status_queue
        それ以外（BLOCK_WRITE / PRINTING の WX 応答）→ _wx_queue
        """
        import socket as _socket
        host = self.Commander.main_host
        port = self.Commander.main_port
        print(f"[listener] ポート{port}で常時待機開始", flush=True)
        while True:
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as srv:
                    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                    srv.bind((host, port))
                    srv.listen(5)
                    while True:
                        srv.settimeout(1.0)
                        try:
                            conn, addr = srv.accept()
                        except _socket.timeout:
                            continue
                        with conn:
                            data = conn.recv(1024)
                            if data:
                                msg = data.decode('utf-8', errors='ignore')
                                if msg.lstrip().upper().startswith('RX'):
                                    self._status_queue.put(msg)
                                else:
                                    self._wx_queue.put(msg)
            except Exception as e:
                print(f"[listener] エラー: {e} — 再起動します", flush=True)
                time.sleep(0.1)


    # ------------------------------------------------------------------
    # ブザー：ブザーをON/OFFする
    # ------------------------------------------------------------------

    def buzzer_on(self):
        try:
            req = urllib.request.Request(
                f"{self._buzzer_url}/buzzer/on", method="POST", data=b""
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            print(f"[buzzer] ON 失敗: {e}", flush=True)

    def buzzer_off(self):
        try:
            req = urllib.request.Request(
                f"{self._buzzer_url}/buzzer/off", method="POST", data=b""
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            print(f"[buzzer] OFF 失敗: {e}", flush=True)





    # ------------------------------------------------------------------
    # 印刷ワーカー：BLOCK_WRITE → PRINTING を別スレッドで実行
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # プリフェッチ：PRINTING 完了後に次ワークの UUID + BLOCK_WRITE を先行実行
    # ------------------------------------------------------------------
    def _do_prefetch(self, current_job_id: str):
        """
        FP-1000 が印刷完了した直後（WX,OK 受信後）に呼び出す。
        次ワーク分の UUID 取得と BLOCK_WRITE を先行送信しておくことで、
        次の GPIO → PRINTING の所要時間を ~71ms → ~5ms に短縮する。

        成功時: _prefetch_done=True / _prefetch_uuid=<UUID> がセットされる。
        失敗時: _prefetch_done=False のまま → run() が通常フローにフォールバック。
        """
        # ジョブが停止中 / 終了間際ならプリフェッチ不要
        if self.initstate != 1:
            return
        remaining = self.spi.job.qty - self.printed
        if remaining <= 1:
            # 残り 1 枚以下なら次ワークは存在しない
            return

        try:
            next_uuid = self.Commander.uuid_gen('GEN')
            if not next_uuid or not next_uuid.strip():
                print("[prefetch] UUID取得失敗 — スキップ", flush=True)
                return
            next_uuid = next_uuid.strip()

            # PRINTING 完了直後はプリンターがポートを再開放するまで短く待機する。
            # 待機なしで即座に接続すると [Errno 111] Connection refused が発生する。
            time.sleep(0.15)
            if self.initstate != 1:
                return

            sent = self.Commander.printer_send_command("BLOCK_WRITE", current_job_id, next_uuid)
            if not sent:
                # 接続失敗（Connection refused 等）→ キュー待ちせずに即フォールバック
                print("[prefetch] BLOCK_WRITE 送信失敗 — フォールバック", flush=True)
                with self._prefetch_lock:
                    self._prefetch_done = False
                return

            _t = time.monotonic()
            resp_bw = self._wx_queue.get(timeout=self.Commander.timeout)
            print(
                f"[prefetch] BLOCK_WRITE: {time.monotonic()-_t:.3f}s"
                f"  内容={resp_bw.strip()!r}",
                flush=True,
            )

            with self._prefetch_lock:
                self._prefetch_uuid   = next_uuid
                self._prefetch_job_id = current_job_id
                self._prefetch_done   = True
            print(f"[prefetch] 完了 → 次ワーク PRINTING のみで印刷可能", flush=True)

        except queue.Empty:
            print("[prefetch] BLOCK_WRITE 応答タイムアウト — フォールバック", flush=True)
            with self._prefetch_lock:
                self._prefetch_done = False
        except Exception as e:
            print(f"[prefetch] エラー: {e} — フォールバック", flush=True)
            with self._prefetch_lock:
                self._prefetch_done = False

    def _print_worker(self):
        """
        印刷ジョブワーカースレッド（常駐）

        run() から (job_id, uuid, gpio_at, skip_bw) を受け取り
        BLOCK_WRITE（skip_bw=False の場合）→ PRINTING を順番に実行する。
        skip_bw=True のときは BLOCK_WRITE がプリフェッチ済みのため PRINTING のみ送信。
        PRINTING の2秒ブロックはこのスレッド内で完結するため
        run() の STATUS ポーリングは影響を受けない。
        """
        while True:
            try:
                item = self._print_job_queue.get()
                job_id, uuid, gpio_at = item[0], item[1], item[2]
                skip_bw = item[3] if len(item) > 3 else False

                # BLOCK_WRITE（プリフェッチ済みの場合はスキップ）
                if skip_bw:
                    print("[print_worker] BLOCK_WRITE スキップ（プリフェッチ済み）", flush=True)
                else:
                    self.Commander.printer_send_command("BLOCK_WRITE", job_id, uuid)
                    _t = time.monotonic()
                    resp_bw = self._wx_queue.get(timeout=self.Commander.timeout)
                    print(
                        f"[listen_response] BLOCK_WRITE: {time.monotonic()-_t:.3f}s"
                        f"  内容={resp_bw.strip()!r}",
                        flush=True,
                    )
                    print(f"responce:{resp_bw}", flush=True)

                if self.initstate != 1:
                    print("[print_worker] スキップ: ジョブ停止中", flush=True)
                    continue

                # ─── 印字タイミング調整 ────────────────────────────────────────
                # GPIO 発火から WX,StartMarking 送信までの時間を PRINT_TRIGGER_DELAY_SEC に合わせる。
                # プリフェッチで BLOCK_WRITE が省略された場合、経過時間が非常に短くなるため
                # 残り時間をここで確実に補完する。
                # dmxprinter のポート再開放待ちも兼ねるため、最低でも 0.01s は空ける。
                elapsed_since_gpio = time.monotonic() - gpio_at
                trigger_wait = max(0.01, PRINT_TRIGGER_DELAY_SEC - elapsed_since_gpio)
                print(
                    f"[print_worker] 印字タイミング待機: {trigger_wait:.3f}s"
                    f"  (GPIO経過={elapsed_since_gpio:.3f}s / 目標={PRINT_TRIGGER_DELAY_SEC}s)",
                    flush=True,
                )
                time.sleep(trigger_wait)
                # ──────────────────────────────────────────────────────────────

                # PRINTING（Connection refused 高速リトライ）
                # printer_send_command の戻り値（True/False）で接続失敗を即座に検知する。
                # 失敗時は 50ms 後にリトライ → 3秒タイムアウト待ちを廃止。
                PRINT_MAX_RETRY    = 8      # 最大試行回数
                PRINT_RETRY_WAIT   = 0.05   # 接続失敗後の待機秒数（50ms）
                PRINT_RESP_TIMEOUT = 4.0    # 送信成功後の応答待ちタイムアウト
                resp_print = None

                for attempt in range(1, PRINT_MAX_RETRY + 1):
                    print(f"印刷開始 (試行{attempt}/{PRINT_MAX_RETRY})", flush=True)
                    sent = self.Commander.printer_send_command("PRINTING", job_id, uuid)

                    if not sent:
                        # 接続失敗を即座に検知（< 1ms）→ 50ms後にリトライ
                        print(
                            f"[print_worker] PRINTING 接続失敗 (試行{attempt}/{PRINT_MAX_RETRY})"
                            f" → {int(PRINT_RETRY_WAIT*1000)}ms後リトライ",
                            flush=True,
                        )
                        if attempt < PRINT_MAX_RETRY:
                            time.sleep(PRINT_RETRY_WAIT)
                        else:
                            print("[print_worker] PRINTING 全試行失敗 — このワークをスキップ", flush=True)
                        continue

                    # 送信成功 → 応答を待つ
                    try:
                        _t = time.monotonic()
                        resp_print = self._wx_queue.get(timeout=PRINT_RESP_TIMEOUT)
                        elapsed = time.monotonic() - _t
                        print(
                            f"[listen_response] PRINTING: {elapsed:.3f}s"
                            f"  内容={resp_print.strip()!r}",
                            flush=True,
                        )
                        print(f"responce:{resp_print}", flush=True)
                        break   # 成功 → ループ終了
                    except queue.Empty:
                        # 送信は成功したが応答が来ない（通信異常）→ リトライ
                        print(
                            f"[print_worker] PRINTING 応答タイムアウト (試行{attempt}/{PRINT_MAX_RETRY})",
                            flush=True,
                        )
                        if attempt >= PRINT_MAX_RETRY:
                            print("[print_worker] PRINTING 全試行失敗 — このワークをスキップ", flush=True)

                print(
                    f"[timing] GPIO→PRINTING完了: {time.monotonic()-gpio_at:.3f}s",
                    flush=True,
                )

                if resp_print and "WX,OK" in resp_print:
                    with self._print_result_lock:
                        self.printed += 1
                        self.spi.job.printed = self.printed

                # ─── プリフェッチ ───────────────────────────────────────────
                # PRINTING 完了（WX,OK 受信）直後、FP-1000 は再び idle に戻る。
                # この空き時間に次ワーク分の UUID + BLOCK_WRITE を先行送信する。
                # → 次の GPIO が来たとき PRINTING のみ投げれば済み、
                #   遅延を ~71ms → ~5ms に圧縮できる。
                self._do_prefetch(job_id)
                # ──────────────────────────────────────────────────────────

            except queue.Empty:
                print("[print_worker] レスポンスタイムアウト", flush=True)
            except Exception as e:
                print(f"[print_worker] 例外: {e}", flush=True)
            finally:
                self._print_flag = 0
                self._print_job_queue.task_done()

    # ------------------------------------------------------------------
    # キューフラッシュ（JOB_ID / START / STOP 送信後に呼ぶ）
    # ------------------------------------------------------------------
    def _flush_queues(self, reason: str = ""):
        """両レスポンスキューに残った未読メッセージを破棄する。"""
        discarded = []
        for q in (self._status_queue, self._wx_queue):
            while True:
                try:
                    discarded.append(q.get_nowait().strip())
                except queue.Empty:
                    break
        if discarded:
            print(f"[flush_queue] {reason}: {len(discarded)} 件破棄 → {discarded}", flush=True)

    # ------------------------------------------------------------------
    # NG 検査ワーカー（変更なし）
    # ------------------------------------------------------------------
    def _ng_worker(self):
        """
        NG 検査ワーカースレッド（常駐）

        キューからマーカー検知時刻を取り出し、
        残り delay を計算してから LON を送信する。
        """
        while True:
            detected_at = self._ng_queue.get()
            try:
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
                    threading.Thread(target=self.buzzer_on, daemon=True).start()
                    self.buzzer_on_flag = 1
                    self.stop()
                elif "ERROR" in res.upper():
                    print(f"NG検査 OUT (ERROR): {res!r} — ライン停止", flush=True)
                    threading.Thread(target=self.buzzer_on, daemon=True).start()
                    self.buzzer_on_flag = 1
                    self.stop()

            except Exception as e:
                print(f"NG検査 例外: {e}", flush=True)
            finally:
                self._ng_queue.task_done()

    def _on_marker_detected(self):
        """スリーブ検知センサーが反応したらフラグを立てる"""
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
            # スリーブ供給間隔ログ
            if self._last_gpio_at > 0:
                print(f"[sleeve_interval] スリーブ間隔: {now - self._last_gpio_at:.3f}s", flush=True)
            self._marker_detected    = True
            self._marker_detected_at = now
            # 最終 GPIO 発火時刻を更新（供給タイムアウト検出用）
            self._last_gpio_at     = now
            self._sleeve_timed_out = False  # 再供給 → タイムアウト解除

    def _schedule_ng_inspection(self):
        """マーカー検知時刻をキューに積む"""
        if self.initstate != 1:
            print("NG検査 予約スキップ: initstate!=1", flush=True)
            return
        if not self.Commander.ng_sensor_enabled:
            print("NG検査 予約スキップ: NG_HOST 未設定", flush=True)
            return

        with self._sensor_lock:
            detected_at = self._marker_detected_at

        schedule_lag = time.monotonic() - detected_at
        if schedule_lag > 0.05:
            print(f"NG検査 スケジュール遅延: {schedule_lag:.3f}s", flush=True)

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
        self.dmx_printer.recordSetupEndTime(datetime.datetime.now())
        self.starter = 1
        self._conveyor_only_started = False   # Bug②: 再 START 時はモード判定をリセット
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

            # ─── フロントエンド STOP vs NGセンサー一時停止の判別 ─────────────
            # stopSW() が呼ばれると job.reset() により qty=0 になる（フロントエンドSTOP）。
            # NGセンサー等からの一時停止では qty は元の値のまま。
            # これを使って「完全停止」か「再開前提の一時停止」かを判別する。
            qty_snapshot     = self.spi.job.qty   # job.reset()後なら0
            printed_snapshot = self.printed        # ログ用スナップショット
            is_full_stop     = (qty_snapshot == 0)

            self.stoping()

            # Bug① 修正:
            #   NGセンサー等による一時停止（qty > 0）→ printed を引き継いで再開。
            #   フロントエンドSTOP（qty=0 に reset済）→ printed もリセットして
            #   次の START で誤って「再開」扱いにならないようにする。
            self._print_flag  = 0
            self.lastid       = "none"
            with self._sensor_lock:
                self._last_print_complete_monotonic = None
                self._marker_ignore_until = 0.0
                self._sleeve_timed_out = False     # タイムアウト状態もクリア
            # プリフェッチ状態をクリア（中断時に古いUUIDを引き継がないよう）
            with self._prefetch_lock:
                self._prefetch_done   = False
                self._prefetch_uuid   = None
                self._prefetch_job_id = None
            self._conveyor_only_started = False    # Bug②: モードフラグをリセット
            self.Commander.ng_reset_first_poll()
            self._ng_delay_first = True

            if is_full_stop:
                # フロントエンドSTOP: printed カウンタをリセットし、
                # 次の START で同一ジョブIDでも必ずゼロから開始できるようにする
                self.printed = 0
                self._last_init_job_id = None
                print(
                    f"[stop] 停止完了（完全停止）"
                    f" 印字済={printed_snapshot} / qty がリセット済のため残数不明",
                    flush=True,
                )
            else:
                # NGセンサー等による一時停止: printed を保持（再開時に引き継ぐ）
                remaining = qty_snapshot - self.printed
                print(
                    f"[stop] 停止完了（一時停止）"
                    f" 印字済={self.printed}, 残={remaining}",
                    flush=True,
                )

    def getPrinted(self):
        printed = int(input("Printed: "))
        self.spi.job.printed = printed
        if printed >= self.spi.job.qty:
            self.spi.job.finished = True

    # ------------------------------------------------------------------
    # メインループ：STATUS ポーリング + マーカー検知のみ
    # BLOCK_WRITE / PRINTING は _print_worker スレッドに委譲
    # ------------------------------------------------------------------
    def run(self):
        job_id = str(self.spi.job.jobNum)
        print(f"Job ID: {job_id}")

        if self.initstate == 0:
            # ─────────────────────────────────────────────────────────────
            # Bug② 対策: ジョブ未設定（qty==0）はコンベアのみ起動し印字しない
            # ─────────────────────────────────────────────────────────────
            job_configured = (self.spi.job.qty > 0)

            if not job_configured:
                if not self._conveyor_only_started:
                    self.Commander.start_conveyor()
                    self._conveyor_only_started = True
                    print(
                        "[run] ジョブ未設定 (qty=0) — コンベアのみ起動します。"
                        "印字は行いません。",
                        flush=True,
                    )
                # initstate は 0 のまま維持（ジョブ設定後に再 START されたとき
                # 改めて FP-1000 初期化が走るようにする）
                time.sleep(0.1)
                return

            # ─────────────────────────────────────────────────────────────
            # Bug① 対策: ジョブID変化時のみカウントをリセット
            # 同一ジョブ中の一時停止（スリーブ補充・NG停止）→ 再開では
            # printed / count を保持して印字済み枚数を引き継ぐ。
            # ─────────────────────────────────────────────────────────────
            if self._last_init_job_id != job_id:
                # 新ジョブ（またはシステム起動直後）→ カウントをリセット
                prev = self.printed
                self.count   = 0
                self.printed = 0
                self.spi.job.printed = 0
                self._last_init_job_id = job_id
                print(
                    f"[run] 新ジョブ開始 (job_id={job_id}) → カウントリセット"
                    f" (前回印字済={prev})",
                    flush=True,
                )
            else:
                # 同一ジョブ再開 → カウントを引き継ぎ
                print(
                    f"[run] 同一ジョブ再開 (job_id={job_id})"
                    f" → カウント引き継ぎ (印字済={self.printed}/{self.spi.job.qty})",
                    flush=True,
                )

            # FP-1000 初期化
            printer_status = self.Commander.printer_status()
            try:
                listen = self._status_queue.get(timeout=self.Commander.timeout)
            except queue.Empty:
                listen = "none"
            print(f"Printer status: {printer_status.decode('utf-8', errors='ignore')}")
            print(listen)

            self.Commander.printer_send_command("JOB_ID", job_id, 'none')
            print(f"Job ID changed to {job_id}")
            self.Commander.ng_reset_first_poll()
            self._ng_delay_first = True
            time.sleep(0.1)
            self._flush_queues(reason="JOB_ID応答")

            self.Commander.printer_send_command("START", job_id, 'none')
            time.sleep(0.1)
            self._flush_queues(reason="START応答")

            self.Commander.start_conveyor()
            print(f"DMX Printer job started. Conveyor started.:{listen}")
            self.initstate = 1
            with self._sensor_lock:
                self._marker_detected = False
                until = time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
                if until > self._marker_ignore_until:
                    self._marker_ignore_until = until
            time.sleep(0.1)

        # ─────────────────────────────────────────────────────────────────
        # Bug① 対策: スリーブ供給タイムアウト検出
        # SLEEVE_TIMEOUT_SEC 秒以上 GPIO が発火しなかった場合、
        # 次の再供給を「1枚目」扱い（NG初回タイムアウト）に切り替える。
        # count / printed は変更しない。
        # ─────────────────────────────────────────────────────────────────
        if (
            self.initstate == 1
            and self._last_gpio_at > 0
            and not self._sleeve_timed_out
            and (time.monotonic() - self._last_gpio_at) > SLEEVE_TIMEOUT_SEC
        ):
            print(
                f"[sleeve_timeout] {SLEEVE_TIMEOUT_SEC:.0f}s 供給なし → "
                f"NG初回タイムアウトをリセット (印字済={self.printed}枚, 再供給まで待機)",
                flush=True,
            )
            self._ng_delay_first = True
            self.Commander.ng_reset_first_poll()
            self._sleeve_timed_out = True   # 次のループで重複通知しない

        # _print_worker が BLOCK_WRITE / PRINTING を実行中は
        # dmxprinter がビジーになるため STATUS 送信をスキップする。
        # GPIO 検知は _print_flag チェック後に行うため遅延改善は維持される。
        # sleep を 50ms → 10ms に短縮して次 GPIO の検出反応を速める。
        if self._print_flag == 1:
            # 印刷中でもマーカーフラグは読み捨てない（次ループで処理）
            time.sleep(0.01)
            return

        try:
            print("-----------STATUS received------------")
            self.Commander.printer_send_command("STATUS", job_id, "none")
            _t = time.monotonic()
            try:
                raw = self._status_queue.get(timeout=self.Commander.timeout)
            except queue.Empty:
                print(f"[listen_response] STATUS: タイムアウト", flush=True)
                return
            print(
                f"[listen_response] STATUS: {time.monotonic()-_t:.3f}s"
                f"  内容={raw.strip()!r}",
                flush=True,
            )
            parts = raw.split(',')
            status = parts[2] if len(parts) > 2 else 'none'
            print(f"Bresponce:{status}")
            print(f"print_flag:{self._print_flag}")

            if status.strip('\r') == '0':
                # プリンター待機中 かつ 印刷ジョブが空いているときのみ受け付ける
                if self._print_flag == 0:
                    # フラグを読むだけ。クリアはジョブ投入成功後に行う
                    with self._sensor_lock:
                        marker_hit = self._marker_detected
                        gpio_at = self._marker_detected_at  # クリア前に取得

                    if marker_hit:
                        gpio_lag = time.monotonic() - gpio_at
                        print(f"[timing] GPIO→マーカー処理: {gpio_lag:.3f}s")

                        self._schedule_ng_inspection()

                        # ─── プリフェッチ確認 ─────────────────────────────────
                        # _print_worker が前ワーク完了後に UUID + BLOCK_WRITE を
                        # 先行送信済みであれば、このワークは PRINTING のみで済む。
                        job_id = str(self.spi.job.jobNum)
                        with self._prefetch_lock:
                            if (
                                self._prefetch_done
                                and self._prefetch_job_id == job_id
                                and self._prefetch_uuid
                            ):
                                uuid    = self._prefetch_uuid
                                skip_bw = True
                                self._prefetch_done  = False
                                self._prefetch_uuid  = None
                                self._prefetch_job_id = None
                                print(
                                    f"[run] プリフェッチHIT — PRINTING のみ送信"
                                    f" (GPIO遅延={gpio_lag:.3f}s)",
                                    flush=True,
                                )
                            else:
                                uuid    = None
                                skip_bw = False

                        # プリフェッチなし → 通常の UUID 取得
                        if not skip_bw:
                            try:
                                uuid = self.Commander.uuid_gen('GEN')
                                print(f"UUID:{uuid}")
                            except Exception as e:
                                print(f"[run] uuid_gen失敗 — マーカーを保持して次ループへ: {e}", flush=True)
                                return  # _marker_detected はまだ True のまま → 次ループで再試行
                        # ─────────────────────────────────────────────────────

                        self._print_flag = 1
                        # 印刷開始時点でタイマーを記録する
                        # （印刷完了時に記録すると次ワークのGPIOがクールダウン窓に入り無視される）
                        with self._sensor_lock:
                            self._last_print_complete_monotonic = time.monotonic()
                        try:
                            # ノンブロッキング投入（満杯なら捨てて警告）
                            # タプル第4要素 skip_bw: True=BLOCK_WRITE省略、False=通常
                            self._print_job_queue.put_nowait((job_id, uuid, gpio_at, skip_bw))
                            # ジョブ投入成功後に初めてフラグをクリア
                            with self._sensor_lock:
                                self._marker_detected = False
                        except queue.Full:
                            print("[run] 印刷ジョブキュー満杯 — このワークをスキップ", flush=True)
                            self._print_flag = 0
                            # マーカーはクリアしない（次ループで再試行）

            elif status.strip('\r') == '1':
                # 印刷中（_print_worker が動作中のため通常ここには来ない）
                pass

            elif status.strip('\r') == '2':
                # 印刷完了（_print_worker が動作中のため通常ここには来ない）
                with self._print_result_lock:
                    self.spi.job.printed = self.printed

            time.sleep(0.05)

        except KeyboardInterrupt:
            print("DMX Printer interrupted by user.")
            exit()
        except Exception as e:
            print(f"[run] 予期しない例外: {e}", flush=True)

    def stoping(self):
        self.job_id = "none"
        self.starter   = 0
        self.initstate = 0
        self.dmx_printer.recordOperationEndTime(datetime.now())  
        self.Commander.uuid_gen('STOP')
        print(f"UUID generation stopped.")
        self.Commander.printer_send_command("STOP", self.spi.job.jobNum, 'none')
        time.sleep(0.1)
        self._flush_queues(reason="STOP応答")
        self.Commander.stop_conveyor()
        print("Conveyor stopped.")
        if self.buzzer_on_flag == 0:
            threading.Thread(target=self.buzzer_off, daemon=True).start()
        else:
            self.buzzer_on_flag = 0




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
                threading.Thread(target=main_instance.buzzer_on, daemon=True).start()
                # ジョブ自然完了: printed カウンタをリセットして
                # 次の START で同一ジョブIDでも正しくゼロから開始できるようにする
                main_instance.printed = 0
                main_instance._last_init_job_id = None
                main_instance.spi.job.finished = True

        except KeyboardInterrupt:
            print(f"EXIT")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            continue

    print("DMX Printer successfully.")

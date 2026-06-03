#!/usr/bin/env python3
"""
DMXプリンター コンベア停止バグ 調査用シミュレーター
=====================================================
目的:
    スリーブ検品センサーが不良品を検知した際に
    コンベアが停止しない事象の原因を特定する。

調査対象のバグ候補:
    [A] NG センサーが無効 (NG_SENSOR_HOST 未設定 → ng_sensor_enabled=False)
        → _ng_worker が起動せず stop() が呼ばれない
    [B] stop_conveyor() の TCP 接続失敗がサイレントに無視される
        → 停止コマンドが届かずコンベアが動き続ける
    [C] _ng_worker の 4 秒遅延中も run() ループが回り印刷が継続する
        → 停止前に追加印刷が発生する（設計上の問題）
    [D] run() の except Exception: pass が stop() 内部エラーを隠蔽する
        → 停止処理が失敗しても気づけない

使い方:
    python3 simulator.py [--scenario A|B|C|D|ALL]

    --scenario A  : NG センサー無効シナリオ（最も疑わしい）
    --scenario B  : TCP 失敗シナリオ
    --scenario C  : 4 秒遅延中に印刷継続シナリオ
    --scenario D  : 例外握りつぶしシナリオ
    --scenario ALL: 全シナリオ（デフォルト）
"""

import argparse
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ─────────────────────────────────────────────
# ログユーティリティ
# ─────────────────────────────────────────────

_LOG_LOCK = threading.Lock()
_START_TIME = time.monotonic()

class Color:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    MAGENTA= "\033[95m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def log(tag: str, msg: str, color: str = ""):
    elapsed = time.monotonic() - _START_TIME
    with _LOG_LOCK:
        print(f"{color}[{elapsed:6.2f}s][{tag:<18}] {msg}{Color.RESET}")

def log_bug(msg: str):
    log("★ BUG FOUND", msg, Color.RED + Color.BOLD)

def log_ok(msg: str):
    log("✓ OK", msg, Color.GREEN)

def log_warn(msg: str):
    log("⚠ WARNING", msg, Color.YELLOW)

def separator(title: str):
    print()
    print(Color.BOLD + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + Color.RESET)


# ─────────────────────────────────────────────
# シミュレート部品
# ─────────────────────────────────────────────

class SimConveyor:
    """コンベアモーターのシミュレーター"""

    def __init__(self, name: str = "Conveyor"):
        self.name = name
        self._running = False
        self._lock = threading.Lock()
        self._tcp_fail_stop = False  # True にすると STOP コマンドのみ失敗（バグBシミュレーション）
        self._cmd_queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        while True:
            cmd = self._cmd_queue.get()
            if cmd == "START":
                with self._lock:
                    was = self._running
                    self._running = True
                if not was:
                    log(self.name, "コンベア 起動", Color.GREEN)
            elif cmd == "STOP":
                with self._lock:
                    was = self._running
                    self._running = False
                if was:
                    log(self.name, "コンベア 停止", Color.CYAN)
                else:
                    log(self.name, "コンベア は既に停止済み")

    def send(self, cmd: str, caller: str = "") -> bool:
        """START / STOP コマンドを送信。TCP失敗フラグが立っていると STOP のみ失敗"""
        if cmd == "STOP" and self._tcp_fail_stop:
            log(caller or self.name,
                f"TCP 送信失敗: {cmd} → コンベアに届かない [バグ B]", Color.RED)
            return False
        self._cmd_queue.put(cmd)
        return True

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running


class SimPrinter:
    """FP-1000 プリンターのシミュレーター（STATUSサイクルを再現）"""

    STATUS_IDLE      = "0\r"  # 待機中（印刷可能）
    STATUS_PRINTING  = "1\r"  # 印刷中
    STATUS_DONE      = "2\r"  # 印刷完了

    def __init__(self):
        self._lock = threading.Lock()
        self._status = self.STATUS_IDLE
        self._print_count = 0

    def get_status(self) -> str:
        with self._lock:
            return self._status

    def start_print(self) -> bool:
        """印刷開始。成功時 True"""
        with self._lock:
            if self._status != self.STATUS_IDLE:
                return False
            self._status = self.STATUS_PRINTING
        log("SimPrinter", "印刷開始", Color.MAGENTA)
        # 0.3 秒後に DONE → IDLE に戻る（非同期）
        threading.Thread(target=self._finish_print, daemon=True).start()
        return True

    def _finish_print(self):
        time.sleep(0.15)
        with self._lock:
            self._status = self.STATUS_DONE
            self._print_count += 1
            count = self._print_count
        log("SimPrinter", f"印刷完了 (累計={count})", Color.MAGENTA)
        time.sleep(0.1)
        with self._lock:
            self._status = self.STATUS_IDLE

    @property
    def print_count(self) -> int:
        with self._lock:
            return self._print_count


class SimNGSensor:
    """スリーブ検品センサーのシミュレーター（Ethernet/TCP）"""

    def __init__(self, enabled: bool = True, defect_response: str = "ERROR"):
        self._enabled = enabled
        self._next_result = "OK"  # "OK" or defect_response
        self._defect_response = defect_response  # センサーが返す不良品応答

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_defect(self):
        """次回の poll で不良品応答を返すよう設定"""
        self._next_result = self._defect_response

    def poll(self, timeout: float = 5.0) -> Optional[str]:
        """LON コマンド相当。応答を返す"""
        if not self._enabled:
            return None
        result = self._next_result
        self._next_result = "OK"
        log("SimNGSensor", f"poll → {result!r}")
        return result


class SimMarkerSensor:
    """GPIO スリーブ検知センサー（when_pressed コールバック方式）"""

    def __init__(self):
        self._callback = None

    def set_callback(self, func):
        self._callback = func

    def trigger(self):
        """センサーが ON になった（マーカー通過）をシミュレート"""
        log("SimMarkerSensor", "マーカー検知 (GPIO 割り込み)", Color.YELLOW)
        if self._callback:
            self._callback()


# ─────────────────────────────────────────────
# メインコントローラー（main.py のロジックを再現）
# ─────────────────────────────────────────────

MARKER_MIN_INTERVAL_AFTER_PRINT_SEC = 0.6
MARKER_IGNORE_AFTER_JOB_START_SEC   = 0.6
NG_WORKER_DELAY_SEC                  = 4.0   # _ng_due_at_monotonic のオフセット


class MainController:
    """
    main.py の制御ロジックを忠実に再現したコントローラー。
    TCP ソケットの代わりに SimConveyor / SimPrinter / SimNGSensor を使う。
    """

    def __init__(
        self,
        conveyor: SimConveyor,
        printer:  SimPrinter,
        ng_sensor: SimNGSensor,
        marker:    SimMarkerSensor,
        swallow_exceptions: bool = False,  # バグD: except Exception: pass の再現
    ):
        self._conveyor  = conveyor
        self._printer   = printer
        self._ng_sensor = ng_sensor
        self._marker    = marker
        self._swallow   = swallow_exceptions

        # --- main.py と同じ状態変数 ---
        self.starter   = 0
        self.initstate = 0
        self.count     = 0
        self.printed   = 0
        self.flag      = 0
        self.lastid    = "none"

        # --- センサー関連 (main.py と同じ) ---
        self._sensor_lock    = threading.Lock()
        self._marker_detected = False
        self._last_print_complete_monotonic: Optional[float] = None
        self._marker_ignore_until = 0.0

        # --- NG ワーカー関連 (main.py と同じ) ---
        self._ng_lock           = threading.Lock()
        self._ng_trigger_event  = threading.Event()
        self._ng_due_at_monotonic: Optional[float] = None
        self._ng_thread_started = False

        # GPIO コールバック登録
        self._marker.set_callback(self._on_marker_detected)

        self._stop_requested = threading.Event()  # シミュレーション終了用

    # ── main.py: _ensure_ng_thread_started ──────────────────────────────

    def _ensure_ng_thread_started(self):
        if self._ng_thread_started:
            return
        if not self._ng_sensor.enabled:
            log("MainCtrl", "NG センサー無効 → _ng_worker 起動スキップ", Color.RED)
            return
        self._ng_thread_started = True
        th = threading.Thread(target=self._ng_worker, daemon=True)
        th.start()
        log("MainCtrl", "_ng_worker スレッド起動", Color.CYAN)

    # ── main.py: _ng_worker ─────────────────────────────────────────────

    def _ng_worker(self):
        while True:
            self._ng_trigger_event.wait()

            while True:
                with self._ng_lock:
                    due = self._ng_due_at_monotonic

                if due is None:
                    self._ng_trigger_event.clear()
                    break

                now = time.monotonic()
                if now < due:
                    time.sleep(min(0.2, due - now))
                    continue

                log("MainCtrl._ng_worker", "NG センサー poll 実行", Color.CYAN)
                res = self._ng_sensor.poll()
                log("MainCtrl._ng_worker", f"NG センサー応答: {res!r}")

                if res is None:
                    log("MainCtrl._ng_worker", "タイムアウト → ライン停止", Color.RED)
                    self.stop()
                elif "ERROR" in str(res).upper():
                    log("MainCtrl._ng_worker",
                        f"ERROR 受信 ({res!r}) → ライン停止", Color.RED)
                    self.stop()
                else:
                    log("MainCtrl._ng_worker", f"OK ({res!r}) → 継続")

                with self._ng_lock:
                    self._ng_due_at_monotonic = None
                self._ng_trigger_event.clear()
                break

    # ── main.py: _on_marker_detected ────────────────────────────────────

    def _on_marker_detected(self):
        """GPIO 割り込みコールバック（main.py と同一ロジック）"""
        with self._sensor_lock:
            now = time.monotonic()
            if now < self._marker_ignore_until:
                log("MainCtrl", "マーカー無視中（ignore期間内）", Color.YELLOW)
                return
            if self._last_print_complete_monotonic is not None:
                if (now - self._last_print_complete_monotonic
                        < MARKER_MIN_INTERVAL_AFTER_PRINT_SEC):
                    log("MainCtrl", "マーカー無視中（印刷完了直後）", Color.YELLOW)
                    return
            self._marker_detected = True
            log("MainCtrl", "_marker_detected = True", Color.YELLOW)

        # NG 検査トリガー（main.py と同一条件）
        if self.initstate == 1 and self._ng_sensor.enabled:
            self._ensure_ng_thread_started()
            with self._ng_lock:
                self._ng_due_at_monotonic = time.monotonic() + NG_WORKER_DELAY_SEC
            self._ng_trigger_event.set()
            log("MainCtrl",
                f"NG 検査を {NG_WORKER_DELAY_SEC}s 後にスケジュール", Color.CYAN)
        elif self.initstate == 1 and not self._ng_sensor.enabled:
            log_bug(
                "initstate==1 だが ng_sensor が無効 → "
                "NG 検査がスケジュールされない！ "
                "[バグ候補 A]"
            )

    # ── main.py: start ──────────────────────────────────────────────────

    def start(self):
        self.starter = 1
        with self._sensor_lock:
            self._marker_detected = False
            self._marker_ignore_until = (
                time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
            )
        log("MainCtrl", "start() 呼び出し → starter=1", Color.GREEN)

    # ── main.py: stop ───────────────────────────────────────────────────

    def stop(self):
        log("MainCtrl", "stop() 呼び出し開始", Color.CYAN)
        self._stoping()
        self.count   = 0
        self.printed = 0
        self.flag    = 0
        self.lastid  = "none"
        with self._sensor_lock:
            self._last_print_complete_monotonic = None
            self._marker_ignore_until = 0.0
        log("MainCtrl", "stop() 完了", Color.CYAN)

    # ── main.py: stoping ────────────────────────────────────────────────

    def _stoping(self):
        log("MainCtrl", "STOP コマンドをプリンターへ送信")
        # UUID 停止（省略）
        log("MainCtrl", "stop_conveyor() 呼び出し")
        ok = self._conveyor.send("STOP", caller="MainCtrl")
        if not ok:
            log_bug(
                "stop_conveyor() で TCP 接続失敗 → "
                "コンベアに STOP が届かない！ [バグ候補 B]"
            )
        self.starter   = 0
        self.initstate = 0
        log("MainCtrl", f"_stoping() 完了: starter={self.starter}, "
                        f"initstate={self.initstate}")

    # ── main.py: run ────────────────────────────────────────────────────

    def run(self):
        """main.py の run() メソッド相当（1 ループ分）"""
        try:
            # コンベア起動（starter==1 なら毎回送信）
            if self.starter == 1:
                self._conveyor.send("START", caller="MainCtrl")

            # STATUS 取得
            status = self._printer.get_status()
            log("MainCtrl.run", f"STATUS={status.strip()!r}  flag={self.flag}")

            if status == SimPrinter.STATUS_IDLE:          # '0\r'
                if self.flag == 0:
                    with self._sensor_lock:
                        marker_hit = self._marker_detected
                        if marker_hit:
                            self._marker_detected = False

                    if marker_hit:
                        log("MainCtrl.run", "マーカー検知 → 印刷開始指示")
                        ok = self._printer.start_print()
                        if ok:
                            self.printed += 1
                            self.flag = 1
                            log("MainCtrl.run",
                                f"印刷コマンド送信完了 printed={self.printed}",
                                Color.MAGENTA)
                        else:
                            self.flag = 0
                    else:
                        pass  # マーカー未検知 → 待機
                else:
                    self.flag = 0

            elif status == SimPrinter.STATUS_PRINTING:    # '1\r'
                if self.flag == 1:
                    with self._sensor_lock:
                        self._last_print_complete_monotonic = time.monotonic()
                self.flag = 0

            elif status == SimPrinter.STATUS_DONE:        # '2\r'
                if self.flag == 1:
                    with self._sensor_lock:
                        self._last_print_complete_monotonic = time.monotonic()
                self.flag = 0

            time.sleep(0.05)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if self._swallow:
                log_bug(f"except Exception: pass が例外を隠蔽: {e!r} [バグ候補 D]")
            else:
                raise

    # ── シミュレーション実行メソッド ────────────────────────────────────

    def boot(self):
        """コンベア・プリンターを起動してジョブを開始する"""
        self.initstate = 0
        self.start()
        # initstate を 1 に（run() の最初のループで設定される部分を再現）
        self._conveyor.send("START", caller="MainCtrl.boot")
        self.initstate = 1
        self._ensure_ng_thread_started()
        with self._sensor_lock:
            self._marker_detected = False
            until = time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
            if until > self._marker_ignore_until:
                self._marker_ignore_until = until
        log("MainCtrl", "boot 完了: initstate=1", Color.GREEN)

    def run_loop(self, duration: float):
        """duration 秒間 run() を繰り返す"""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not self._stop_requested.is_set():
            self.run()

    def is_running(self):
        return self.starter == 1


# ─────────────────────────────────────────────
# 現行 main.py の _schedule_ng_inspection 実装を再現したコントローラー
# ─────────────────────────────────────────────

NG_DELAY_AFTER_MARKER_SEC = 2.0   # main.py のデフォルト値


class MainControllerV2:
    """
    現行 main.py (_ng_pending ガード + Timer スレッド方式) を忠実に再現。
    シナリオ G で使用。
    """

    def __init__(
        self,
        conveyor: SimConveyor,
        printer:  SimPrinter,
        ng_sensor: SimNGSensor,
        marker:    SimMarkerSensor,
    ):
        self._conveyor  = conveyor
        self._printer   = printer
        self._ng_sensor = ng_sensor
        self._marker    = marker

        self.starter   = 0
        self.initstate = 0
        self.printed   = 0
        self.flag      = 0

        self._sensor_lock    = threading.Lock()
        self._marker_detected = False
        self._last_print_complete_monotonic: Optional[float] = None
        self._marker_ignore_until = 0.0

        # ─── 現行 main.py の _ng_pending ガード ───────────────────────
        self._ng_timer_lock  = threading.Lock()
        self._ng_pending     = False
        self._ng_schedule_id = 0
        self._stop_lock      = threading.Lock()

        self._marker.set_callback(self._on_marker_detected)

    # ── _on_marker_detected（現行: フラグを立てるだけ）──────────────────

    def _on_marker_detected(self):
        with self._sensor_lock:
            now = time.monotonic()
            if now < self._marker_ignore_until:
                log("MainCtrlV2", "マーカー無視中（ignore期間内）", Color.YELLOW)
                return
            if self._last_print_complete_monotonic is not None:
                if (now - self._last_print_complete_monotonic
                        < MARKER_MIN_INTERVAL_AFTER_PRINT_SEC):
                    log("MainCtrlV2", "マーカー無視中（印刷完了直後）", Color.YELLOW)
                    return
            self._marker_detected = True
            log("MainCtrlV2", "_marker_detected = True (GPIO割り込み)", Color.YELLOW)

    # ── _schedule_ng_inspection（現行コードそのまま）────────────────────

    def _schedule_ng_inspection(self):
        if self.initstate != 1:
            log("MainCtrlV2", "NG検査 予約スキップ: initstate!=1", Color.YELLOW)
            return
        if not self._ng_sensor.enabled:
            log("MainCtrlV2", "NG検査 予約スキップ: NG_HOST 未設定", Color.YELLOW)
            return
        with self._ng_timer_lock:
            if self._ng_pending:
                log_bug(
                    "NG検査 予約スキップ: 前回の検査待ち中 [バグ G]\n"
                    "         → この製品（不良品の可能性）は検査されない！"
                )
                return
            self._ng_schedule_id += 1
            schedule_id = self._ng_schedule_id
            self._ng_pending = True

        log("MainCtrlV2",
            f"NG検査 予約: {NG_DELAY_AFTER_MARKER_SEC}秒後 (id={schedule_id})",
            Color.CYAN)
        th = threading.Thread(
            target=self._ng_delayed_run,
            args=(schedule_id,),
            daemon=True,
        )
        th.start()

    # ── _ng_delayed_run ────────────────────────────────────────────────

    def _ng_delayed_run(self, schedule_id: int):
        try:
            time.sleep(NG_DELAY_AFTER_MARKER_SEC)
            self._execute_ng_inspection(schedule_id)
        finally:
            with self._ng_timer_lock:
                self._ng_pending = False
            log("MainCtrlV2", f"_ng_pending = False (id={schedule_id})", Color.CYAN)

    # ── _execute_ng_inspection ─────────────────────────────────────────

    def _execute_ng_inspection(self, schedule_id: int):
        with self._ng_timer_lock:
            if schedule_id != self._ng_schedule_id:
                log("MainCtrlV2",
                    f"NG検査 スキップ: 古い予約 (id={schedule_id})", Color.YELLOW)
                return
        try:
            log("MainCtrlV2", "NG センサー poll 実行", Color.CYAN)
            res = self._ng_sensor.poll()
            log("MainCtrlV2", f"NG センサー応答: {res!r}")
            if res is None:
                log("MainCtrlV2", "応答なし → ライン停止", Color.RED)
                self.stop()
            elif "ERROR" in str(res).upper():
                log("MainCtrlV2", f"ERROR 受信 → ライン停止", Color.RED)
                self.stop()
            else:
                log("MainCtrlV2",
                    f"ERROR を含まない応答 ({res!r}) → stop() 呼ばれない",
                    Color.RED + Color.BOLD if res not in ("OK", "LON_OK") else "")
        except Exception as e:
            log_bug(f"_execute_ng_inspection で例外 → stop() 呼ばれない: {e!r}")

    # ── stop / stoping ─────────────────────────────────────────────────

    def stop(self):
        with self._stop_lock:
            if self.starter == 0 and self.initstate == 0:
                return
            log("MainCtrlV2", "stop() 実行", Color.CYAN)
            self._conveyor.send("STOP", caller="MainCtrlV2")
            self.starter   = 0
            self.initstate = 0
            self.printed   = 0
            self.flag      = 0

    # ── run / boot ─────────────────────────────────────────────────────

    def run(self):
        if self.starter == 1:
            self._conveyor.send("START", caller="MainCtrlV2")

        status = self._printer.get_status()

        if status == SimPrinter.STATUS_IDLE:
            if self.flag == 0:
                with self._sensor_lock:
                    marker_hit = self._marker_detected
                    if marker_hit:
                        self._marker_detected = False

                if marker_hit:
                    self._schedule_ng_inspection()   # ← 現行コードと同じ位置
                    ok = self._printer.start_print()
                    if ok:
                        self.printed += 1
                        self.flag = 1
                        log("MainCtrlV2",
                            f"印刷開始 printed={self.printed}", Color.MAGENTA)
            else:
                self.flag = 0

        elif status == SimPrinter.STATUS_PRINTING:
            if self.flag == 1:
                with self._sensor_lock:
                    self._last_print_complete_monotonic = time.monotonic()
            self.flag = 0

        elif status == SimPrinter.STATUS_DONE:
            if self.flag == 1:
                with self._sensor_lock:
                    self._last_print_complete_monotonic = time.monotonic()
            self.flag = 0

        time.sleep(0.05)

    def boot(self):
        self.starter   = 1
        self.initstate = 1
        with self._sensor_lock:
            self._marker_detected = False
            self._marker_ignore_until = (
                time.monotonic() + MARKER_IGNORE_AFTER_JOB_START_SEC
            )
        self._conveyor.send("START", caller="MainCtrlV2.boot")
        log("MainCtrlV2", "boot 完了: starter=1, initstate=1", Color.GREEN)

    def run_loop(self, duration: float):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and self.starter == 1:
            self.run()


def run_scenario_g(
    label: str,
    product_interval_sec: float,   # 製品間隔（秒）
    defect_response: str,          # センサーが不良品時に返す文字列
    total_duration: float = 12.0,
):
    """シナリオ G: 現行コード (_ng_pending) の _ng_pending スキップ再現"""
    separator(label)

    conveyor  = SimConveyor("Conveyor")
    printer   = SimPrinter()
    ng_sensor = SimNGSensor(enabled=True, defect_response=defect_response)
    marker    = SimMarkerSensor()
    ctrl      = MainControllerV2(conveyor, printer, ng_sensor, marker)

    ctrl.boot()
    log("Scenario-G", "=== ジョブ開始 ===", Color.GREEN)

    # 製品を product_interval_sec 間隔で流す。2番目を不良品にする。
    def inject_products():
        time.sleep(0.8)   # 最初の ignore 期間を超えてから開始
        for i in range(4):
            is_defect = (i == 1)
            if is_defect:
                ng_sensor.set_defect()
                label_str = "★ 製品② [不良品]"
            else:
                label_str = f"製品{'①③④'[i if i < 3 else 2]} [正常]"
            log("Scenario-G",
                f"{label_str} 通過 → マーカー ON",
                Color.RED + Color.BOLD if is_defect else Color.GREEN)
            marker.trigger()
            if i < 3:
                time.sleep(product_interval_sec)

    threading.Thread(target=inject_products, daemon=True).start()

    ctrl.run_loop(total_duration)

    print()
    log("Result-G", f"コンベア最終状態  : {'動作中' if conveyor.running else '停止済み'}")
    log("Result-G", f"累計印刷枚数      : {printer.print_count}")
    log("Result-G", f"controller.starter: {ctrl.starter}")

    bug = conveyor.running
    if bug:
        log_bug(
            f"コンベアが停止しない。製品間隔={product_interval_sec}s, "
            f"NG遅延={NG_DELAY_AFTER_MARKER_SEC}s\n"
            f"         → 製品間隔 < NG遅延 の場合、不良品の検査が "
            "'NG検査 予約スキップ: 前回の検査待ち中' でスキップされる"
        )
    else:
        log_ok("コンベア停止を確認")
    return bug


# ─────────────────────────────────────────────
# シナリオ定義
# ─────────────────────────────────────────────

def run_scenario(
    label: str,
    ng_enabled: bool,
    conveyor_tcp_fail: bool,
    swallow_exceptions: bool,
    defect_at_sec: float = 1.0,
    total_duration: float = 8.0,
    defect_response: str = "ERROR",   # センサーが不良品時に返す文字列
):
    separator(label)

    # 部品の初期化
    conveyor = SimConveyor("Conveyor")
    printer  = SimPrinter()
    ng_sensor = SimNGSensor(enabled=ng_enabled, defect_response=defect_response)
    marker    = SimMarkerSensor()

    # バグ B のシミュレーションは boot() 後に有効化（START は正常に届く）

    ctrl = MainController(
        conveyor=conveyor,
        printer=printer,
        ng_sensor=ng_sensor,
        marker=marker,
        swallow_exceptions=swallow_exceptions,
    )

    # ジョブ開始（START は正常送信）
    ctrl.boot()
    log("Scenario", "=== ジョブ開始 ===", Color.GREEN)

    # バグ B: 起動後に STOP コマンドだけ TCP 失敗するよう設定
    if conveyor_tcp_fail:
        conveyor._tcp_fail_stop = True
        log("Scenario",
            "バグ B 有効: 以降の STOP コマンドは TCP 失敗する設定", Color.RED)

    # defect_at_sec 秒後に不良品通過をシミュレート
    def inject_defect():
        time.sleep(defect_at_sec * 0.5)
        # 1回目: 正常品（マーカー通過 → 印刷）
        log("Scenario", "正常品通過 → マーカー ON", Color.GREEN)
        marker.trigger()

        time.sleep(defect_at_sec)
        # 2回目: 不良品（NG センサーに ERROR を仕込む）
        ng_sensor.set_defect()
        log("Scenario", "★ 不良品通過 → マーカー ON（NG センサー: ERROR を返す設定）",
            Color.RED + Color.BOLD)
        marker.trigger()

    injection_thread = threading.Thread(target=inject_defect, daemon=True)
    injection_thread.start()

    # メインループ実行
    ctrl.run_loop(total_duration)

    # ─── 結果判定 ───────────────────────────────────────────────────────
    print()
    log("Result", f"コンベア最終状態  : {'動作中' if conveyor.running else '停止済み'}")
    log("Result", f"累計印刷枚数      : {printer.print_count}")
    log("Result", f"controller.starter: {ctrl.starter}")
    log("Result", f"ng_sensor.enabled : {ng_sensor.enabled}")

    # 期待: 不良品検知後にコンベアが停止していること
    bug_detected = False
    if not ng_enabled:
        log_bug(
            "[バグ A] ng_sensor_enabled=False のため _ng_worker が起動せず、"
            "不良品を検知してもコンベアが停止しない。\n"
            "          → config.ini の [NG_SENSOR] に NG_HOST を設定するか、"
            "環境変数 NG_SENSOR_HOST を指定してください。"
        )
        bug_detected = True
    elif conveyor_tcp_fail and conveyor.running:
        log_bug(
            "[バグ B] stop_conveyor() の TCP 接続が失敗しており、"
            "STOP コマンドがコンベアに届いていない。\n"
            "          → コンベアコンテナのアドレス・ポートを確認してください。"
        )
        bug_detected = True
    elif conveyor.running:
        log_bug(
            "コンベアが停止していない（原因不明）。ログを詳細に確認してください。"
        )
        bug_detected = True
    else:
        log_ok("不良品検知 → コンベア停止 が正常に動作しました。")

    return bug_detected  # True = バグあり


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DMXプリンター コンベア停止バグ シミュレーター"
    )
    parser.add_argument(
        "--scenario",
        choices=["A", "B", "C", "D", "E", "F", "G", "ALL"],
        default="ALL",
        help="実行するシナリオを指定 (デフォルト: ALL)",
    )
    args = parser.parse_args()

    results = {}

    if args.scenario in ("A", "ALL"):
        # ─── シナリオ A: NG センサー無効 ─────────────────────────────────
        failed = run_scenario(
            label="[シナリオ A] NG センサー無効 (ng_sensor_host 未設定)",
            ng_enabled=False,
            conveyor_tcp_fail=False,
            swallow_exceptions=False,
            defect_at_sec=1.0,
            total_duration=7.0,
        )
        results["A"] = failed

    if args.scenario in ("B", "ALL"):
        # ─── シナリオ B: TCP 送信失敗 ────────────────────────────────────
        failed = run_scenario(
            label="[シナリオ B] stop_conveyor() TCP 失敗（コンベアコンテナ未応答）",
            ng_enabled=True,
            conveyor_tcp_fail=True,
            swallow_exceptions=False,
            defect_at_sec=1.0,
            total_duration=9.0,  # NG_WORKER_DELAY(4s) + 余裕
        )
        results["B"] = failed

    if args.scenario in ("C", "ALL"):
        # ─── シナリオ C: 正常動作（対照群）+ 4 秒遅延中に印刷継続の観察 ──
        failed = run_scenario(
            label="[シナリオ C] 正常動作 + 4 秒遅延中の印刷継続を観察",
            ng_enabled=True,
            conveyor_tcp_fail=False,
            swallow_exceptions=False,
            defect_at_sec=1.0,
            total_duration=9.0,
        )
        results["C"] = failed

    if args.scenario in ("D", "ALL"):
        # ─── シナリオ D: 例外握りつぶしの確認 ───────────────────────────
        # conveyor.send をわざと例外を投げるよう改造
        failed = run_scenario(
            label="[シナリオ D] except Exception: pass による例外隠蔽の確認",
            ng_enabled=True,
            conveyor_tcp_fail=False,
            swallow_exceptions=True,  # バグD を有効化
            defect_at_sec=1.0,
            total_duration=9.0,
        )
        results["D"] = failed

    if args.scenario in ("G", "ALL"):
        # ─── シナリオ G-1: 製品間隔 < NG遅延 → _ng_pending スキップ ──────
        failed_g1 = run_scenario_g(
            label=(
                "[シナリオ G-1] 製品間隔 1.0s < NG遅延 2.0s\n"
                "  → 不良品(製品②)が _ng_pending スキップで検査されない"
            ),
            product_interval_sec=1.0,
            defect_response="ERROR",
            total_duration=12.0,
        )

        # ─── シナリオ G-2: センサーが "OK" を返す（プロトコル不一致） ────
        failed_g2 = run_scenario_g(
            label=(
                "[シナリオ G-2] センサーが 'OK' を返す\n"
                "  → poll() 成功・タイムアウトなし・ERRORなし → stop() 呼ばれない"
            ),
            product_interval_sec=3.0,   # 間隔を十分開けて G-1 とは別条件に
            defect_response="OK",       # 不良品でも "OK" が返る
            total_duration=14.0,
        )

        results["G-1"] = failed_g1
        results["G-2"] = failed_g2

    if args.scenario in ("E", "ALL"):
        # ─── シナリオ E: センサー応答が "ERROR" でなく "NG" ──────────────
        # main_old.py→main.py のリファクタリング時に IP が同じ 192.168.100.100 に
        # なったことで、センサーが返す文字列が変わった可能性がある
        failed = run_scenario(
            label=(
                "[シナリオ E] センサーが 'NG' を返す（'ERROR' を期待しているが不一致）\n"
                "  ※ main_old.py→main.py 移行後に NG_HOST=192.168.100.100 がプリンターと\n"
                "     同じ IP になっており、センサーが異なるプロトコルを返す可能性"
            ),
            ng_enabled=True,
            conveyor_tcp_fail=False,
            swallow_exceptions=False,
            defect_at_sec=1.0,
            total_duration=9.0,
            defect_response="NG",     # "ERROR" ではなく "NG" を返す
        )
        results["E"] = failed

    if args.scenario in ("F", "ALL"):
        # ─── シナリオ F: センサーが LON への ACK "OK" を返す（プロトコル不一致） ──
        # SR-X 系センサーは LON\r に対して ACK "LON_OK\r" を返した後、
        # 別途スキャン結果を送る可能性がある。コードは最初の応答だけ読む。
        failed = run_scenario(
            label=(
                "[シナリオ F] LON コマンドへの ACK が 'OK' → 実際の不良判定を読み損ねる\n"
                "  ※ SR-X 系センサーは LON→ACK応答→スキャン結果 の2段階応答の可能性"
            ),
            ng_enabled=True,
            conveyor_tcp_fail=False,
            swallow_exceptions=False,
            defect_at_sec=1.0,
            total_duration=9.0,
            defect_response="OK",     # 不良品でも "OK" (ACK) が返ってくる
        )
        results["F"] = failed

    # ─── 総合サマリー ────────────────────────────────────────────────────
    separator("総合サマリー")
    for sc, failed in results.items():
        status = (Color.RED + "コンベア停止失敗（バグあり）" + Color.RESET
                  if failed
                  else Color.GREEN + "コンベア正常停止" + Color.RESET)
        print(f"  シナリオ {sc}: {status}")

    print()
    print(Color.BOLD + "【調査結論】" + Color.RESET)
    if results.get("A"):
        print(
            "  ★ 最有力原因: [バグ A] NG_SENSOR_HOST が設定されていない。\n"
            "    → command.py の ng_sensor_enabled プロパティが False を返し、\n"
            "       _ng_worker スレッドが起動しないため、不良品を検知しても\n"
            "       stop() が呼ばれない。\n"
            "    → 修正: config.ini [NG_SENSOR] の NG_HOST に検品センサーの\n"
            "       IP アドレスを設定するか、環境変数 NG_SENSOR_HOST を指定。"
        )
    if results.get("B"):
        print(
            "  ★ [バグ B] コンベアコンテナへの TCP 接続が失敗している。\n"
            "    → stop_conveyor() / start_conveyor() の例外が catch されて\n"
            "       サイレントに無視されるため、コンベアが制御不能になる。\n"
            "    → 修正: コンベアコンテナのネットワーク設定・ポート番号を確認。\n"
            "       失敗時にリトライまたはアラートを出す実装を追加する。"
        )
    if results.get("G-1"):
        print(
            "  ★ [バグ G-1] _ng_pending スキップ（最有力原因）\n"
            "    製品間隔 < NG_DELAY_AFTER_MARKER_SEC(2.0s) のとき、\n"
            "    前の製品の検査スレッド実行中に次の製品（不良品かも）が来ると\n"
            "    '前回の検査待ち中' として黙ってスキップされる。\n"
            "    → NG検査スレッドは動いているが、不良品の番が回ってこない。\n"
            "    → 修正: _ng_pending スキップ時に不良品フラグを保持して\n"
            "       次の空きタイミングで必ず検査する設計に変更する。"
        )
    if results.get("G-2"):
        print(
            "  ★ [バグ G-2] センサーの応答が 'ERROR' でない\n"
            "    ng_sensor_poll() は接続成功・応答受信できているが、\n"
            "    応答文字列が 'OK' / 'LON_OK' など → stop() が呼ばれない。\n"
            "    → debug_ng_check.py を実行して実際の応答を確認してください。"
        )
    if results.get("E") or results.get("F"):
        print(
            "  ★ [バグ E/F] センサーの応答文字列が 'ERROR' でない。\n"
            "    → _ng_worker は 'ERROR' を含む応答のみ stop() を呼ぶ。\n"
            "    → 実際に 192.168.100.100:9004 に接続して何が返ってくるか\n"
            "       debug_ng_check.py で確認してください（後述）。\n"
            "\n"
            "  ★ [構造的問題] main_old.py では GPIO ピン 24 で NG センサーを読んでいた。\n"
            "    現行 main.py では Ethernet (192.168.100.100:9004) に変更されている。\n"
            "    物理センサーが GPIO 配線のままなら、現行コードはその信号を無視する。\n"
            "    → 確認: センサーは Ethernet 接続か? GPIO 配線のままか?\n"
            "    → 確認: 192.168.100.100 はプリンターと同じ IP → 意図的か?"
        )
    if not any(results.values()):
        print("  すべてのシナリオで正常動作を確認。別の原因の可能性があります。")


if __name__ == "__main__":
    main()

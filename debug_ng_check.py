#!/usr/bin/env python3
"""
NG センサー 実機診断スクリプト
================================
目的: センサーに実際に接続し、LON コマンドへの応答を確認する。

使い方:
    python3 debug_ng_check.py
    python3 debug_ng_check.py --host 192.168.100.110 --port 9004
    python3 debug_ng_check.py --host 192.168.100.110 --port 9004 --raw
    python3 debug_ng_check.py --host 192.168.100.110 --lon-timeout 5.0

オプション:
    --host          センサー IP  (デフォルト: 192.168.100.110)
    --port          ポート番号   (デフォルト: 9004)
    --timeout       TCP 接続タイムアウト秒 (デフォルト: 5.0)
    --lon-timeout   LON 送信後の応答待ちタイムアウト秒 (デフォルト: 3.0)
                    この時間内に応答がなければ LOFF を送信して終了する
    --raw           生バイト（hex）も表示する

フロー:
    1. TCP 接続
    2. 接続直後の自発メッセージ確認（1 秒）
    3. LON\\r 送信 → --lon-timeout 秒待機
       ├─ 応答あり → 判定 → LOFF\\r → RESET\\r → close
       └─ タイムアウト → LOFF\\r 送信 → close
"""

import argparse
import socket
import time


# ─────────────────────────────────────────────────────────────────────────────
# 低レベルヘルパー
# ─────────────────────────────────────────────────────────────────────────────

def recv_line(sock: socket.socket, wait_sec: float, show_raw: bool) -> str | None:
    """
    CR (\\r) 区切りで 1 行読む。
    wait_sec 秒以内に \\r が来なければ None を返す。
    """
    buf = bytearray()
    deadline = time.monotonic() + wait_sec

    while True:
        if b"\r" in buf:
            idx  = buf.index(b"\r")
            line = buf[:idx].decode("utf-8", errors="replace").strip()
            rest = bytes(buf[idx + 1:])
            if show_raw:
                print(f"     [raw recv] {bytes(buf[:idx + 1])!r}  "
                      f"残り={rest!r}")
            return line

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None                          # タイムアウト

        sock.settimeout(min(remaining, 0.2))
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            continue
        except OSError as e:
            print(f"  ✗ recv エラー: {e}")
            return None

        if not chunk:
            print("  ✗ 接続が切断されました")
            return None

        buf.extend(chunk)
        if show_raw:
            print(f"  ← チャンク受信: {chunk!r}  hex: {chunk.hex()}")


def send_cmd(sock: socket.socket, cmd: bytes, label: str,
             wait_sec: float, show_raw: bool) -> str | None:
    """
    コマンドを送信し、応答 1 行を返す。
    送信失敗時は None。
    """
    print(f"  → 送信: {cmd!r}  ({label})")
    try:
        sock.sendall(cmd)
    except OSError as e:
        print(f"  ✗ 送信エラー: {e}")
        return None

    resp = recv_line(sock, wait_sec, show_raw)
    if resp is None:
        print(f"  ← 応答なし ({wait_sec:.1f}s タイムアウト)")
    else:
        print(f"  ← 応答: {resp!r}")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# メイン診断ロジック
# ─────────────────────────────────────────────────────────────────────────────

def check_ng_sensor(
    host: str,
    port: int,
    conn_timeout: float,
    lon_timeout: float,
    show_raw: bool,
):
    print()
    print("=" * 60)
    print("  NG センサー 接続診断")
    print(f"  接続先     : {host}:{port}")
    print(f"  接続TO     : {conn_timeout}s")
    print(f"  LON 応答TO : {lon_timeout}s  ← タイムアウト時は LOFF を送信")
    print("=" * 60)

    # ─── Step 1: TCP 接続 ─────────────────────────────────────────────────
    print("\n[Step 1] TCP 接続...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(conn_timeout)
    t0 = time.monotonic()
    try:
        s.connect((host, port))
    except socket.timeout:
        print(f"  ✗ タイムアウト ({conn_timeout}s)")
        print("    → IP アドレス / ポート番号 / ネットワーク経路を確認してください")
        s.close()
        return
    except ConnectionRefusedError:
        print(f"  ✗ 接続拒否 (ポート {port} が閉じている)")
        print("    → センサーが TCP {port} で待ち受けていない可能性があります")
        s.close()
        return
    except OSError as e:
        print(f"  ✗ 接続エラー: {e}")
        s.close()
        return

    print(f"  ✓ 接続成功 ({time.monotonic() - t0:.2f}s)")

    # ─── Step 2: 接続直後の自発メッセージ確認 ─────────────────────────────
    print("\n[Step 2] 接続直後の自発メッセージ確認 (1.0s 待機)...")
    spontaneous = recv_line(s, wait_sec=1.0, show_raw=show_raw)
    if spontaneous is None:
        print("  (自発メッセージなし)")
    else:
        print(f"  ← 自発メッセージ: {spontaneous!r}")

    # ─── Step 3: LON\\r 送信 → lon_timeout 秒待機 ───────────────────────────
    print(f"\n[Step 3] LON\\r 送信 → 最大 {lon_timeout}s 応答待ち...")
    t_lon = time.monotonic()

    try:
        s.sendall(b"LON\r")
        if show_raw:
            print("  [raw send] b'LON\\r'")
    except OSError as e:
        print(f"  ✗ LON 送信エラー: {e}")
        s.close()
        return

    response = recv_line(s, wait_sec=lon_timeout, show_raw=show_raw)
    elapsed  = time.monotonic() - t_lon

    if response is None:
        # ── タイムアウト ────────────────────────────────────────────────────
        print(f"\n  ⚠ {lon_timeout}s 経過しても応答なし → タイムアウト")
        print("  → LOFF\\r を送信してセンサーを読み取りモードから戻します...")
        _send_loff(s, show_raw)
        s.close()
        print("\n[完了] 接続を閉じました（タイムアウト終了）")
        _print_timeout_hint(host, port)
        return

    # ── 応答あり ──────────────────────────────────────────────────────────
    print(f"\n  ← 応答 ({elapsed:.2f}s): {response!r}")

    # バッファに残りがあれば表示
    try:
        s.settimeout(0.3)
        tail = s.recv(1024)
        if tail:
            print(f"  ← 追加データ: {tail!r}")
            if show_raw:
                print(f"     hex: {tail.hex()}")
    except socket.timeout:
        pass

    # 判定
    _judge_response(response)

    # ─── Step 4: クリーンアップ (LOFF → RESET) ───────────────────────────
    print("\n[Step 4] クリーンアップ: LOFF\\r → RESET\\r 送信...")
    _send_loff(s, show_raw)
    send_cmd(s, b"RESET\r", "RESET", wait_sec=1.0, show_raw=show_raw)

    s.close()
    print("\n[完了] 接続を閉じました")


# ─────────────────────────────────────────────────────────────────────────────
# サブ処理
# ─────────────────────────────────────────────────────────────────────────────

def _send_loff(sock: socket.socket, show_raw: bool):
    """LOFF\\r を送信して応答を 1 秒待つ。"""
    resp = send_cmd(sock, b"LOFF\r", "LOFF", wait_sec=1.0, show_raw=show_raw)
    if resp is None:
        print("  (LOFF への応答なし — 送信は完了)")


def _judge_response(response: str):
    """応答文字列を解析して診断コメントを出力する。"""
    upper = response.upper()
    print()
    if "ERROR" in upper:
        print("  判定: ✓ 'ERROR' を含む → stop() が呼ばれる（正常動作）")
    elif "NG" in upper:
        print("  判定: ✗ 'NG' を含むが 'ERROR' ではない → stop() が呼ばれない")
        print("  → command.py の判定条件に 'NG' を追加する:")
        print('       elif "ERROR" in res.upper() or "NG" in res.upper():')
        print("           self.stop()")
    elif upper in ("OK", "LON_OK", "ACK", ""):
        print("  判定: ✗ ACK 応答のみ受信 → 実際の検品結果は別のタイミングで送られる可能性")
        print("  → LON 送信後に 2 行目以降も読み取る実装が必要")
        print("  → またはセンサーのマニュアルで正しいコマンド体系を確認してください")
    else:
        print(f"  判定: ✗ 未知の応答 {response!r} → センサーマニュアルで意味を確認")


def _print_timeout_hint(host: str, port: int):
    """タイムアウト時の追加確認案内を表示する。"""
    print()
    print("=" * 60)
    print("  タイムアウト時の確認事項")
    print("=" * 60)
    print(f"""
  1. センサーが {host} で TCP {port} を待ち受けているか確認
       $ ping {host}
       $ nc -zv {host} {port}

  2. Docker コンテナ内からの到達確認
       $ docker exec dmxprint-main ping -c 3 {host}
       $ docker exec dmxprint-main python3 \\
             /home/Python/src/main/debug_ng_check.py \\
             --host {host} --port {port}

  3. Pi ホスト上での IP フォワーディング確認
       $ sysctl net.ipv4.ip_forward
       → 0 の場合: sudo sysctl -w net.ipv4.ip_forward=1

  4. LON コマンドへの応答が 3 秒を超えるセンサーの場合
       --lon-timeout を延長して再試行
       $ python3 debug_ng_check.py --host {host} --lon-timeout 10.0
""")


# ─────────────────────────────────────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NG センサー 実機接続診断スクリプト",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host", default="192.168.100.110",
        help="センサーの IP アドレス",
    )
    parser.add_argument(
        "--port", type=int, default=9004,
        help="センサーのポート番号",
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0,
        help="TCP 接続タイムアウト（秒）",
    )
    parser.add_argument(
        "--lon-timeout", type=float, default=3.0,
        dest="lon_timeout",
        help="LON 送信後の応答待ちタイムアウト（秒）。超えたら LOFF を送信して終了",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="生バイト（hex）も表示する",
    )
    args = parser.parse_args()

    check_ng_sensor(
        host        = args.host,
        port        = args.port,
        conn_timeout= args.timeout,
        lon_timeout = args.lon_timeout,
        show_raw    = args.raw,
    )


if __name__ == "__main__":
    main()

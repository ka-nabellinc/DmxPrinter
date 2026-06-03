# Threading採用の役割整理

このドキュメントでは、DMXプリンターシステムにおけるThreadingの採用箇所とその役割を整理します。

## 1. カスタムThreadクラス (`thread`)

### 場所
- `apis/main/src/sleevePrinterInterface/sleevePrinterInterface.py`
- `apis/sleevePrinterInterfaceDriver-main/src/sleevePrinterInterfaceDriver/apis/thread/thread.py`
- `sdk/sleevePrinterInterface/sleevePrinterInterface.py`

### 役割
標準の`threading.Thread`を拡張したカスタムThreadクラス。以下の機能を提供：

1. **通常のThread機能**: `threading.Thread`を継承し、基本的なスレッド機能を提供
2. **強制終了機能**: `raise_exception()`メソッドにより、スレッドを外部から強制終了可能
   - `ctypes`を使用してスレッドIDを取得し、`SystemExit`例外を送信することで実現

### 使用目的
- 長時間実行されるバックグラウンド処理を安全に停止するため
- HTTPサーバーやシリアル通信などのブロッキング処理を非同期で実行するため

---

## 2. HTTPサーバー用スレッド

### 場所
`apis/main/src/sleevePrinterInterface/sleevePrinterInterface.py`

### 実装箇所
```125:126:apis/main/src/sleevePrinterInterface/sleevePrinterInterface.py
self.__serverTH = thread(target=self.httpServer)
self.__serverTH.start()
```

### 役割
- **HTTPサーバーの非同期実行**: ポート8989でHTTPサーバーを起動し、外部からのリクエストを処理
- **APIエンドポイント提供**:
  - `GET /start`: 印刷開始
  - `GET /stop`: 印刷停止
  - `GET /printed`: 印刷済み枚数の取得
  - `GET /finished`: ジョブ完了状態の取得
  - `POST /job`: ジョブ情報の設定

### 必要性
- メイン処理（印刷制御ループ）をブロックせずに、HTTPリクエストを常時受付可能にするため
- 外部システム（Web UIなど）からの制御コマンドを受け付けるため

---

## 3. シリアル通信受信スレッド

### 場所
`apis/sleevePrinterInterfaceDriver-main/src/sleevePrinterInterfaceDriver/apis/mainCommander/mainCommander.py`

### 実装箇所
```31:35:apis/sleevePrinterInterfaceDriver-main/src/sleevePrinterInterfaceDriver/apis/mainCommander/mainCommander.py
self.receiveThread = thread.thread(target=self.receive)
self.receiveThread.start()

self.parseThread = thread.thread(target=self.parse)
self.parseThread.start()
```

### 役割

#### 3.1 受信スレッド (`receive`)
- **シリアルポートからのデータ受信**: プリンターインターフェースデバイスから送信されるデータを継続的に受信
- **キューへの格納**: 受信したデータを`serialQue`に追加

#### 3.2 解析スレッド (`parse`)
- **データの分類**: `serialQue`からデータを取り出し、以下のキューに分類
  - `buttonQue`: ボタン操作（start, stop）
  - `keyQue`: キー入力（enter, back, 0-9）

### 必要性
- シリアル通信の`readline()`はブロッキング処理のため、メイン処理を停止させないようにするため
- 受信と解析を分離することで、処理の効率化と応答性の向上を実現

---

## 4. UIモード別の受信スレッド

### 場所
以下の各モードクラス：
- `apis/modes/printing.py`
- `apis/modes/confirm.py`
- `apis/modes/home.py`
- `apis/modes/showIP.py`
- `apis/modes/selectJob.py`
- `apis/modes/confirmCancel.py`
- `apis/modes/selectQty.py`

### 実装例（printing.py）
```24:25:apis/sleevePrinterInterfaceDriver-main/src/sleevePrinterInterfaceDriver/apis/modes/printing.py
self.receiveThread = thread.thread(target=self.receive)
self.receiveThread.start()
```

### 役割
各UIモードで、ユーザー入力（キー操作）を非同期で監視：

1. **printingモード**: 
   - キー入力の監視（backキーで前の画面へ）
   - 定期的に印刷進捗情報をHTTP APIから取得

2. **confirmモード**: 
   - enter/backキーの監視
   - ジョブ確認画面での操作待ち

3. **homeモード**: 
   - モード選択用の数値キー入力の監視

4. **その他のモード**: 
   - 各モード固有の入力待ち処理

### 必要性
- UIの応答性を保つため（メイン処理をブロックしない）
- 複数の入力ソース（シリアル通信、HTTP API）を同時に監視するため

---

## 5. 未使用のThreading要素

### 場所
`apis/main/src/main.py`

### 実装箇所
```16:16:apis/main/src/main.py
COUNTER_LOCK = threading.Lock()
```

### 状態
- `threading.Lock()`が定義されているが、実際には使用されていない
- `threading`モジュールのインポートも存在しない
- おそらく将来の拡張用に残されたコード、または未使用のコード

---

## まとめ

### Threadingが採用されている主な理由

1. **非同期処理の実現**
   - HTTPサーバー、シリアル通信、UI入力などのブロッキング処理を非同期で実行
   - メイン処理ループを停止させない

2. **応答性の向上**
   - 複数の処理（印刷制御、HTTPリクエスト、ユーザー入力）を同時に処理可能
   - リアルタイム性が求められる制御システムとしての要件を満たす

3. **モジュール化と保守性**
   - 各機能を独立したスレッドとして分離
   - 各スレッドの開始・停止を個別に制御可能

### スレッドのライフサイクル管理

- **開始**: 各クラスの`__init__`メソッドで`thread.start()`を呼び出し
- **終了**: `__del__`メソッドで`raise_exception()`を呼び出して強制終了
- **安全な終了**: `ctypes`を使用した例外送出による強制終了機能を実装

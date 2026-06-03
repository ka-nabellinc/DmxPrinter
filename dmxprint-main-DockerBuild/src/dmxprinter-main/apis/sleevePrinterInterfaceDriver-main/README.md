# sleevePrinterInterfaceDriver

本ドライバは、ユーザの書いたソフトウェア内で使用するSDKが持つHTTPサーバと通信します。

## ドライバの起動
dockerコンテナを起動。  
添付のcomposeファイルで起動も可。

### 要点
- `privileged: true`は絶対に必要
- 次の環境変数を設定する。設定しなかった場合、括弧内のデフォルト値が適用される。
    - `RECEIVER_HOST` (127.0.0.1) : SDKのホストネーム。ドライバと同じホストで動作させない場合、SDKを使用するホストのアドレスを指定する。
    - `RECEIVER_PORT` (8989) : SDKの待受ポート。
    - `USB_DEV_NAME` (AUTO) : インターフェースデバイスのシリアルポート。`AUTO`にすると自動探索して接続する。



## SDK


### class sleevePrinterInterface.sleevePrinterInterface(port=8989, startFunc=None, stopFunc=None, debug=False)
インターフェースドライバとの通信を受付始める。  
- port: 待受ポート（TCP）  
- startFunc: スタートボタンが押されたとき、および画面操作でスタート操作をしたときに実行する関数  
- stopFunc: ストップボタンが押されたとき、および画面操作でストップ操作をしたときに実行する関数
- debug: デバッグモード（各種パラメータのprint表示の有効/無効設定。Trueにすると標準出力にデバッグ表示が入る）


### job
人間が入力し、確定したジョブ情報が格納された構造体。
内容は、人間がジョブ番号の指定と枚数の指定を行い、印刷開始を指示したタイミングで更新される。
以下のメンバを持つ。
- uid: ユニークID。ジョブ情報が生成される度に変わる。(type: str)
- jobNum: ジョブ番号。印刷機にプリセットしてあるテンプレートの番号。(type: int)
- qty: 印刷部数。(type: int)
- printed: 印刷終了部数。Printing画面で印刷完了割合の表示に使用される。(type: int)
- finished: 印刷完了フラグ。終了時にTrueにする。Trueにすると印刷完了としてホーム画面に戻る。(type: bool)

job.printedとjob.finishedの内容は、更新するとLCDの表示が変わる。  
job.finishedは、印刷開始時とストップ操作時にFalseに自動的に書き換えられる。

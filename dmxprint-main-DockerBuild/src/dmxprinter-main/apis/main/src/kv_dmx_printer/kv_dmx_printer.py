import json
import datetime
import urllib.request
import urllib.error

TIMEOUT = 5

# レジスタマップ
# DM0-DM1  : ① 仕掛け時間 開始時刻       (32bit unix timestamp, .L)
# DM2-DM3  : ① 仕掛け時間 終了 = ② 運転時間 開始時刻 (32bit unix timestamp, .L)
# DM4-DM5  : ② 運転時間 終了時刻          (32bit unix timestamp, .L)
# DM6-DM7  : ③ トラブル時間 開始時刻      (32bit unix timestamp, .L)
# DM8-DM9  : ③ トラブル時間 終了時刻      (32bit unix timestamp, .L)
# DM10     : ④ 不良枚数                   (16bit unsigned, .U)
# DM11     : ⑤ 印刷枚数                   (16bit unsigned, .U)
# DM12     : ⑥ JOB番号                    (16bit unsigned, .U)
# DM13     : ⑦ 印刷予定枚数               (16bit unsigned, .U)


class kvDmxPrinter():
	def __init__(self, host, port=3000, raiseOnError=False):
		self.host = host
		self.port = int(port)
		self.raiseOnError = raiseOnError
		self._baseUrl = "http://{}:{}".format(host, port)

	def _put(self, addr, value):
		url = "{}/value?addr={}".format(self._baseUrl, addr)
		data = json.dumps({"value": str(value)}).encode('utf-8')
		req = urllib.request.Request(url, data=data, method='PUT')
		req.add_header('Content-Type', 'application/json')
		try:
			with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
				return json.loads(res.read().decode('utf-8'))
		except (urllib.error.URLError, TimeoutError) as e:
			print("PUT error [{}]: {}".format(url, e))
			if self.raiseOnError:
				raise
			return None

	def _get(self, addr):
		url = "{}/value?addr={}".format(self._baseUrl, addr)
		try:
			with urllib.request.urlopen(url, timeout=TIMEOUT) as res:
				return json.loads(res.read().decode('utf-8'))
		except (urllib.error.URLError, TimeoutError) as e:
			print("GET error [{}]: {}".format(url, e))
			if self.raiseOnError:
				raise
			return None

	@staticmethod
	def _toTimestamp(dt: datetime.datetime) -> int:
		return int(dt.timestamp())

	def recordSetupStartTime(self, time: datetime.datetime):
		"""① 仕掛け時間 開始の時刻を取得"""
		return self._put('DM0.L', self._toTimestamp(time))

	def recordSetupEndTime(self, time: datetime.datetime):
		"""① 仕掛け時間 終了の時刻を取得 (= ② 運転時間 開始の時刻)

		2点は同一の数として扱ってよいため、DM2 への1回の書き込みで両方を兼ねる。
		"""
		return self._put('DM2.L', self._toTimestamp(time))

	def recordOperationEndTime(self, time: datetime.datetime):
		"""② 運転時間 終了の時刻を取得"""
		return self._put('DM4.L', self._toTimestamp(time))

	def recordTroubleStartTime(self, time: datetime.datetime):
		"""③ トラブル時間 開始の時刻を取得"""
		return self._put('DM6.L', self._toTimestamp(time))

	def recordTroubleEndTime(self, time: datetime.datetime):
		"""③ トラブル時間 終了の時刻を取得"""
		return self._put('DM8.L', self._toTimestamp(time))

	def incrementDefectCount(self):
		"""④ 不良枚数 を 1 カウント分取得 (現在値に +1 して書き戻す)"""
		res = self._get('DM10.U')
		if res is None:
			return None
		current = int(res['value'].lstrip('+'))
		return self._put('DM10.U', current + 1)

	def recordPrintCount(self, count: int):
		"""⑤ 印刷枚数 を △ 枚数取得"""
		return self._put('DM11.U', count)

	def recordJobNumber(self, jobNumber: int):
		"""⑥ JOB番号 取得"""
		return self._put('DM12.U', jobNumber)

	def recordPlannedPrintQty(self, qty: int):
		"""⑦ 印刷予定枚数 取得"""
		return self._put('DM13.U', qty)


if __name__ == '__main__':
	client = kvDmxPrinter('127.0.0.1', 3000)
	now = datetime.datetime.now()

	print(client.recordJobNumber(1))
	print(client.recordPlannedPrintQty(100))
	print(client.recordSetupStartTime(now))
	print(client.recordSetupEndTime(now))
	print(client.recordOperationEndTime(now))
	print(client.recordTroubleStartTime(now))
	print(client.recordTroubleEndTime(now))
	print(client.incrementDefectCount())
	print(client.recordPrintCount(50))

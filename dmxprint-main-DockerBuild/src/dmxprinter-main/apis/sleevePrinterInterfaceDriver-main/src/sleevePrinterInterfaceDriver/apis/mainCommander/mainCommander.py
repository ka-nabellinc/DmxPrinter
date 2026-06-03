import json
import time
import serial
import serial.tools.list_ports

from apis.thread import thread
# from apis.envReader import envReader


class mainCommander():
	def __init__(self, port="AUTO", debug=False):
		self.debug = debug

		if port == "AUTO":
			r = self.searchPort()
			if r == -1:
				raise RuntimeError("Device not found")
			port = r

		self.dev = port

		if debug:
			print("Port:", self.dev)

		self.serialOpen(self.dev)

		self.serialQue = []
		self.buttonQue = []
		self.keyQue = []

		self.receiveThread = thread.thread(target=self.receive)
		self.receiveThread.start()

		self.parseThread = thread.thread(target=self.parse)
		self.parseThread.start()


	def __del__(self):
		self.receiveThread.raise_exception()
		self.parseThread.raise_exception()


	def parse(self):
		while True:
			while len(self.serialQue) != 0:
				if self.serialQue[0] == "start":
					self.buttonQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "stop":
					self.buttonQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "enter":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "back":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "0":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "1":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "2":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "3":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "4":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "5":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "6":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "7":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "8":
					self.keyQue.append(self.serialQue.pop())
				elif self.serialQue[0] == "9":
					self.keyQue.append(self.serialQue.pop())
				else:
					self.serialQue.pop()

			time.sleep(0.0001)


	def serialOpen(self, port):
		self.serial = serial.Serial(
			port = port,
			baudrate = 115200,
			parity = 'N'
		)


	def receive(self):
		self.serialQue = []

		while True:
			data = self.serial.readline()
			data = data.strip()
			data = data.decode('utf-8')
			self.serialQue.append(data)

			if self.debug:
				print("raw data:", data)

		if self.debug:
			print("Serial receive thread closed")


	def send(self, strings):
		data = strings + '\n'
		data = data.encode('utf-8')
		self.serial.write(data)
		time.sleep(0.05)

		if self.debug:
			print("Sent:", strings)


	def checkPort(self, port):
		if self.debug:
			print("Check", port)
		self.port = port

		self.serialOpen(port)
		self.receiveThread = thread.thread(target=self.receive)
		self.receiveThread.start()

		self.send("HELLO printerInterface")
		time.sleep(1)

		self.receiveThread.raise_exception()
		time.sleep(0.1)
		self.serial.close()

		if len(self.serialQue) != 0:
			if self.serialQue.pop() == "HELLO from printerInterface":
				return True
			else:
				return False
		else:
			return False


	def searchPort(self):
		ports = serial.tools.list_ports.comports()

		if len(ports) == 0:
			return -1

		for p in ports:
			r = self.checkPort(p.device)
			if r:
				port = p.device
				if self.debug:
					print("Port determined:", port)
				break

		return port




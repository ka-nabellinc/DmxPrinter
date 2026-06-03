import time

from apis.thread import thread


class selectJob():
	def __init__(self, comm, disp, nextFunc, previousFunc, debug=False):
		self.debug = debug
		self.num = None

		self.comm = comm
		self.disp = disp
		self.nextFunc = nextFunc
		self.previousFunc = previousFunc

		self.receiveThread = thread.thread(target=self.receive)
		self.receiveThread.start()

		self.print()

	def __del__(self):
		if self.debug:
			print(self.name(), "end")
		self.receiveThread.raise_exception()

	def name(self):
		return self.__class__.__name__

	def receive(self):
		while True:
			while len(self.comm.keyQue) != 0:
				com = self.comm.keyQue.pop()
				if self.debug:
					print(com)
				if com == "enter":
					r = self.checkNum()
					if r == 0:
						return
				elif com == "back":
					self.previousFunc(None)
					return
				else:
					self.addNum(com)

			time.sleep(0.0001)

	def print(self):
		lines = ["",""]
		lines[0] = "Enter Job Number"
		self.disp.print(lines)

	def printWarn(self):
		lines = ["",""]
		lines[0] = "Invalid Number"
		self.disp.print(lines)
		time.sleep(2)
		self.print()
		self.num = None


	def addNum(self, num):
		try:
			n = int(num)
		except Exception as e:
			if self.debug:
				print(e)
			return

		if self.num == None:
			self.num = n
		else:
			self.num = self.num * 10 + n

		self.printNum()


	def printNum(self):
		line = str(self.num)
		self.disp.printOneLine(2, line)


	def checkNum(self):
		if self.num == None:
			self.printWarn()
			return 1
		else:
			j = {"job": self.num}
			self.nextFunc(j)
			return 0


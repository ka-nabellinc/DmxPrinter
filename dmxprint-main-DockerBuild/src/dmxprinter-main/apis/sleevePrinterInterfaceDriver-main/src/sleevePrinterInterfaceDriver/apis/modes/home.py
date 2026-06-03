import time

from apis.thread import thread


class home():
	def __init__(self, comm, disp, nextFunc, debug=False):
		self.debug = debug

		self.comm = comm
		self.disp = disp
		self.nextFunc = nextFunc

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
				if com!="enter" and com!="back":
					self.nextFunc(int(com))
					return

			time.sleep(0.0001)

	def print(self):
		lines = ["",""]
		lines[0] = "Select mode"
		lines[1] = "Print:1 ShowIP:2"
		self.disp.print(lines)
		
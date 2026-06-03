import time

from apis.thread import thread


class confirmCancel():
	def __init__(self, comm, disp, nextFunc, previousFunc, jobData, debug=False):
		self.debug = debug
		self.abort = False
		self.jobData = jobData

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
						self.nextFunc(self.jobData)
						return
				elif com == "back":
					self.previousFunc(self.jobData)
					return
			if self.abort:
				return

			time.sleep(0.0001)

	def print(self):
		lines = ["",""]
		lines[0] = "Cance Printing?"
		lines[1] = "Enter to Cancel"
		self.disp.print(lines)
		
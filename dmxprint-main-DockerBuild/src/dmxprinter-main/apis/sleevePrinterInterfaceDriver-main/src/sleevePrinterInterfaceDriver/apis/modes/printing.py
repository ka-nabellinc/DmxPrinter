import os
import time
import json
import requests

from apis.thread import thread
from apis.envReader import envReader


class printing():
	def __init__(self, comm, disp, nextFunc, previousFunc, jobData, debug=False):
		self.debug = debug
		self.jobData = jobData

		self.host = "http://"
		self.host += envReader.read("RECEIVER_HOST")
		self.host += ":" + str(envReader.read("RECEIVER_PORT"))

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

	def getFinished(self):
		url = os.path.join(self.host, "finished")
		try:
			r = requests.get(url)
		except Exception as e:
			print(e)
		else:
			j = json.loads(r.text)
			if j["finished"]:
				self.nextFunc(None)
				return True
		return False

	def getPrinted(self):
		url = os.path.join(self.host, "printed")
		try:
			r = requests.get(url)
		except Exception as e:
			print(e)
		else:
			j = json.loads(r.text)
			self.printPrinted(j["printed"])


	def receive(self):
		sleep = 0.0001
		progressSleep = 0.2
		cycle = progressSleep / sleep

		while True:
			while len(self.comm.keyQue) != 0:
				com = self.comm.keyQue.pop()
				if self.debug:
					print(com)
				if com == "back":
					self.previousFunc(self.jobData)
					return

			if (cycle%(progressSleep/sleep)) == 0:
				r = self.getFinished()
				if r:
					return
				self.getPrinted()

			cycle += 1
			time.sleep(0.0001)


	def print(self):
		lines = ["",""]
		lines[0] = "Printing..."
		self.disp.print(lines)


	def printPrinted(self, printed):
		per = int(printed/self.jobData["qty"]*100)
		line = str(per) + "% Completed"
		self.disp.printOneLine(2, line)
		
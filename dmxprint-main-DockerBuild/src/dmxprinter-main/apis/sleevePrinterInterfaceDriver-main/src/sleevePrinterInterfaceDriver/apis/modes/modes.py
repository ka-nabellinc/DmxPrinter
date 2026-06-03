import os
import time
import uuid
import requests

from apis.thread import thread
from apis.envReader import envReader

from apis.modes import home as home
from apis.modes import selectJob as selectJob
from apis.modes import selectQty as selectQty
from apis.modes import confirm as confirm
from apis.modes import printing as printing
from apis.modes import confirmCancel as confirmCancel
from apis.modes import showIP as showIP


class modes():
	def __init__(self, comm, disp, debug=False):
		self.debug = debug

		self.comm = comm
		self.disp = disp

		self.host = "http://"
		self.host += envReader.read("RECEIVER_HOST")
		self.host += ":" + str(envReader.read("RECEIVER_PORT"))


		self.receiveThread = thread.thread(target=self.receive)
		self.receiveThread.start()

		self.mode = home.home(self.comm, self.disp, self.printORip, debug=self.debug)

		self.job = {}

	def cancelPrinting(self, arg):
		self.getStop()
		self.setHome(None)


	def startPrinting(self, arg):
		self.job["uid"] = str(uuid.uuid4())
		self.postJob(self.job)
		self.getStart()
		self.mode = printing.printing(self.comm, self.disp, self.confirmCancel, self.confirmCancel, arg, debug=self.debug)


	def confirmCancel(self, arg):
		if self.debug:
			print(arg)
		self.mode = confirmCancel.confirmCancel(self.comm, self.disp, self.cancelPrinting, self.printing, arg, debug=self.debug)

	def printing(self, arg):
		if self.debug:
			print(arg)
		self.mode = printing.printing(self.comm, self.disp, self.setHome, self.confirmCancel, arg, debug=self.debug)

	def confirm(self, arg):
		if self.debug:
			print(arg)

		self.job = arg
		self.mode = confirm.confirm(self.comm, self.disp, self.startPrinting, self.setHome, arg, debug=self.debug)

	def getQty(self, arg):
		if self.debug:
			print(arg)
		self.mode = selectQty.selectQty(self.comm, self.disp, self.confirm, self.printORip, arg, debug=self.debug)

	def printORip(self, arg):
		if arg == 1:
			self.mode = selectJob.selectJob(self.comm, self.disp, self.getQty, self.setHome, debug=self.debug)
		elif arg == 2:
			self.mode = showIP.showIP(self.comm, self.disp, self.setHome, debug=self.debug)
	
	def setHome(self, arg):
		self.job = {}
		self.mode = home.home(self.comm, self.disp, self.printORip, debug=self.debug)


	def postJob(self, job):
		url = os.path.join(self.host, "job")
		print(job)

		try:
			r = requests.post(url, json=job)
		except Exception as e:
			print(e)

	def getStart(self):
		url = os.path.join(self.host, "start")
		try:
			r = requests.get(url)
		except Exception as e:
			print(e)

	def getStop(self):
		url = os.path.join(self.host, "stop")
		try:
			r = requests.get(url)
		except Exception as e:
			print(e)


	def start(self):
		if self.mode.name() == "confirm":
			self.mode.__del__()
			self.startPrinting(self.job)

		self.getStart()


	def stop(self):
		if self.mode.name() == "printing":
			self.mode.__del__()
			self.setHome(None)
		elif self.mode.name() == "confirmCancel":
			self.mode.__del__()
			self.setHome(None)

		self.getStop()


	def receive(self):
		while True:
			while len(self.comm.buttonQue) != 0:
				com = self.comm.buttonQue.pop()
				if com == "start":
					self.start()
				elif com == "stop":
					self.stop()
				if self.debug:
					print(com)

			time.sleep(0.0001)


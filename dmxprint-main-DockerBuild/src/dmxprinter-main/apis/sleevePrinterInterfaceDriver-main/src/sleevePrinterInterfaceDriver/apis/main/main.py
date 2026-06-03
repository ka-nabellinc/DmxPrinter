import time

from apis.modes import modes
from apis.thread import thread
from apis.envReader import envReader
from apis.mainCommander import mainCommander
from apis.displayController import displayController


class main():
	def __init__(self):
		self.debug = True

		self.comm = mainCommander.mainCommander(port=envReader.read("USB_DEV_NAME"), debug=self.debug)
		self.disp = displayController.displayController(self.comm, debug=self.debug)
		self.modes = modes.modes(self.comm, self.disp, debug=self.debug)


	def main(self):

		try:
			while 1:
				time.sleep(1)
				# sys.stderr.flush()
				# sys.stdout.flush()
		except KeyboardInterrupt:
			pass
		finally:
			pass



		

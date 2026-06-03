from sleevePrinterInterface import sleevePrinterInterface

import time


class sample():
	def __init__(self):
		self.count = 0
		self.spi = sleevePrinterInterface.sleevePrinterInterface(
			port = 8989, 				# Port settings
			startFunc = self.start,		# function which run when start button pressed
			stopFunc = self.stop,		# function which run when stop button pressed
			debug = True				# debug mode (give False to disable debug print)
		)

	def start(self):
		self.count += 1
		print()

	def stop(self):
		self.count -= 1
		print(self.count)

	def printJob(self):
		print("uid:", self.spi.job.uid)
		print("job:", self.spi.job.jobNum)
		print("qty:", self.spi.job.qty)
		print()

	def getPrinted(self):
		printed = int(input("Printed: "))
		self.spi.job.printed = printed

		if printed >= self.spi.job.qty:
			self.spi.job.finished = True


if __name__ == "__main__":
	spl = sample()

	print("SDK start")

	while True:
		spl.getPrinted()
		spl.printJob()
		# time.sleep(1)




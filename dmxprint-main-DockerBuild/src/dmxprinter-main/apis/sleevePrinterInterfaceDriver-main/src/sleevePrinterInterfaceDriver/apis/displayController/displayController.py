class displayController():
	def __init__(self, comm, debug=False):
		self.debug = debug

		self.comm = comm


	def print(self, lines):
		cmd = "afLP01" + lines[0]
		self.comm.send(cmd)

		cmd = "afLP02" + lines[1]
		self.comm.send(cmd)

	def printOneLine(self, line, content):
		cmd = "afLP0" + str(line) + content
		self.comm.send(cmd)




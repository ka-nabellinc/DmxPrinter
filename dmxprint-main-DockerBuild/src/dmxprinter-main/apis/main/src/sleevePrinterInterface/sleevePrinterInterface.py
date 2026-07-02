import http.server
import urllib
import json
import time
import ctypes
import threading
from functools import partial


class thread(threading.Thread):
    def __init__(self, group=None, target=None, name=None, args=(), kwargs={}):
        threading.Thread.__init__(self, group=group, target=target, name=name)
        self.args = args
        self.kwargs = kwargs
        return
    
    def run(self):
        self._target(*self.args, **self.kwargs)

    def get_id(self):
        if hasattr(self, '_thread_id'):
            return self._thread_id
        for id, thread in threading._active.items():
            if thread is self:
                return id
    
    def raise_exception(self):
        thread_id = self.get_id()
        resu = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(SystemExit))
        if resu > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), 0)
            print('Failure in raising exception')


class ServerHandler(http.server.SimpleHTTPRequestHandler):
	def __init__(self, *args, directory=None, startFunc=None, stopFunc=None, jobLoadFunc=None, job=None, debug=False, **kwargs):
		self.__debug = debug
		self.__startFunc = startFunc
		self.__stopFunc = stopFunc
		self.__jobLoadFunc = jobLoadFunc
		self.__job = job

		super().__init__(*args, directory=directory, **kwargs)

	def log_message(self, format: str, *args) -> None:
		if self.__debug:
			print(args)
		pass


	def response(self, code, message):
		j = json.dumps(message)
		self.send_response(code)
		self.send_header('Content-type', 'application/json')
		self.end_headers()
		self.wfile.write(j.encode())


	def do_GET(self):
		parsed = urllib.parse.urlparse(self.path)

		if parsed.path[1:] == "start":
			self.startSW()
			self.response(200, {"success": "success", "command": "start"})
		elif parsed.path[1:] == "stop":
			self.stopSW()
			self.response(200, {"success": "success", "command": "stop"})
		elif parsed.path[1:] == "printed":
			self.response(200, {"success": "success", "command": "stop", "printed": self.__job.printed})			
		elif parsed.path[1:] == "finished":
			self.response(200, {"success": "success", "command": "stop", "finished": self.__job.finished})			
		else:
			self.response(404, {"success": "failed", "message": "comand not found"})

	def do_POST(self):
		parsed = urllib.parse.urlparse(self.path)
		if parsed.path[1:] == "job":
			data = self.rfile.read(int(self.headers['Content-Length']))
			self.jobLoad(data)
			self.response(200, {"success": "success", "command": "job"})
		else:
			self.response(404, {"success": "failed", "message": "comand not found"})

	def jobLoad(self, data):
		j = json.loads(data.decode())
		self.__job.jobNum = j["job"]
		self.__job.qty = j["qty"]
		self.__job.uid = j["uid"]
		if self.__jobLoadFunc is not None:
			self.__jobLoadFunc()


	def startSW(self):
		self.__job.finished = False
		if self.__startFunc != None:
			self.__startFunc()

	def stopSW(self):
		self.__job.reset()
		if self.__stopFunc != None:
			self.__stopFunc()


class job():
	def __init__(self):
		self.jobNum = 0
		self.qty = 0
		self.uid = ""
		self.printed = 0
		self.finished = False

	def reset(self):
		self.jobNum = 0
		self.qty = 0
		self.uid = ""
		self.printed = 0


class sleevePrinterInterface():
	def __init__(self, port=8989, startFunc=None, stopFunc=None, jobLoadFunc=None, debug=False):
		self.__port = port
		self.__debug = debug
		self.__startFunc = startFunc
		self.__stopFunc = stopFunc
		self.__jobLoadFunc = jobLoadFunc

		self.job = job()

		self.__serverTH = thread(target=self.httpServer)
		self.__serverTH.start()


	def __del__(self):
		self.__serverTH.raise_exception()


	def httpServer(self):
		if self.__debug:
			print("Server Start")

		handler = partial(
			ServerHandler,
			startFunc=self.__startFunc,
			stopFunc=self.__stopFunc,
			jobLoadFunc=self.__jobLoadFunc,
			job=self.job,
			debug=self.__debug
		)

		with http.server.HTTPServer(('', self.__port), handler) as server:
			server.serve_forever()


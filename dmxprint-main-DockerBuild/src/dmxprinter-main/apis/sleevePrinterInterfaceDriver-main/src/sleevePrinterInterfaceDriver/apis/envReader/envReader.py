import os

__default = {
	"RECEIVER_HOST": "127.0.0.1",
	"RECEIVER_PORT": 8989,
	"REDIS_DB": 1,
	"DEFAULT_FEATURES": False,
	"USB_DEV_NAME": "AUTO",
	"FORMAT": "bmp",
	"ILLUMINANCE": 0.4986,
	"LIGHT_DELAY": 0.5
}

def read(name):
	try:
		env = os.environ[name]
	except Exception as e:
		try:
			env = __default[name]
		except Exception as e:
			raise

	return env

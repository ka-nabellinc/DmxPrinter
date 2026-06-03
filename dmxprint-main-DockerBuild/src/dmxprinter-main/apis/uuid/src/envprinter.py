import os

__default = {
    "MAIN_HOST": "main",
    "MAIN_PORT": 5001,
    "PRINT_HOST":"dmxprinter",
    "PRINT_PORT":5002,
    "PRINTER_HOST":"192.168.0.100",
    "PRINTER_PORT":50002,
    "CMOTER_PORT":17,
    "UUID_HOST":"uuid",
    "UUID_PORT":5003,
    "DELAY":0.1,
    "BY_ENCODE":"utf-8",
    "CSV_DIR":"/home/Python/src/uuid/output"
}



def read(name):
    """
    環境変数を読み込みます。
    """
    try:
        env = os.environ[name]
    except Exception as e:
        if name in __default:
            env = __default[name]
        else:
            raise
        
    return env
    


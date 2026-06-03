from gpiozero import Button
import time
import os
import configparser
import sys
import threading
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)

from command import Command
from sleevePrinterInterface import sleevePrinterInterface

config_ini = configparser.ConfigParser()
path = os.path.join(os.path.dirname(__file__), 'config.ini')
config_ini.read(path,encoding='utf-8')

# GPIO input settings (overrideable by environment variables)
# - INPUT_PIN: BCM pin number for marker sensor button input
# - PULL_UP_RESISTOR: 1/0, true/false (gpiozero Button.pull_up)
INPUT_PIN = int(os.getenv("INPUT_PIN", "17")) #スリーブ検知
INPUT_PIN2 = int(os.getenv("INPUT_PIN2", "18")) #NG検知
PULL_UP_RESISTOR = os.getenv("PULL_UP_RESISTOR", "1").strip().lower() in ("1", "true", "yes", "y", "on")

COUNTER_LOCK = threading.Lock()
FLAGMENT=0

class main:
    global FLAGMENT
    def __init__(self):
        """
        config.iniより環境変数を設定
        """
        self.print_host = config_ini['PRINTER']['PRINTER_HOST']
        self.print_port = int(config_ini.get('PRINTER','PRINTER_PORT'))
        self.printRank = config_ini['PRINTER']['PRINTER_RANK']

        self.uuid_host = config_ini['UUID']['UUID_HOST']
        self.uuid_port = int(config_ini.get('UUID','UUID_PORT'))

        self.conveyor_host = config_ini['CONVEYOR']['CONVEYOR_HOST']
        self.conveyor_port = int(config_ini.get('CONVEYOR','CONVEYOR_PORT'))

        self.by_encode = config_ini['DEBUG']['DEBUG_BY_ENCODE']
        self.delay = float(config_ini.get('DEBUG','DEBUG_DELAY'))
        self.timeout = float(config_ini.get('DEBUG','DEBUG_TIMEOUT'))

        self.Commander = Command()

        self.count = 0
        self.printed = 0
        self.flag = 0
        self.lastid = "none"
        self.starter = 0
        self.initstate = 0

        self.spi = sleevePrinterInterface.sleevePrinterInterface(
			port = 8989, 				# Port settings
			startFunc = self.start,		# function which run when start button pressed
			stopFunc = self.stop,		# function which run when stop button pressed
			#debug = True				# debug mode (give False to disable debug print)
		)  

        # ---- interrupt-like triggers / internal state ----
        # Thread-safety: gpiozero callbacks may run on background threads.
        self._event_lock = threading.Lock()

        # Process state flags
        self.proc1_requested = False
        self.proc1_running = False
        self.proc1_done = False

        self.proc2_requested = False
        self.proc2_running = False
        self.proc2_done = False

        self.proc3_requested = False
        self.proc3_running = False

        # Shared context across processes
        self.current_uuid = None
        self.current_job_id = None

        # GPIO buttons (keep instances; don't recreate in run-loop)
        self.marker_button = Button(INPUT_PIN, pull_up=PULL_UP_RESISTOR)
        self.ng_button = Button(INPUT_PIN2, pull_up=PULL_UP_RESISTOR)

        self.marker_button.when_pressed = self.on_marker_pressed
        self.ng_button.when_pressed = self.on_ng_pressed
        

    def on_marker_pressed(self, *args):
        with self._event_lock:
            self.proc1_requested = True

    def on_ng_pressed(self, *args):
        with self._event_lock:
            self.proc3_requested = True

    def start(self):
        self.starter = 1
        print("DMX Printer started.")
        

    def stop(self):
        self.stoping()
        self.count = 0
        self.printed = 0
        self.flag = 0
        self.lastid = "none"
        print(self.count)

    def getPrinted(self):
        printed = int(input("Printed: "))
        self.spi.job.printed = printed

        if printed >= self.spi.job.qty:
            self.spi.job.finished = True


    def _reset_process_cycle_state(self):
        with self._event_lock:
            self.proc1_requested = False
            self.proc1_running = False
            self.proc1_done = False

            self.proc2_requested = False
            self.proc2_running = False
            self.proc2_done = False

            self.current_uuid = None
            self.current_job_id = None

    def process1_uuid_and_block_write(self, job_id: str):
        with self._event_lock:
            if self.proc1_running or self.proc1_done:
                return
            self.proc1_running = True

        try:
            self.current_job_id = str(job_id)
            uuid = self.Commander.uuid_gen('GEN')
            self.current_uuid = uuid
            print(f"UUID:{uuid}")

            command = "BLOCK_WRITE"
            self.Commander.printer_send_command(command, self.current_job_id, self.current_uuid)
            responce = self.Commander.listen_response()
            print(f"responce:{responce}")

            if responce and "WX,OK" in responce:
                with self._event_lock:
                    self.flag = 1
                    self.proc1_done = True
                    self.proc2_requested = True
                    self.proc1_requested = False
            else:
                print("BLOCK_WRITE not confirmed (missing WX,OK).")
        except Exception as e:
            print(f"Process1 error: {e}")
        finally:
            with self._event_lock:
                self.proc1_running = False

    def process2_start_printing(self):
        with self._event_lock:
            if (not self.proc2_requested) or self.proc2_running or self.proc2_done:
                return
            self.proc2_running = True

        try:
            if not self.current_job_id or not self.current_uuid:
                print("Process2 skipped: missing job_id/uuid.")
                return

            print("印刷開始")
            command = "PRINTING"
            self.Commander.printer_send_command(command, self.current_job_id, self.current_uuid)
            responce = self.Commander.listen_response()
            print(f"responce:{responce}")

            if responce and "WX,OK" in responce:
                with self._event_lock:
                    self.proc2_done = True
                    self.proc2_requested = False
                # printed は PRINTING が成功したときだけ増やす
                self.printed = self.printed + 1
                self.spi.job.printed = self.printed
            else:
                print("PRINTING not confirmed (missing WX,OK).")
        except Exception as e:
            print(f"Process2 error: {e}")
        finally:
            with self._event_lock:
                self.proc2_running = False

    def process3_inspection_ng(self):
        with self._event_lock:
            if self.proc3_running:
                return
            self.proc3_running = True
            self.proc3_requested = False

        try:
            time.sleep(0.2)
            self.Commander.stop_conveyor()
            print("Inspection NG: Conveyor stopped. Job paused.")
            self.starter = 0
        except Exception as e:
            print(f"Process3 error: {e}")
        finally:
            with self._event_lock:
                self.proc3_running = False


    def run(self):
        """
        メインの実行処理
        """
        job_id = str(self.spi.job.jobNum)
        print(f"Job ID: {job_id}")

        with self._event_lock:
            proc3_req = self.proc3_requested
        if proc3_req:
            self.process3_inspection_ng()
            return

        # # 印刷条件取得（現在は未使用のためコメントアウト）
        # # ユーザ入力から印刷状態や枚数を取得したい場合に利用する想定
        # user_status = self.Commander.user_status(self.spi.job.qty,self.spi.job.jobNum)
        # self.count = int(user_status[1])
        # print(f"User status selected: {user_status}") 
        if self.initstate == 0:
            # 初回のみ：印刷機状態確認〜JOB設定〜プリンタ/コンベア起動
            printer_status = self.Commander.printer_status()
            listen = self.Commander.listen_response()
            print(f"Printer status: {printer_status.decode('utf-8', errors='ignore')}")
            print(listen)
            
            # 印刷JOB変更
            command = "JOB_ID"
            self.Commander.printer_send_command(command,job_id,'none')
            print(f"Job ID changed to {job_id}")

            # プリンタを起動
            command = "START"
            self.Commander.printer_send_command(command,job_id,'none')

            # コンベアを起動
            self.Commander.start_conveyor()
            print(f"DMX Printer job started. Conveyor started.:{listen}")
            self.initstate = 1


        uuid = "none"
        
        
        # UI側からのジョブ開始フラグが立っていればコンベアを起動し直す
        if self.spi.job.start == True:
            self.Commander.start_conveyor()

        # 印刷開始処理：印刷枚数分ループ
        
        try: 
            print("-----------STATUS received------------")
            command = "STATUS"#"COUNT"
            # 印刷機の現在ステータスを取得
            self.Commander.printer_send_command(command,job_id,uuid)
            self.status_responce = self.Commander.listen_response()
            self.status_responce = self.status_responce.split(',')[2]#[3]
            #print(f"Bresponce:{self.lastid}")

            # ステータスが取得できなかった場合は何もせず次ループへ
            if self.status_responce == 'none':
                print("STATUS not received. Retrying...")
                return
            elif self.status_responce == '0\r':
                # 状態0: 印刷準備OK（印刷可能状態）
                self.current_job_id = str(job_id)

                # 割り込み（ボタン）で立ったフラグを取得
                with self._event_lock:
                    proc1_req = self.proc1_requested
                    proc1_done = self.proc1_done
                    proc2_req = self.proc2_requested

                # このループで処理1/2のどちらかを実行したかどうか
                did_work = False
                if self.flag == 0 and proc1_req and (not proc1_done):
                    self.process1_uuid_and_block_write(job_id)
                    did_work = True

                # 処理2（印刷開始）は処理1の終了がトリガ
                if proc2_req:
                    did_work = True
                    self.process2_start_printing()

                # 何もしていない場合でも printed は変えない（printed は process2 側で管理）
                
            elif self.status_responce == '1\r':
                pass
                
            elif self.status_responce == '2\r':
                self.flag = 0
                self._reset_process_cycle_state()
                self.spi.job.printed = self.printed
                

            
               
            
            time.sleep(0.01)#self.delay)
            
        except KeyboardInterrupt:
            print("DMX Printer interrupted by user.")
            exit()
        except Exception:
            pass
        
    
        # print("-----------JOB END------------")
        # #　UUID停止
        # self.Commander.uuid_gen('STOP')
        # print(f"DMX Printer job {count} completed. UUID generation stopped.")
        
        # #　プリンタを停止
        # command = "STOP"
        # self.Commander.printer_send_command(command,job_id,'none')
       
        # # コンベアを停止
        # self.Commander.stop_conveyor()
        # print("DMX Printer job completed. Conveyor stopped.")
        

    def stoping(self):
         #　UUID停止
        self.Commander.uuid_gen('STOP')
        print(f"UUID generation stopped.")
        
        #　プリンタを停止
        command = "STOP"
        self.Commander.printer_send_command(command,self.spi.job.jobNum,'none')
       
        # コンベアを停止
        self.Commander.stop_conveyor()
        print("Conveyor stopped.")
        self.starter = 0
        self.initstate = 0


if __name__ == "__main__":
    main_instance = main()

    while True:
        try:
            if main_instance.spi.job.finished == True:
                #print("Job finished.")
                main_instance.initstate = 0
                #break

            # Process3 should be handled even when starter == 0 (pause state)
            if main_instance.proc3_requested == True:
                main_instance.process3_inspection_ng()

            if main_instance.starter == 1:
                main_instance.run()
            
            # qty に達したら終了（printed は PRINTING 成功時のみ増える前提）
            if main_instance.spi.job.qty > 0 and main_instance.printed >= main_instance.spi.job.qty:
                time.sleep(2)
                main_instance.stoping()
                main_instance.spi.job.finished = True

        except KeyboardInterrupt:
            print(f"EXIT")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            break


    print("DMX Printer successfully.")
   
   
import os
import sys
import time
import datetime
import threading
import configparser
import subprocess
import queue
import requests
import streamlink
import logging
from concurrent.futures import ThreadPoolExecutor

if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

# 常量定义
CONFIG_PATH = os.path.join(sys.path[0], 'config.conf')
LOG_FILE = 'log.log'

# 初始化日志
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# 全局变量
settings = {}
recording = []
threads = []
processingQueue = queue.Queue()

def cls():
    os.system('cls' if os.name == 'nt' else 'clear')

def read_config():
    global settings
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    settings = {
        'save_directory': config.get('paths', 'save_directory'),
        'wishlist': config.get('paths', 'wishlist'),
        'interval': config.getint('settings', 'checkInterval'),
        'postProcessingCommand': config.get('settings', 'postProcessingCommand')
    }

    post_processing_threads = config.get('settings', 'postProcessingThreads', fallback='1')
    try:
        settings['postProcessingThreads'] = int(post_processing_threads)
    except ValueError:
        settings['postProcessingThreads'] = 1

    os.makedirs(settings['save_directory'], exist_ok=True)

def log_exception(e):
    logging.exception(f'Exception occurred: {e}')

def post_process():
    while True:
        try:
            parameters = processingQueue.get()
            model = parameters['model']
            path = parameters['path']
            filename = os.path.split(path)[-1]
            directory = os.path.dirname(path)
            file = os.path.splitext(filename)[0]
            subprocess.call(settings['postProcessingCommand'].split() + [path, filename, directory, model, file, 'cam4'])
        except Exception as e:
            log_exception(e)

class ModelRecorder(threading.Thread):
    def __init__(self, model):
        super().__init__()
        self.model = model
        self._stopevent = threading.Event()
        self.file = None
        self.online = None

    def run(self):
        global recording, threads
        try:
            is_online = self.is_online()
            if not is_online:
                self.online = False
                return

            self.online = True
            self.file = os.path.join(settings['save_directory'], self.model,
                                     f'{datetime.datetime.now().strftime("%Y.%m.%d_%H.%M.%S")}_{self.model}.mp4')
            session = streamlink.Streamlink()
            streams = session.streams(f'hlsvariant://{is_online}')
            stream = streams['best']
            fd = stream.open()

            if not is_model_in_list(self.model, recording):
                os.makedirs(os.path.join(settings['save_directory'], self.model), exist_ok=True)
                with open(self.file, 'wb') as f:
                    with threading.Lock():
                        recording.append(self)
                        threads = [t for t in threads if t.model != self.model]

                    while not (self._stopevent.is_set() or os.fstat(f.fileno()).st_nlink == 0):
                        data = fd.read(1024)
                        f.write(data)
                    if settings['postProcessingCommand']:
                        processingQueue.put({'model': self.model, 'path': self.file})

        except Exception as e:
            log_exception(e)
            self.stop()
        finally:
            self.cleanup()

    def is_online(self):
        try:
            resp = requests.get(f'https://stripchat.com/api/front/v2/models/username/{self.model}/cam').json()
            if 'cam' in resp and {'isCamAvailable', 'streamName', 'viewServers'} <= resp['cam'].keys():
                if 'flashphoner-hls' in resp['cam']['viewServers']:
                    return f'https://b-{resp["cam"]["viewServers"]["flashphoner-hls"]}.doppiocdn.com/hls/{resp["cam"]["streamName"]}/{resp["cam"]["streamName"]}.m3u8'
        except Exception as e:
            log_exception(e)
        return False

    def stop(self):
        self._stopevent.set()

    def cleanup(self):
        global recording  # 声明 recording 作为全局变量
        self.online = False
        with threading.Lock():
            recording = [r for r in recording if r.model != self.model]
        try:
            if os.path.isfile(self.file) and os.path.getsize(self.file) <= 1024:
                os.remove(self.file)
        except Exception as e:
            log_exception(e)

class Cleaner(threading.Thread):
    def run(self):
        global threads, recording
        while True:
            with threading.Lock():
                threads = [t for t in threads if t.is_alive() or t.online]
            time.sleep(10)

class ModelAdder(threading.Thread):
    def run(self):
        global threads, recording
        while True:
            try:
                with open(settings['wishlist'], 'r') as f:
                    models = {line.strip().lower() for line in f if line.strip()}
                with threading.Lock():
                    new_models = models - {t.model for t in threads} - {r.model for r in recording}
                    for model in new_models:
                        thread = ModelRecorder(model)
                        thread.start()
                        threads.append(thread)

                    for r in recording:
                        if r.model not in models:
                            r.stop()
            except Exception as e:
                log_exception(e)
            time.sleep(settings['interval'])

def is_model_in_list(model, lst):
    return any(item.model == model for item in lst)

def main():
    read_config()
    if settings['postProcessingCommand']:
        with ThreadPoolExecutor(max_workers=settings['postProcessingThreads']) as executor:
            for _ in range(settings['postProcessingThreads']):
                executor.submit(post_process)
    Cleaner().start()
    ModelAdder().start()
    while True:
        try:
            cls()
            print_status()
            time.sleep(settings['interval'])
        except KeyboardInterrupt:
            break

def print_status():
    print(f'{len(threads):02d} alive Threads (1 Thread per non-recording model)')
    print(f'Online Threads (models): {len(recording):02d}')
    print('The following models are being recorded:')
    for r in recording:
        print(f'  Model: {r.model}  -->  File: {os.path.basename(r.file)}')
    print(f'Next check in {settings["interval"]} seconds')

if __name__ == '__main__':
    main()

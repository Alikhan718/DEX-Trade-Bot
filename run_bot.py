import subprocess
import time
import sys
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class BotRestartHandler(FileSystemEventHandler):
    def __init__(self, process_manager):
        self.process_manager = process_manager
        self.last_restart = 0
        self.restart_cooldown = 1  # Минимальный интервал между перезапусками в секундах

    def on_modified(self, event):
        if event.is_directory:
            return
        
        # Проверяем только Python файлы
        if not event.src_path.endswith('.py'):
            return
            
        # Игнорируем файлы кэша и временные файлы
        if '__pycache__' in event.src_path or '.pyc' in event.src_path:
            return
            
        current_time = time.time()
        if current_time - self.last_restart < self.restart_cooldown:
            return
            
        self.last_restart = current_time
        logger.info(f"Detected change in {event.src_path}")
        self.process_manager.restart_bot()

class BotProcessManager:
    def __init__(self):
        self.process = None
        self.should_restart = True

    def start_bot(self):
        try:
            if self.process:
                self.stop_bot()
                
            logger.info("Starting bot...")
            self.process = subprocess.Popen([sys.executable, "bot.py"])
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")

    def stop_bot(self):
        if self.process:
            logger.info("Stopping bot...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def restart_bot(self):
        logger.info("Restarting bot...")
        self.start_bot()

def run_bot_with_reload():
    process_manager = BotProcessManager()
    event_handler = BotRestartHandler(process_manager)
    
    # Создаем наблюдателя за файловой системой
    observer = Observer()
    # Следим за текущей директорией и src директорией
    paths_to_watch = ['.', 'src']
    
    for path in paths_to_watch:
        if os.path.exists(path):
            observer.schedule(event_handler, path, recursive=True)
            logger.info(f"Watching directory: {path}")
    
    # Запускаем наблюдателя
    observer.start()
    
    try:
        # Запускаем бота первый раз
        process_manager.start_bot()
        
        # Держим программу запущенной
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping...")
        process_manager.stop_bot()
        observer.stop()
        
    observer.join()

if __name__ == "__main__":
    run_bot_with_reload() 
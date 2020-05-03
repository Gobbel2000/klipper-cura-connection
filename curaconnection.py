#!/usr/bin/env python2
"""
Handles the discovery and the server for the  connection with Cura.

This module does not fully run in a seperate thread, but the server
does,  which is doing most of the work  outside of initializing and
shutting down,  which is handled in the CuraConnectionModule class.
"""

import logging
import logging.handlers
import os
import platform
import socket

from contentmanager import ContentManager
from custom_exceptions import QueuesDesynchronizedError
import server
from zeroconfhandler import ZeroConfHandler


class CuraConnectionModule(object):

    def __init__(self, config):
        self.testing = config is None

        # Global variables
        self.VERSION = "5.2.11" # We need to disguise as Cura Connect for now
        self.ADDRESS = self.get_ip()
        self.NAME = platform.node()
        self.SDCARD_PATH = os.path.expanduser("~/sdcard")
        self.MATERIAL_PATH = os.path.expanduser("~/materials")
        self.PATH = os.path.dirname(os.path.realpath(__file__))
        self.LOGFILE = os.path.join(self.PATH, "logs/server.log")

        self.configure_logging()
        self.klippy_logger.info("Cura Connection Module initializing...")

        self.content_manager = ContentManager(self)
        self.zeroconf_handler = ZeroConfHandler(self)
        self.server = server.get_server(self)

        if self.testing:
            import site
            p = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
            site.addsitedir(p)
            import filament_manager
            self.filament_manager = filament_manager.load_config(None)
            return
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.printer.register_event_handler(
                "klippy:connect", self.handle_connect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.printer.register_event_handler("klippy:disconnect", self.stop)
        self.printer.register_event_handler("klippy:shutdown", self.stop)
        self.printer.register_event_handler("klippy:exception", self.stop)

    def configure_logging(self):
        """Add log handler based on testing"""
        formatter = logging.Formatter(
                fmt="%(levelname)s: \t[%(asctime)s] %(message)s")
        if self.testing:
            # Log to console in testing mode
            logging.basicConfig(level=logging.DEBUG)
            handler = logging.StreamHandler()
        else:
            with open(self.LOGFILE, "a") as fp:
                fp.write("\n=== RESTART ===\n\n")
            handler = logging.handlers.RotatingFileHandler(
                    filename=self.LOGFILE,
                    maxBytes=4194304, # max 4 MiB per file
                    backupCount=3, # up to 4 files total
                    delay=True, # Open file only once needed
                )
        handler.setFormatter(formatter)
        self.klippy_logger = logging.getLogger()
        server_logger = logging.getLogger("root.server")
        server_logger.propagate = False # Avoid server logs in klippy logs
        server_logger.addHandler(handler)

    def handle_connect(self):
        self.filament_manager = self.printer.lookup_object(
                "filament_manager", None)
        self.sdcard = self.printer.lookup_object("virtual_sdcard", None)

    def handle_ready(self):
        """Start the server only once Klipper is all up and running"""
        self.start()

    def start(self):
        """Start the zeroconf service and the server in a seperate thread"""
        self.content_manager.start()
        self.zeroconf_handler.start() # Non-blocking
        self.klippy_logger.debug("Cura Connection Zeroconf service started")
        self.server.start() # Starts server thread
        self.klippy_logger.debug("Cura Connection Server started")

    def stop(self, *args):
        """This might take a little while, be patient"""
        self.klippy_logger.debug("Cura Connection shutting down server...")
        self.zeroconf_handler.stop()
        self.server.shutdown()
        self.server.join()
        self.klippy_logger.debug("Cura Connection Server shut down")


    def send_print(self, path):
        """Start a print in klipper"""
        if self.testing:
            self.klippy_logger.info("Start printing {}".format(path))
            self.content_manager.add_test_print(path)
            return
        self.reactor.register_async_callback(
                lambda e: self.sdcard.add_printjob(path))

    def resume_print(self, filename):
        self._verify_queue(0, filename)
        self.reactor.register_async_callback(self.sdcard.resume_printjob)

    def pause_print(self, filename):
        self._verify_queue(0, filename)
        self.reactor.register_async_callback(self.sdcard.pause_printjob)

    def stop_print(self, filename):
        self._verify_queue(0, filename)
        self.reactor.register_async_callback(self.sdcard.stop_printjob)

    def send_queue(self, queue):
        self.sdcard.clear_queue()
        for q in queue[1:]:
            self.reactor.register_async_callback(
                    lambda e: self.sdcard.add_printjob(*q))

    def queue_delete(self, index, filename):
        """
        Delete the print job from the queue.
        """
        self._verify_queue(index, filename)
        queue = self.sdcard.jobs
        queue.pop(index)
        self.send_queue(queue)

    def queue_move(self, old_index, new_index, filename):
        self._verify_queue(old_index, filename)
        queue = self.sdcard.jobs
        if not (0 < new_index < len(queue)):
            raise IndexError(
                "Can't move print job to index {}".format(new_index))
        to_move = queue.pop(old_index)
        queue.insert(new_index, to_move)
        self.send_queue(queue)

    def get_thumbnail_path(self, index, filename):
        """Return the thumbnail path for the specified printjob"""
        self._verify_queue(index, filename)
        return (self.sdcard.jobs[index].thumbnail_path or
                os.path.join(self.PATH, "tux.png"))

    def _verify_queue(self, index, filename):
        """
        Raise QueuesDesynchronizedError if filename is not at index in queue.
        The index is checked as well as the filename in order to detect
        changes in the queue that have not yet been updated in the
        content manager.
        """
        queue = self.sdcard.jobs
        try:
            assert(os.path.basename(queue[index].path) == filename)
        except (IndexError, AssertionError):
            raise QueuesDesynchronizedError()

    @staticmethod
    def get_ip():
        """https://stackoverflow.com/a/28950776"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP


def load_config(config):
    """Entry point, called by Klippy"""
    module = CuraConnectionModule(config)
    return module

if __name__ == "__main__":
    import time
    module = CuraConnectionModule(None)
    module.start()
    while True:
        try:
            time.sleep(0.1)
        except KeyboardInterrupt:
            module.stop()
            break

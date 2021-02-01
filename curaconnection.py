#!/usr/bin/env python3
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
import time

from .contentmanager import ContentManager
from .custom_exceptions import QueuesDesynchronizedError
from . import server
from .zeroconfhandler import ZeroConfHandler


class CuraConnectionModule:

    # How many seconds after the last request to consider disconnected
    # 4.2 allows missing just one update cycle (every 2sec)
    CONNECTION_TIMEOUT = 4.2

    def __init__(self, config):
        self.testing = config is None

        # Global variables
        self.VERSION = "5.2.11" # We need to disguise as Cura Connect for now
        self.NAME = platform.node()
        self.SDCARD_PATH = os.path.expanduser("~/sdcard")
        self.MATERIAL_PATH = os.path.expanduser("~/materials")
        self.PATH = os.path.dirname(os.path.realpath(__file__))
        self.LOGFILE = os.path.join(self.PATH, "logs/server.log")
        self.ADDRESS = None

        self.content_manager = self.zeroconf_handler = self.server = None

        self.configure_logging()
        self.klippy_logger.info("Cura Connection Module initializing...")

        if self.testing:
            import site
            site.addsitedir(os.path.dirname(self.PATH))
            import filament_manager
            self.filament_manager = filament_manager.load_config(None)
            return
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self.stop)
        self.printer.register_event_handler("klippy:disconnect", self.stop)

    def configure_logging(self):
        """Add log handler based on testing"""
        formatter = logging.Formatter(
                fmt="%(levelname)s: \t[%(asctime)s] %(message)s")
        if self.testing:
            # Log to console in testing mode
            logging.basicConfig(level=logging.DEBUG)
            handler = logging.StreamHandler()
        else:
            now = time.strftime(logging.Formatter.default_time_format)
            with open(self.LOGFILE, "a") as fp:
                fp.write(f"\n=== RESTART {now} ===\n\n")
            handler = logging.handlers.RotatingFileHandler(
                    filename=self.LOGFILE,
                    maxBytes=4194304, # max 4 MiB per file
                    backupCount=3, # up to 4 files total
                    delay=True, # Open file only once needed
                )
        handler.setFormatter(formatter)
        self.klippy_logger = logging.getLogger()
        self.server_logger = logging.getLogger("root.server")
        self.server_logger.propagate = False # Avoid server logs in klippy logs
        self.server_logger.addHandler(handler)

    def handle_connect(self):
        self.filament_manager = self.printer.lookup_object(
                "filament_manager", None)
        self.sdcard = self.printer.lookup_object("virtual_sdcard", None)
        self.print_stats = self.printer.lookup_object("print_stats", None)
        self.metadata = self.printer.lookup_object("gcode_metadata", None)

    def handle_ready(self):
        """
        Now it's safe to start the server once there is a network connection
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.wait_for_network()

    def wait_for_network(self, eventtime=0):
        """
        This function executes every 2 seconds until a network
        connection is established.  At that point the IPv4-Address is
        saved and the server started.
        """
        try:
            self.sock.connect(("10.255.255.255", 1))
        except OSError:
            self.reactor.register_callback(self.wait_for_network,
                                           self.reactor.monotonic() + 2)
        else:
            self.ADDRESS = self.sock.getsockname()[0]
            self.sock.close()
            self.start()

    def start(self):
        """Start the zeroconf service, and the server in a seperate thread"""
        self.content_manager = ContentManager(self)
        self.zeroconf_handler = ZeroConfHandler(self)
        self.server = server.get_server(self)

        self.content_manager.start()
        self.zeroconf_handler.start() # Non-blocking
        self.klippy_logger.debug("Cura Connection Zeroconf service started")
        self.server.start() # Starts server thread
        self.klippy_logger.debug("Cura Connection Server started")

    def stop(self, *args):
        """
        This might take a little while, be patient
        can be called before start() e.g. when klipper initialization fails
        """
        if self.server is None:
            # stop() is called before start()
            return
        self.klippy_logger.debug("Cura Connection shutting down server...")
        self.zeroconf_handler.stop()
        self.klippy_logger.debug("Cura Connection Zeroconf shut down")
        if self.server.is_alive():
            self.server.shutdown()
            self.server.join()
            self.klippy_logger.debug("Cura Connection Server shut down")

    def is_connected(self):
        """
        Return true if there currently is an active connection.
        Also see CONNECTION_TIMEOUT
        """
        return (self.server is not None and
                time.time() - self.server.last_request < self.CONNECTION_TIMEOUT)

    def send_print(self, path):
        """Start a print in klipper"""
        if self.testing:
            self.klippy_logger.info("Start printing %s", path)
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
        if not 0 < new_index < len(queue):
            raise IndexError(
                "Can't move print job to index {}".format(new_index))
        to_move = queue.pop(old_index)
        queue.insert(new_index, to_move)
        self.send_queue(queue)

    def get_thumbnail_path(self, index, filename):
        """Return the thumbnail path for the specified printjob"""
        self._verify_queue(index, filename)
        return (self.sdcard.jobs[index].thumbnail_path or
                os.path.join(self.PATH, "default.png"))

    def _verify_queue(self, index, filename):
        """
        Raise QueuesDesynchronizedError if filename is not at index in queue.
        The index is checked as well as the filename in order to detect
        changes in the queue that have not yet been updated in the
        content manager.
        """
        queue = self.sdcard.jobs
        try:
            assert os.path.basename(queue[index].path) == filename
        except (IndexError, AssertionError):
            raise QueuesDesynchronizedError()


def load_config(config):
    """Entry point, called by Klippy"""
    module = CuraConnectionModule(config)
    return module

import email
#PYTHON3: from http import HTTPStatus
#PYTHON3: import http.server as srv
import httplib as HTTPStatus
import BaseHTTPServer as srv
import json
import logging
import os.path
import re

PRINTER_API = "/api/v1/"
CLUSTER_API = "/cluster-api/v1/"

logger = logging.getLogger("root.server")


class Handler(srv.BaseHTTPRequestHandler):

    def __init__(self, request, client_address, server):
        """This is the worst thing ever but it somehow works"""
        self.module = server.module
        self.content_manager = self.module.content_manager
        srv.BaseHTTPRequestHandler.__init__(
                self, request, client_address, server)

    def do_GET(self):
        """
        Implement a case-specific response, limited to the requests
        that we can expect from Cura.  For a summary of those see
        README.md
        """
        if self.path == PRINTER_API + "system":
            content = self.content_manager.get_system()
        elif self.path == CLUSTER_API + "printers":
            content = self.content_manager.get_printer_status()
        elif self.path == CLUSTER_API + "print_jobs":
            content = self.content_manager.get_print_jobs()
        elif self.path == CLUSTER_API + "materials":
            content = self.content_manager.get_materials()
        elif self.path == "/print_jobs":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "https://youtu.be/dQw4w9WgXcQ")
            self.end_headers()
            return
        else:
            m = self.handle_uuid_path()
            if m and m.group("suffix") == "/preview_image":
                self.send_response(HTTPStatus.OK)
                self.end_headers()
                chunksize = 1024**2 # 1 MiB
                with open(os.path.join(self.module.PATH, "tux.png"), "rb") as fp:
                    while True:
                        chunk = fp.read(chunksize)
                        if chunk == "":
                            break
                        self.wfile.write(chunk)
            else:
                # NOTE: send_error() calls end_headers()
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        json.dump(content, self.wfile)

    def do_POST(self):
        if self.headers.getmaintype() == "multipart":
            if self.path == CLUSTER_API + "print_jobs/":
                self.post_print_job()
            elif self.path == CLUSTER_API + "materials/":
                self.post_material()
        else:
            m = self.handle_uuid_path()
            if m and m.group("suffix") == "/action/move":
                try:
                    self.move_to_top(m.group("uuid"))
                except (ValueError, TypeError, KeyError):
                    self.send_error(HTTPStatus.BAD_REQUEST)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        m = self.handle_uuid_path()
        if m and m.group("suffix") == "/action":
            # pause, print or abort
            self.send_error(HTTPStatus.NOT_IMPLEMENTED)
        elif m and not m.group("suffix"):
            # force print job
            self.send_error(HTTPStatus.NOT_IMPLEMENTED)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        m = self.handle_uuid_path()
        if m and not m.group("suffix"):
            # Delete print job from queue
            index, print_job = self.content_manager.uuid_to_print_job(
                    m.group("uuid"))
            if print_job:
                try:
                    self.module.queue_delete(index, print_job.name)
                except LookupError:
                    self.send_error(HTTPStatus.CONFLICT,
                            "Queues are desynchronised")
                else:
                    self.send_responte(HTTPStatus.NO_CONTENT)
                    self.end_headers()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def handle_uuid_path(self):
        """
        Return the regex match for a path in form:
        /cluster-api/v1/print_jobs/<UUID>...
        with the uuid and the suffix (everything past the uuid) in their
        respective groups.
        """
        return re.match(r"^" + CLUSTER_API + "print_jobs/"
                + r"(?P<uuid>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
                + r"(?P<suffix>.*)$", self.path)


    def post_print_job(self):
        boundary = self.headers.getparam("boundary")
        length = int(self.headers.get("Content-Length", 0))
        try:
            parser = MimeParser(self.rfile, boundary, length,
                self.module.SDCARD_PATH, overwrite=False)
            submessages = parser.parse()
        except:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
        else:
            for msg in submessages:
                name = msg.get_param("name", header="Content-Disposition")
                if name == "file":
                    fname = msg.get_filename()
                elif name == "owner":
                    owner = msg.get_payload().strip()
            self.module.send_print(fname)
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

    def post_material(self):
        boundary = self.headers.getparam("boundary")
        length = int(self.headers.get("Content-Length", 0))
        try:
            parser = MimeParser(self.rfile, boundary, length,
                    self.module.MATERIAL_PATH)
            submessages = parser.parse()
        except:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
        else:
            # Reply is checked specifically for 200
            self.send_response(HTTPStatus.OK)
            self.end_headers()

    def move_to_top(self, uuid):
        length = int(self.headers.get("Content-Length", 0))
        rdata = self.rfile.read(length)
        data = json.loads(rdata)
        if data["list"] == "queued":
            new_index = data["to_position"]
            old_index, print_job = self.content_manager.uuid_to_print_job(uuid)
            if print_job:
                self.module.queue_move(old_index, new_index, print_job.name)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            else:
                send_error(HTTPStatus.NOT_FOUND)

    def log_error(self, format, *args):
        """Similar to log_message, but log under loglevel ERROR"""
        message = ("%s - - [%s] %s" %
                (self.address_string(),
                 self.log_date_time_string(),
                 format%args))
        logger.error(message)

    def log_message(self, format, *args):
        message = ("%s - - [%s] %s" %
                (self.address_string(),
                 self.log_date_time_string(),
                 format%args))
        if (self.path == CLUSTER_API + "printers" or
            self.path == CLUSTER_API + "print_jobs"):
            # Put periodic requests to DEBUG
            logger.debug(message)
        else:
            logger.info(message)


class MimeParser(object):
    """
    Parser for MIME messages which directly writes attached files.

    When calling parse() this class will parse all parts of a multipart
    MIME message, converting the parts to email.Message objects.
    If a part contains a file it is not added as a payload to that
    Message object but instead directly written to the directory
    specified by RECEIVE_DIR.
    If the file already exists, it will be renamed (see _unique_path()
    for details).

    Arguments:
    fp          The file pointer to parse from
    boundary    The MIME boundary, as specified in the main headers
    length      Length of the body, as specified in the main headers
    out_dir     The directory where any files will be written into
    overwrite   In case a file with the same name exists overwrite it
                if True, write to a unique, indexed name otherwise.
                Defaults to True.
    """

    HEADERS = 0
    BODY = 1
    FILE = 2

    def __init__(self, fp, boundary, length, out_dir, overwrite=True):
        self.fp = fp
        self.boundary = boundary
        self.bytes_left = length
        self.out_dir = out_dir
        self.overwrite = overwrite
        self.submessages = []

        # What we are reading right now. One of:
        # self.HEADERS, self.BODY, self.FILE (0, 1, 2)
        self._state = None
        self._current_headers = ""
        self._current_body = ""
        self.fpath = "" # Path to the file to write to

    def parse(self):
        """
        Parse the entire file, returning a list of all submessages
        including headers and bodies, except for transmitted files
        which are directly written to disk.
        """
        while True:
            line = self.fp.readline()
            #TODO Be aware of unicode. This might need change for Python 3.
            self.bytes_left -= len(line)
            try:
                self._parse_line(line)
            except StopIteration:
                break
        return self.submessages

    def _parse_line(self, line):
        """
        Parse a single line by first checking for self._state changes.
        Raising StopIteration breaks the loop in self.parse().
        """
        # Previous message is finished
        if line.startswith("--" + self.boundary):
            if self._current_body:
                self.submessages[-1].set_payload(
                        self._current_body.rstrip("\r\n"))
                self._current_body = ""
            self._state = self.HEADERS # Read headers next
            # This is the last line of the MIME message
            if line.strip() == "--" + self.boundary + "--":
                raise StopIteration()
        # Parse dependent on _state
        elif self._state == self.HEADERS:
            self._parse_headers(line)
        elif self._state == self.BODY:
            self._parse_body(line)

        # FILE state is set after parsing headers and should be
        # handled before reading the next line.
        if self._state == self.FILE:
            self._write_file()

    def _parse_headers(self, line):
        """Add the new line to the headers or parse the full header"""
        if line == "\r\n": # End of headers
            headers_message = email.message_from_string(self._current_headers)
            self._current_headers = ""
            self.submessages.append(headers_message)
            self._start_body(headers_message)
        else:
            self._current_headers += line

    def _parse_body(self, line):
        self._current_body += line

    def _write_file(self):
        """
        Write the file following in fp directly to the disk.
        This does not happen line by line because with a lot of very
        short lines that is quite inefficient. Instead the file is copied
        in blocks with a size of 1024 bytes.
        Then parse the remaining lines that have been read into the
        buffer but do not belong to the file (everything past the first
        occurance of boundary).
        """
        logger.debug("Writing file: {}".format(self.fpath))
        buflen = 1024

        # Use two buffers in case the boundary gets cut in half
        # Make sure to not attempt to read past the content length
        buflen = min(self.bytes_left, buflen)
        buf1 = self.fp.read(buflen)
        self.bytes_left -= buflen

        buflen = min(self.bytes_left, buflen)
        buf2 = self.fp.read(buflen)
        self.bytes_left -= buflen
        with open(self.fpath, "w") as write_fp:
            while self.boundary not in buf1 + buf2:
                write_fp.write(buf1)
                buf1 = buf2
                buflen = min(self.bytes_left, buflen)
                buf2 = self.fp.read(buflen)
                self.bytes_left -= buflen
            if self.bytes_left != 0:
                # Catch the rest of the last line
                remaining_lines = (
                        buf1 + buf2 + self.fp.readline()).splitlines(True)
            else:
                remaining_lines = (buf1 + buf2).splitlines(True)

            # We need an exception for the last line of the file to strip
            # the trailing "\r\n" (<CR><LF>)
            prev_line = ""
            # We take the index with us so we now where to pick up below
            for i, line in enumerate(remaining_lines):
                if self.boundary not in line:
                    write_fp.write(prev_line)
                    prev_line = line
                else:
                    # Now write the last line, but stripped
                    write_fp.write(prev_line.rstrip("\r\n"))
                    break
        # Parse all other lines left in the buffer normally
        # When reaching the end, StopIteration will be propagated up to parse()
        for line in remaining_lines[i:]:
            self._parse_line(line)

    def _start_body(self, headers):
        """Initiate reading of the body depending on whether it is a file"""
        name = headers.get_param("name", header="Content-Disposition")
        if name == "file":
            self.fpath = os.path.join(self.out_dir, headers.get_filename())
            if not self.overwrite:
                self.fpath = self._unique_path(self.fpath)
            self._state = self.FILE
        else:
            self._state = self.BODY

    @staticmethod
    def _unique_path(path):
        """
        Adjust a filename so that it doesn't overwrite an existing file.
        For example, if /path/to/file.txt exists, this function will
        return '/path/to/file-1.txt', then '/path/to/file-2.txt'
        and so on.
        """
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        index = 1
        path = "{}-{}{}".format(root, index, ext)
        while os.path.exists(path):
            path = "{}-{}{}".format(root, index, ext)
            index += 1
        return path


class Server(srv.HTTPServer):
    """Wrapper class to store the module in the server"""
    def __init__(self, server_address, RequestHandler, module):
        srv.HTTPServer.__init__(self, server_address, RequestHandler)
        self.module = module


def get_server(module):
    return Server((module.ADDRESS, 8080), Handler, module)

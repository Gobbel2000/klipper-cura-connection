from datetime import datetime
import os
import uuid as uuid_lib

from .Models.Http.ClusterMaterial import ClusterMaterial
from .Models.Http.ClusterPrintCoreConfiguration import (
        ClusterPrintCoreConfiguration)
from .Models.Http.ClusterPrinterStatus import ClusterPrinterStatus
from .Models.Http.ClusterPrintJobStatus import ClusterPrintJobStatus


class ContentManager:

    def __init__(self, module):
        self.module = module

        self.printer_status = ClusterPrinterStatus(
            enabled=True,
            firmware_version=self.module.VERSION,
            friendly_name=self.module.NAME, # hostname
            ip_address=self.module.ADDRESS,
            machine_variant="Ultimaker 3",
            # One of: idle, printing, error, maintenance, booting
            status="idle",
            unique_name=self.get_mac_address(),
            uuid=self.new_uuid(),
            configuration=[],
        )
        self.print_jobs = [] # type: [ClusterPrintJobStatus]
        self.materials = [] # type: [ClusterMaterial]

    def start(self):
        """
        Add to the list of local materials.
        Must be called later so that filament_manager is available.
        """
        for guid in self.module.filament_manager.guid_to_path:
            version = int(self.module.filament_manager.get_info(guid,
                    "./m:metadata/m:version"))
            self.materials.append(ClusterMaterial(
                guid=guid,
                version=version,
            ))

    def get_print_job_status(self, path):
        """Return a print job model for the given path"""
        metadata = self.module.metadata.get_metadata(path)
        time = metadata.get_time() or 0
        configuration = []
        for i in range(metadata.get_extruder_count()):
            configuration.append(ClusterPrintCoreConfiguration(
                extruder_index=i,
                material={
                    "guid": metadata.get_material_guid(i),
                    "brand": metadata.get_material_brand(i),
                    "color": metadata.get_material_info("./m:metadata/m:name/m:color", i),
                    "material": metadata.get_material_type(i),
                }
            ))
        return ClusterPrintJobStatus(
            created_at=self.get_time_str(),
            force=False,
            machine_variant="Ultimaker 3",
            name=os.path.basename(path),
            started=False,
            # One of: wait_cleanup, finished, sent_to_printer, pre_print,
            # pausing, paused, resuming, queued, printing, post_print
            # (possibly also aborted and aborting)
            status="queued",
            time_total=time,
            time_elapsed=0,
            uuid=self.new_uuid(),
            configuration=self.printer_status.configuration,
            constraints=[],
        )

    def add_test_print(self, path):
        """
        Testing only: add a print job outside of klipper and pretend
        we're printing.
        """
        self.print_jobs.append(self.get_print_job_status(path))
        self.print_jobs[0].status = "printing"
        self.print_jobs[0].started = True
        self.print_jobs[0].time_total = 10000
        self.print_jobs[0].time_elapsed += 2
        self.print_jobs[0].assigned_to = self.printer_status.uuid

        self.printer_status.status = "printing"

    def update_printers(self):
        """Update currently loaded material and state"""
        configuration = []
        fm = self.module.filament_manager
        loaded_materials = fm.material["loaded"]
        for i, material in enumerate(loaded_materials):
            if material['guid'] is None:
                continue
            guid = material['guid']
            brand = fm.get_info(guid, "./m:metadata/m:name/m:brand")
            color = fm.get_info(guid, "./m:metadata/m:name/m:color")
            material = fm.get_info(guid, "./m:metadata/m:name/m:material")
            configuration.append(ClusterPrintCoreConfiguration(
                extruder_index=i,
                material={
                    "guid": guid,
                    "brand": brand,
                    "color": color,
                    "material": material,
                },
            ))
        self.printer_status.configuration = configuration
        if self.module.testing:
            return
        jobs = self.module.sdcard.jobs
        if jobs and jobs[0].state in {"printing", "paused", "pausing", "stopping"}:
            self.printer_status.status = "printing"
        else:
            self.printer_status.status = "idle"

    def update_print_jobs(self):
        """Read queue, Update status, elapsed time"""
        s = self.module.sdcard.get_status()

        # Update self.print_jobs with the queue
        new_print_jobs = []
        for klippy_pj in s["printjobs"]:
            print_job = None
            # Find first cura print job with the same name
            for j, cura_pj in enumerate(self.print_jobs):
                if cura_pj.name == os.path.basename(klippy_pj.path):
                    print_job = self.print_jobs.pop(j)
                    break
            if print_job is None: # Newly added print job
                new_print_jobs.append(self.get_print_job_status(klippy_pj.path))
            else:
                new_print_jobs.append(print_job)
        self.print_jobs = new_print_jobs

        if self.print_jobs: # Update first print job if there is one
            current = s["printjobs"][0]
            elapsed = current.get_printed_time()
            remaining = self.module.print_stats.get_print_time_prediction()[0]
            self.print_jobs[0].time_elapsed = int(elapsed)
            self.print_jobs[0].assigned_to = self.printer_status.uuid
            self.print_jobs[0].time_total = int(elapsed + (1 if remaining is None else remaining))

            # State
            if current.state == "stopping":
                self.print_jobs[0].status = "aborting"
            elif current.state == "stopped":
                self.print_jobs[0].status = "aborted"
            elif current.state == "done":
                self.print_jobs[0].status = "finished"
            else:
                self.print_jobs[0].status = current.state

            if current.state == "printing":
                self.print_jobs[0].started = True

    @staticmethod
    def new_uuid():
        """Returns a newly generated UUID as a str"""
        # uuid4() generates a completely random uuid
        return str(uuid_lib.uuid4())

    @staticmethod
    def get_time_str():
        """Returns the current UTC time in a string in ISO8601 format"""
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def get_mac_address():
        """Return the mac address in form XX:XX:XX:XX:XX:XX"""
        raw = uuid_lib.getnode()
        hex_ = []
        while raw:
            hex_.insert(0, hex(int(raw % 0x100))) # raw is long
            raw = raw >> 8
        return ":".join([i.lstrip("0x").zfill(2) for i in hex_])

    def uuid_to_print_job(self, uuid):
        """
        Return a tuple (index, print job) for the print job with the given
        UUID.  Return (None, None) if the UUID could not be found.
        """
        return next(iter((i, pj) for i, pj in enumerate(self.print_jobs)
            if pj.uuid == uuid), (None, None))

    def get_printer_status(self):
        self.update_printers()
        return [self.printer_status.serialize()]
    def get_print_jobs(self):
        if not self.module.testing:
            self.update_print_jobs()
        return [m.serialize() for m in self.print_jobs]
    def get_materials(self):
        return [m.serialize() for m in self.materials]

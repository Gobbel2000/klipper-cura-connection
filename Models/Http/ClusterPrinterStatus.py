# Copyright (c) 2019 Ultimaker B.V.
# Cura is released under the terms of the LGPLv3 or higher.
from typing import List, Union, Dict, Optional, Any

from .ClusterBuildPlate import ClusterBuildPlate
from .ClusterPrintCoreConfiguration import ClusterPrintCoreConfiguration
from .ClusterPrinterMaterialStation import ClusterPrinterMaterialStation
from .ClusterPrinterMaterialStationSlot import ClusterPrinterMaterialStationSlot
from .ClusterPrinterConfigurationMaterial import ClusterPrinterConfigurationMaterial
from ..BaseModel import BaseModel


##  Class representing a cluster printer
class ClusterPrinterStatus(BaseModel):

    ## Creates a new cluster printer status
    #  \param enabled: A printer can be disabled if it should not receive new jobs. By default every printer is enabled.
    #  \param firmware_version: Firmware version installed on the printer. Can differ for each printer in a cluster.
    #  \param friendly_name: Human readable name of the printer. Can be used for identification purposes.
    #  \param ip_address: The IP address of the printer in the local network.
    #  \param machine_variant: The type of printer. Can be 'Ultimaker 3' or 'Ultimaker 3ext'.
    #  \param status: The status of the printer.
    #  \param unique_name: The unique name of the printer in the network.
    #  \param uuid: The unique ID of the printer, also known as GUID.
    #  \param configuration: The active print core configurations of this printer.
    #  \param reserved_by: A printer can be claimed by a specific print job.
    #  \param maintenance_required: Indicates if maintenance is necessary.
    #  \param firmware_update_status: Whether the printer's firmware is up-to-date, value is one of: "up_to_date",
    #         "pending_update", "update_available", "update_in_progress", "update_failed", "update_impossible".
    #  \param latest_available_firmware: The version of the latest firmware that is available.
    #  \param build_plate: The build plate that is on the printer.
    #  \param material_station: The material station that is on the printer.
    def __init__(self, enabled: bool, firmware_version: str, friendly_name: str, ip_address: str, machine_variant: str,
                 status: str, unique_name: str, uuid: str,
                 configuration: List[Union[Dict[str, Any], ClusterPrintCoreConfiguration]],
                 reserved_by: Optional[str] = None, maintenance_required: Optional[bool] = None,
                 firmware_update_status: Optional[str] = None, latest_available_firmware: Optional[str] = None,
                 build_plate: Union[Dict[str, Any], ClusterBuildPlate] = None,
                 material_station: Union[Dict[str, Any], ClusterPrinterMaterialStation] = None, **kwargs) -> None:

        self.configuration = self.parseModels(ClusterPrintCoreConfiguration, configuration)
        self.enabled = enabled
        self.firmware_version = firmware_version
        self.friendly_name = friendly_name
        self.ip_address = ip_address
        self.machine_variant = machine_variant
        self.status = status
        self.unique_name = unique_name
        self.uuid = uuid
        self.reserved_by = reserved_by
        self.maintenance_required = maintenance_required
        self.firmware_update_status = firmware_update_status
        self.latest_available_firmware = latest_available_firmware
        self.build_plate = self.parseModel(ClusterBuildPlate, build_plate) if build_plate else None
        self.material_station = self.parseModel(ClusterPrinterMaterialStation,
                                                material_station) if material_station else None
        super().__init__(**kwargs)

from Scripts.datasets import cpu_data
from Scripts.datasets import kext_data
from Scripts.datasets import os_data
from Scripts.datasets import pci_data
from Scripts.datasets import codec_layouts
from Scripts import utils
import os
import shutil

try:
    long
    unicode
except NameError:
    long = int
    unicode = str

class KextMaestro:
    def __init__(self):
        self.utils = utils.Utils()
        self.matching_keys = [
            "IOPCIMatch", 
            "IONameMatch", 
            "IOPCIPrimaryMatch", 
            "idProduct", 
            "idVendor", 
            "HDAConfigDefault"
        ]
        self.category_labels = {
            "Required": "必需",
            "VirtualSMC Plugins": "VirtualSMC 插件",
            "Graphics": "显卡",
            "Audio": "音频",
            "Wi-Fi": "Wi-Fi",
            "Bluetooth": "蓝牙",
            "Ethernet": "以太网",
            "USB": "USB",
            "Input": "输入设备",
            "Brand Specific": "品牌专用",
            "Storage": "存储",
            "Card Reader": "读卡器",
            "TSC Synchronization": "TSC 同步",
            "Extras": "其他"
        }
        self.ock_files_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "OCK_Files")
        self.kexts = kext_data.kexts
        
    def extract_pci_id(self, kext_path):
        if not os.path.exists(kext_path):
            return []

        plist_path = os.path.join(kext_path, "Contents", "Info.plist")
        plist_data = self.utils.read_file(plist_path)

        pci_ids = []

        for personality_name, properties in plist_data.get("IOKitPersonalities", {}).items():
            matching_keys = [key for key in self.matching_keys if key in properties]
            
            if not matching_keys:
                continue
            
            match_key = matching_keys[0]

            if match_key in ["IOPCIMatch", "IOPCIPrimaryMatch"]:
                pci_list = properties[match_key].split(" ")
                for pci_id in pci_list:
                    vendor_id = pci_id[-4:]
                    device_id = pci_id[2:6]
                    pci_ids.append("{}-{}".format(vendor_id, device_id).upper())
            elif match_key == "IONameMatch":
                for pci_id in properties[match_key]:
                    vendor_id = pci_id[3:7]
                    device_id = pci_id.split(",")[1].zfill(4)
                    pci_ids.append("{}-{}".format(vendor_id, device_id).upper())
            elif match_key == "idProduct":
                vendor_id = self.utils.int_to_hex(properties["idVendor"]).zfill(4)
                device_id = self.utils.int_to_hex(properties["idProduct"]).zfill(4)
                pci_ids.append("{}-{}".format(vendor_id, device_id).upper())
            elif match_key == "HDAConfigDefault":
                for codec_layout in properties[match_key]:
                    codec_id = self.utils.int_to_hex(codec_layout.get("CodecID")).zfill(8)
                    pci_ids.append("{}-{}".format(codec_id[:4], codec_id[-4:]))
                pci_ids = sorted(list(set(pci_ids)))

        return pci_ids

    def is_intel_hedt_cpu(self, processor_name, cpu_codename):
        if cpu_codename in cpu_data.IntelCPUGenerations[45:66]:
            return cpu_codename.endswith(("-X", "-P", "-W", "-E", "-EP", "-EX"))
        
        if cpu_codename in cpu_data.IntelCPUGenerations[66:]:
            return "Xeon" in processor_name
        
        return False

    def is_kext_compatible(self, kext, target_darwin_version):
        return self.utils.parse_darwin_version(kext.min_darwin_version) <= self.utils.parse_darwin_version(target_darwin_version) <= self.utils.parse_darwin_version(kext.max_darwin_version)

    def get_kext_status(self, kext, target_darwin_version):
        if kext.required:
            return "required"

        if self.is_kext_compatible(kext, target_darwin_version):
            return "selected" if kext.checked else "available"

        if kext.checked:
            return "force_loaded"

        if kext.allow_force_load:
            return "force_available"

        if self.utils.parse_darwin_version(target_darwin_version) > self.utils.parse_darwin_version(kext.max_darwin_version):
            return "not_needed"

        return "requires_newer"

    def check_kext(self, index, target_darwin_version, force_load=False, checking=None):
        if index is None or not 0 <= index < len(self.kexts):
            return False

        kext = self.kexts[index]
        checking = checking or set()

        if kext.checked:
            return True

        if index in checking:
            return True

        if not self.is_kext_compatible(kext, target_darwin_version) and not (force_load and kext.allow_force_load):
            return False

        checking.add(index)
        for requires_kext_name in kext.requires_kexts:
            requires_kext_index = kext_data.kext_index_by_name.get(requires_kext_name)
            if not self.check_kext(requires_kext_index, target_darwin_version, force_load, checking):
                checking.remove(index)
                return False
        checking.remove(index)

        kext.checked = True

        if kext.conflict_group_id:
            for other_index, other_kext in enumerate(self.kexts):
                if other_kext.conflict_group_id == kext.conflict_group_id and other_kext.name != kext.name:
                    self.uncheck_kext(other_index)

        return True

    def select_required_kexts(self, hardware_report, macos_version, needs_oclp, acpi_patches):
        self.utils.head("选择必需的内核扩展")
        print("")
        print("正在检查必需的内核扩展...")

        for kext in self.kexts:
            kext.checked = kext.required

        selected_kexts = ["UTBDefault"]

        if "Intel" in hardware_report.get("CPU").get("Manufacturer"):
            selected_kexts.extend(("SMCProcessor", "SMCSuperIO"))

        if "Laptop" in hardware_report.get("Motherboard").get("Platform") and not "SURFACE" in hardware_report.get("Motherboard").get("Name"):
            selected_kexts.append("SMCBatteryManager")
            if "DELL" in hardware_report.get("Motherboard").get("Name"):
                selected_kexts.append("SMCDellSensors")
            selected_kexts.append("SMCLightSensor")

        if  not (" Core" in hardware_report.get("CPU").get("Processor Name") and \
                 hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[28:]) or \
            self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("23.0.0"):
            selected_kexts.append("RestrictEvents")

        if any(codec_properties.get("Device ID") in codec_layouts.data for codec_properties in hardware_report.get("Sound", {}).values()):
            if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
                print("\n\033[1;93m注意:\033[0m macOS Tahoe 不再包含 AppleHDA。AppleALC 需要以下其中一种恢复方法。")
                print("")
                print("1. \033[1mAppleALC + OCLP\033[0m - 通过系统根补丁恢复 AppleHDA")
                print("2. \033[1mAppleALC + SimpleHDA\033[0m - 通过 OpenCore 注入，无需音频根补丁")
                print("")
                while True:
                    kext_option = self.utils.request_input("请为系统选择音频 kext: ").strip()
                    if kext_option == "1":
                        needs_oclp = True
                        selected_kexts.append("AppleALC")
                        break
                    elif kext_option == "2":
                        selected_kexts.extend(("AppleALC", "SimpleHDA"))
                        break
                    else:
                        print("\033[91m选择无效，请重试。\033[0m\n\n")
            else:
                selected_kexts.append("AppleALC")
        
        if "AMD" in hardware_report.get("CPU").get("Manufacturer") and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("21.4.0") or \
            int(hardware_report.get("CPU").get("CPU Count")) > 1 and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("19.0.0") or \
            hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[:4]:
            selected_kexts.append("AppleMCEReporterDisabler")

        if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("22.0.0") and not "AVX2" in hardware_report.get("CPU").get("SIMD Features"):
            selected_kexts.append("CryptexFixup")

        if  "Lunar Lake" not in hardware_report.get("CPU").get("Codename") and \
            "Meteor Lake" not in hardware_report.get("CPU").get("Codename") and \
            hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[:20] and \
            int(hardware_report.get("CPU").get("Core Count")) > 6:
            selected_kexts.append("CpuTopologyRebuild")

        for gpu_name, gpu_props in hardware_report.get("GPU", {}).items():
            if "Integrated GPU" in gpu_props.get("Device Type"):
                if "AMD" in gpu_props.get("Manufacturer"):
                    selected_kexts.append("NootedRed")
                else:
                    selected_kexts.append("WhateverGreen")
            else:
                if "Navi 22" in gpu_props.get("Codename"):
                    selected_kexts.append("NootRX")
                    break

                if gpu_props.get("Codename") in {"Navi 21", "Navi 23"}:
                    print("\n*** 已检测到 {} 为 AMD {} GPU。".format(gpu_name, gpu_props.get("Codename")))
                    print("")
                    print("\033[91m重要提示: 黑屏修复\033[0m")
                    print("如果在详细模式后遇到黑屏:")
                    print("    1. 使用 ProperTree 打开 config.plist")
                    print("    2. 导航到 NVRAM -> Add -> 7C436110-AB2A-4BBB-A880-FE41995C9F82 -> boot-args")
                    print("    3. 从 boot-args 中移除 \"-v debug=0x100 keepsyms=1\"")
                    print("")
                    recommended_option = 2
                    recommended_name = "WhateverGreen"
                    print("\033[1;93m注意:\033[0m")
                    print("- AMD {} GPU 有两个可用的 kext 选项:".format(gpu_props.get("Codename")))
                    print("- 安装后可以尝试不同 kext，以找到最适合系统的选项")
                    print("")
                    print("1. \033[1mNootRX\033[0m - 使用最新 GPU 固件")
                    print("2. \033[1mWhateverGreen\033[0m - 使用 Apple 原始固件")
                    print("")

                    if any(other_gpu_props.get("Manufacturer") == "Intel" for other_gpu_props in hardware_report.get("GPU", {}).values()):
                        print("\033[91m重要提示:\033[0m NootRX kext 与 Intel GPU 不兼容")
                        print("为了兼容 Intel GPU，已自动选择 WhateverGreen kext")
                        print("")
                        self.utils.request_input("按回车键继续...")
                        continue

                    kext_option = self.utils.request_input("请为 AMD {} GPU 选择 kext (默认: {}): ".format(gpu_props.get("Codename"), recommended_name)).strip() or str(recommended_option)
                    
                    if kext_option.isdigit() and 0 < int(kext_option) < 3:
                        selected_option = int(kext_option)
                    else:
                        print("\033[93m选择无效，已使用推荐选项: {}\033[0m".format(recommended_option))
                        selected_option = recommended_option

                    if selected_option == 1:
                        selected_kexts.append("NootRX")
                    elif selected_option == 2:
                        selected_kexts.append("WhateverGreen")
                    
                    continue

                selected_kexts.append("WhateverGreen")

        if "Laptop" in hardware_report.get("Motherboard").get("Platform") and ("ASUS" in hardware_report.get("Motherboard").get("Name") or "NootedRed" in selected_kexts):
            selected_kexts.append("ForgedInvariant")

        if self.is_intel_hedt_cpu(hardware_report.get("CPU").get("Processor Name"), hardware_report.get("CPU").get("Codename")):
            selected_kexts.append("CpuTscSync")

        for network_name, network_props in hardware_report.get("Network", {}).items():
            device_id = network_props.get("Device ID")

            if device_id in pci_data.BroadcomWiFiIDs and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("23.0.0"):
                selected_kexts.append("IOSkywalkFamily")
                if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
                    selected_kexts.append("AirPortBrcmNIC-Tahoe")

            if device_id in pci_data.BroadcomWiFiIDs[:15]:
                selected_kexts.append("AirportBrcmFixup")
            elif device_id == pci_data.BroadcomWiFiIDs[15] and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("19.0.0"):
                selected_kexts.append("AirportBrcmFixup")
            elif device_id in pci_data.BroadcomWiFiIDs[16:18] and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("20.0.0"):
                selected_kexts.append("AirportBrcmFixup")
            elif device_id in pci_data.IntelWiFiIDs:
                print("\n*** 已检测到 {} 为 Intel WiFi 设备。".format(network_name))
                print("")
                darwin_major = self.utils.parse_darwin_version(macos_version)[0]
                native_fork_available = self.is_kext_compatible(self.kexts[kext_data.kext_index_by_name["AirportItlwm-laobamac"]], macos_version)
                wifi_options = ["AirportItlwm", "itlwm"]
                if native_fork_available:
                    wifi_options.insert(0, "AirportItlwm-laobamac")
                print("\033[1;93m注意:\033[0m Intel WiFi 设备有 {} 个可用的 kext 选项:".format(len(wifi_options)))
                print("")
                if native_fork_available:
                    print("1. \033[1mlaobamac AirportItlwm\033[0m - 在 Sequoia 15.2+ 和 Tahoe 上使用原生 WiFi，无需 OCLP 根补丁")
                    print("")
                print("{}. \033[1mAirportItlwm\033[0m - 使用原生 WiFi 设置菜单".format(2 if native_fork_available else 1))
                print("   • 支持接力、通用剪贴板、定位服务和即时热点")
                print("   • 支持企业级安全")
                if darwin_major >= 24:
                    print("   • 在 Sequoia 和 Tahoe 上需要 OCLP 根补丁，但可能引发问题")
                print("")
                print("{}. \033[1mitlwm\033[0m - 整体更稳定".format(len(wifi_options)))
                print("   • 配合 HeliPort 应用使用，而不是原生 WiFi 设置菜单")
                print("   • 不支持 Apple 连续互通功能和企业级安全")
                print("   • 可以连接隐藏网络")
                print("")
                recommended_option = 1 if native_fork_available or darwin_major < 23 else 2
                if "Beta" in os_data.get_macos_name_by_darwin(macos_version) and not native_fork_available:
                    print("对于不支持的 macOS 测试版，仅支持 itlwm。")
                    selected_name = "itlwm"
                else:
                    choice = self.utils.request_input("请选择 Intel Wi-Fi kext (默认: {}): ".format(wifi_options[recommended_option - 1])).strip() or str(recommended_option)
                    if not choice.isdigit() or not 1 <= int(choice) <= len(wifi_options):
                        print("选择无效，已使用推荐选项: {}".format(recommended_option))
                        choice = str(recommended_option)
                    selected_name = wifi_options[int(choice) - 1]
                selected_kexts.append(selected_name)
                if selected_name == "AirportItlwm" and darwin_major >= 24:
                    needs_oclp = True
                    selected_kexts.append("IOSkywalkFamily")
                    if darwin_major >= 25:
                        selected_kexts.append("AirPortBrcmNIC-Tahoe")
            elif device_id in pci_data.AtherosWiFiIDs[:8]:
                selected_kexts.append("corecaptureElCap")
                if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
                    selected_kexts.append("AirPortAtheros40-Tahoe")
                if self.utils.parse_darwin_version(macos_version) > self.utils.parse_darwin_version("20.99.99"):
                    selected_kexts.append("AMFIPass")
            elif device_id in pci_data.rtw88WiFiIDs:
                selected_kexts.append("Feixiao")
            elif device_id in pci_data.IntelI22XIDs:
                selected_kexts.append("AppleIGC")
            elif device_id in pci_data.AtherosE2200IDs:
                selected_kexts.append("AtherosE2200Ethernet")
            elif device_id in pci_data.IntelMausiIDs:
                selected_kexts.append("IntelMausiEthernet")
            elif device_id in pci_data.RealtekRTL8100IDs:
                selected_kexts.append("RealtekRTL8100")
            elif device_id in pci_data.RealtekRTL8111IDs:
                selected_kexts.append("RealtekRTL8111")
            elif device_id in pci_data.AppleIGBIDs:
                selected_kexts.append("AppleIGB")
            elif device_id in pci_data.BroadcomBCM57XXIDs:
                selected_kexts.append("CatalinaBCM5701Ethernet")
            elif device_id in pci_data.IntelX500IDs:
                selected_kexts.append("IntelLucy")
            elif device_id in pci_data.RealtekRTL812xIDs:
                selected_kexts.append("RTL812xLucy")

        if all(network_props.get("Bus Type") == "USB" for network_props in hardware_report.get("Network", {}).values()):
            selected_kexts.append("NullEthernet")

        for bluetooth_name, bluetooth_props in hardware_report.get("Bluetooth", {}).items():
            usb_id = bluetooth_props.get("Device ID")

            if usb_id in pci_data.AtherosBluetoothIDs:
                selected_kexts.extend(("Ath3kBT", "Ath3kBTInjector"))
            elif usb_id in pci_data.BroadcomBluetoothIDs:               
                selected_kexts.extend(("BrcmFirmwareData", "BrcmPatchRAM2", "BrcmPatchRAM3", "BrcmBluetoothInjector", "BlueToolFixup"))
            elif usb_id in pci_data.IntelBluetoothIDs:
                selected_kexts.extend(("IntelBluetoothFirmware", "IntelBTPatcher", "IntelBluetoothInjector", "BlueToolFixup"))
            elif usb_id in pci_data.RealtekBluetoothIDs:
                selected_kexts.extend(("RealtekBluetoothFirmware", "BlueToolFixup"))
            elif usb_id in pci_data.BluetoothIDs[-1]:
                selected_kexts.append("BlueToolFixup")

        if "Laptop" in hardware_report.get("Motherboard").get("Platform"):
            if "SURFACE" in hardware_report.get("Motherboard").get("Name"):
                selected_kexts.append("BigSurface")
            else:
                if "ASUS" in hardware_report.get("Motherboard").get("Name"):
                    selected_kexts.append("AsusSMC")
                selected_kexts.append("BrightnessKeys")

                for device_name, device_props in hardware_report.get("Input").items():
                    if not device_props.get("Device"):
                        continue

                    device_id = device_props.get("Device")
                    idx = None
                    if device_id in pci_data.InputIDs:
                        idx = pci_data.InputIDs.index(device_id)

                    if "PS/2" in device_props.get("Device Type", "None"):
                        selected_kexts.append("VoodooPS2Controller")
                        if device_id.startswith("SYN"):
                            selected_kexts.append("VoodooRMI")
                        elif idx and 75 < idx < 79:
                            selected_kexts.append("VoodooSMBus")
                    if "I2C" in device_props.get("Device Type", "None"):
                        selected_kexts.append("VoodooI2CHID")
                        if idx:
                            if idx < 76:
                                selected_kexts.append("AlpsHID")
                            elif 78 < idx:
                                selected_kexts.append("VoodooRMI")
        
        for device_name, device_info in hardware_report.get("System Devices", {}).items():
            if device_info.get("Bus Type") == "ACPI" and device_info.get("Device") in pci_data.YogaHIDs:
                selected_kexts.append("YogaSMC")

        if any(patch.checked for patch in acpi_patches if patch.name == "BATP"):
            selected_kexts.append("ECEnabler")

        for controller_name, controller_props in hardware_report.get("SD Controller", {}).items():
            if controller_props.get("Device ID") in pci_data.RealtekCardReaderIDs:
                if controller_props.get("Device ID") in pci_data.RealtekCardReaderIDs[5:]:
                    selected_kexts.append("Sinetek-rtsx")
                else:
                    selected_kexts.append("RealtekCardReader")
        
        for controller_name, controller_props in hardware_report.get("Storage Controllers", {}).items():
            if controller_props.get("Device ID") == "1C5C-174A":
                selected_kexts.append("PC711Probe")

            if "NVMe" in controller_name or "NVM Express" in controller_name:
                selected_kexts.append("NVMeFix")
            elif not "AHCI" in controller_name or "AMD" in hardware_report.get("CPU").get("Manufacturer"):
                if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("20.0.0"):
                    if controller_props.get("Device ID") in pci_data.UnsupportedSATAControllerIDs:
                        selected_kexts.append("CtlnaAHCIPort")
                else:
                    if controller_props.get("Device ID") in pci_data.UnsupportedSATAControllerIDs[15:]:
                        selected_kexts.append("SATA-unsupported")

        for controller_name, controller_props in hardware_report.get("USB Controllers").items():
            device_id = controller_props.get("Device ID")
            subsystem_id = controller_props.get("Subsystem ID", "0"*8)
            unsupported_controller = next((controller for controller in pci_data.UnsupportedUSBControllerIDs if device_id == controller[0]), None)

            if unsupported_controller:
                subsystem_ids = unsupported_controller[1]

                if len(subsystem_ids) > 0 and subsystem_id not in subsystem_ids:
                    continue

                if device_id.startswith("1022"):
                    if "Laptop" in hardware_report.get("Motherboard").get("Platform"):
                        selected_kexts.append("GenericUSBXHCI")
                else:
                    selected_kexts.append("XHCI-unsupported")

        if "Sandy Bridge" in hardware_report.get("CPU").get("Codename"):
            selected_kexts.append("ASPP-Override")

        if "Sandy Bridge" in hardware_report.get("CPU").get("Codename") or "Ivy Bridge" in hardware_report.get("CPU").get("Codename"):
            selected_kexts.extend(("AppleIntelCPUPowerManagement", "AppleIntelCPUPowerManagementClient"))

        if needs_oclp:
            selected_kexts.extend(("AMFIPass", "RestrictEvents"))

        selected_kexts = list(dict.fromkeys(selected_kexts))
        force_load_kexts = self.verify_kext_compatibility(selected_kexts, macos_version)

        for name in selected_kexts:
            self.check_kext(kext_data.kext_index_by_name.get(name), macos_version, name in force_load_kexts)

        return needs_oclp

    def install_kexts_to_efi(self, macos_version, kexts_directory):
        for kext in self.kexts:
            if kext.checked:
                try:
                    source_kext_path = destination_kext_path = None
                    kext_name = kext.name

                    if kext_name == "Feixiao":
                        kext_name = "rtw88"

                    search_directory = self.ock_files_dir
                    if kext_name == "AirportItlwm-laobamac":
                        search_directory = os.path.join(self.ock_files_dir, kext_name + macos_version[:2])
                        kext_name = "AirportItlwm"
                    kext_paths = self.utils.find_matching_paths(search_directory, extension_filter=".kext", name_filter=kext_name)
                    for kext_path, type in kext_paths:
                        if kext.name == "AirportItlwm":
                            version = macos_version[:2]
                            if all((self.kexts[kext_data.kext_index_by_name.get("IOSkywalkFamily")].checked, self.kexts[kext_data.kext_index_by_name.get("IO80211FamilyLegacy")].checked)) or self.utils.parse_darwin_version("24.0.0") <= self.utils.parse_darwin_version(macos_version):
                                version = "22"
                            elif self.utils.parse_darwin_version("23.4.0") <= self.utils.parse_darwin_version(macos_version):
                                version = "23.4"
                            elif self.utils.parse_darwin_version("23.0.0") <= self.utils.parse_darwin_version(macos_version):
                                version = "23.0"
                            
                            if kext_path.replace("\\", "/").split("/")[0] == "AirportItlwm" + version and os.path.basename(kext_path) == "AirportItlwm.kext":
                                source_kext_path = os.path.join(self.ock_files_dir, kext_path)
                                destination_kext_path = os.path.join(kexts_directory, os.path.basename(kext_path))
                                break
                        else:
                            main_kext = kext_path.replace("\\", "/").split("/")[0]
                            main_kext_index = kext_data.kext_index_by_name.get(main_kext)
                            if not main_kext_index or self.kexts[main_kext_index].checked:
                                if os.path.splitext(os.path.basename(kext_path))[0] == kext_name:
                                    source_kext_path = os.path.join(search_directory, kext_path)
                                    destination_kext_path = os.path.join(kexts_directory, os.path.basename(kext_path))
                    
                    if kext.name in ("AirportItlwm-laobamac", "SimpleHDA") and not source_kext_path:
                        raise FileNotFoundError("找不到 {} 的已下载 bundle。".format(kext.name))
                    if os.path.exists(source_kext_path):
                        shutil.copytree(source_kext_path, destination_kext_path, dirs_exist_ok=True)
                except Exception:
                    if kext.name in ("AirportItlwm-laobamac", "SimpleHDA"):
                        raise
                    continue

    def process_kext(self, kexts_directory, kext_path):
        try:
            plist_path = self.utils.find_matching_paths(os.path.join(kexts_directory, kext_path), extension_filter=".plist", name_filter="Info")[0][0]
            bundle_info = self.utils.read_file(os.path.join(kexts_directory, kext_path, plist_path))

            if isinstance(bundle_info.get("CFBundleIdentifier", None), (str, unicode)):
                pass
        except:
            return None

        executable_path = os.path.join("Contents", "MacOS", bundle_info.get("CFBundleExecutable", "None"))
        if not os.path.exists(os.path.join(kexts_directory, kext_path, executable_path)):
            executable_path = ""

        bundle_libraries = {
            bundle_identifier: bundle_version
            for bundle_identifier, bundle_version in bundle_info.get("OSBundleLibraries", {}).items() 
        }

        if bundle_info.get("CFBundleExecutable", "None") == "AppleALC":
            bundle_libraries["org.ChefKiss.NootedRed"] = "0.8.10"
        elif bundle_info.get("CFBundleExecutable", "None") == "SimpleHDA":
            bundle_libraries["as.vit9696.AppleALC"] = "1.9.9"
        
        return {
            "BundlePath": kext_path.replace("\\", "/").lstrip("/"),
            "Enabled": True,
            "ExecutablePath": executable_path.replace("\\", "/").lstrip("/"),
            "PlistPath": plist_path.replace("\\", "/").lstrip("/"),
            "BundleIdentifier": bundle_info.get("CFBundleIdentifier"),
            "BundleVersion": bundle_info.get("CFBundleVersion"),
            "BundleLibraries": bundle_libraries
        }

    def modify_kexts(self, plist_path, hardware_report, macos_version):
        try:
            bundle_info = self.utils.read_file(plist_path)

            if bundle_info.get("IOKitPersonalities").get("itlwm").get("WiFiConfig"):
                from Scripts import wifi_profile_extractor
                
                wifi_profiles = wifi_profile_extractor.WifiProfileExtractor().get_profiles()

                if wifi_profiles:
                    bundle_info["IOKitPersonalities"]["itlwm"]["WiFiConfig"] = {
                        "WiFi_{}".format(index): {
                            "password": profile[1],
                            "ssid": profile[0]
                        }
                        for index, profile in enumerate(wifi_profiles, start=1)
                    }
            elif bundle_info.get("IOKitPersonalities").get("VoodooTSCSync"):
                bundle_info["IOKitPersonalities"]["VoodooTSCSync"]["IOPropertyMatch"]["IOCPUNumber"] = 0 if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("21.0.0") else int(hardware_report["CPU"]["Core Count"]) - 1
            elif bundle_info.get("IOKitPersonalities").get("AmdTscSync"):
                bundle_info["IOKitPersonalities"]["AmdTscSync"]["IOPropertyMatch"]["IOCPUNumber"] = 0 if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("21.0.0") else int(hardware_report["CPU"]["Core Count"]) - 1
            else:
                return
            
            self.utils.write_file(plist_path, bundle_info)
        except:
            return            
        
    def load_kexts(self, hardware_report, macos_version, kexts_directory):
        kernel_add = []
        unload_kext = []

        if self.kexts[kext_data.kext_index_by_name.get("IO80211ElCap")].checked:
            unload_kext.extend((
                "AirPortBrcm4331",
                "AppleAirPortBrcm43224"
            ))
        elif self.kexts[kext_data.kext_index_by_name.get("VoodooSMBus")].checked:
            unload_kext.append("VoodooPS2Mouse")
        elif self.kexts[kext_data.kext_index_by_name.get("VoodooRMI")].checked:
            if not self.kexts[kext_data.kext_index_by_name.get("VoodooI2C")].checked:
                unload_kext.append("RMII2C")
            else:
                unload_kext.extend((
                    "VoodooSMBus",
                    "RMISMBus",
                    "VoodooI2CHID"
                ))

        kext_paths = self.utils.find_matching_paths(kexts_directory, extension_filter=".kext")
        bundle_list = []

        for kext_path, type in kext_paths:
            bundle_info = self.process_kext(kexts_directory, kext_path)

            if bundle_info:
                self.modify_kexts(os.path.join(kexts_directory, kext_path, bundle_info.get("PlistPath")), hardware_report, macos_version)

                bundle_list.append(bundle_info)

        bundle_dict = {bundle["BundleIdentifier"]: bundle for bundle in bundle_list}

        sorted_bundles = []
        
        visited = set()
        seen_identifier = set()

        def visit(bundle):
            kext_name = os.path.splitext(os.path.basename(bundle.get("BundlePath")))[0]
            if kext_name == "rtw88":
                kext_name = "Feixiao"
            elif kext_name == "AirportItlwm" and self.kexts[kext_data.kext_index_by_name["AirportItlwm-laobamac"]].checked:
                kext_name = "AirportItlwm-laobamac"

            if kext_name in unload_kext or (bundle.get("BundlePath"), bundle.get("BundleIdentifier")) in visited:
                return
                        
            bundle["MaxKernel"] = os_data.get_latest_darwin_version()
            bundle["MinKernel"] = os_data.get_lowest_darwin_version()

            kext_index = kext_data.kext_index_by_name.get(kext_name)

            if kext_index:
                bundle["MaxKernel"] = self.kexts[kext_index].max_darwin_version
                bundle["MinKernel"] = self.kexts[kext_index].min_darwin_version
            
            for dep_identifier in bundle.get("BundleLibraries"):
                if dep_identifier in bundle_dict:
                    visit(bundle_dict[dep_identifier])
                    
                    bundle["MaxKernel"] = bundle["MaxKernel"] if self.utils.parse_darwin_version(bundle["MaxKernel"]) < self.utils.parse_darwin_version(bundle_dict[dep_identifier].get("MaxKernel", "99.99.99")) else bundle_dict[dep_identifier]["MaxKernel"]
                    bundle["MinKernel"] = bundle["MinKernel"] if self.utils.parse_darwin_version(bundle["MinKernel"]) > self.utils.parse_darwin_version(bundle_dict[dep_identifier].get("MinKernel", "0.0.0")) else bundle_dict[dep_identifier]["MinKernel"]

            wifi_identifier = bundle.get("BundleIdentifier")
            if wifi_identifier in ("com.apple.driver.AirPort.BrcmNIC", "com.apple.driver.AirPort.Atheros40"):
                if bundle["BundlePath"].endswith("-Tahoe.kext"):
                    bundle["MinKernel"] = "25.0.0"
                else:
                    bundle["MaxKernel"] = "24.99.99"
                    bundle["MinKernel"] = "23.0.0" if wifi_identifier == "com.apple.driver.AirPort.BrcmNIC" else "18.0.0"

            if kext_name == "AirPortBrcm4360_Injector":
                bundle["MaxKernel"] = "19.99.99"
            elif kext_name in ("AirportItlwm", "AirportItlwm-laobamac"):
                bundle["MaxKernel"] = macos_version[:2] + bundle["MaxKernel"][2:]
                if kext_name == "AirportItlwm" or macos_version.startswith("25."):
                    bundle["MinKernel"] = macos_version[:2] + ".0.0"

            visited.add((bundle.get("BundlePath"), bundle.get("BundleIdentifier")))

            identifier = bundle.get("BundleIdentifier")
            if identifier in ("com.apple.driver.AirPort.BrcmNIC", "com.apple.driver.AirPort.Atheros40"):
                identifier = (identifier, bundle["MinKernel"], bundle["MaxKernel"])
            if identifier in seen_identifier:
                bundle["Enabled"] = False
            else:
                seen_identifier.add(identifier)

            sorted_bundles.append(bundle)

        for bundle in bundle_list:
            visit(bundle)

        latest_darwin_version = (os_data.get_latest_darwin_version(), os_data.get_latest_darwin_version(include_beta=False))
        lowest_darwin_version = os_data.get_lowest_darwin_version()

        for bundle in sorted_bundles:
            kernel_add.append({
                "Arch": "Any",
                "BundlePath": bundle.get("BundlePath"),
                "Comment": "",
                "Enabled": bundle.get("Enabled"),
                "ExecutablePath": bundle.get("ExecutablePath"),
                "MaxKernel": "" if bundle.get("MaxKernel") in latest_darwin_version else bundle.get("MaxKernel"),
                "MinKernel": "" if bundle.get("MinKernel") == lowest_darwin_version else bundle.get("MinKernel"),
                "PlistPath": bundle.get("PlistPath")
            })

        return kernel_add

    def uncheck_kext(self, index):
        if index is None or not 0 <= index < len(self.kexts):
            return False

        kext = self.kexts[index]

        if not kext.checked:
            return True

        dependents = [
            (other_index, other_kext)
            for other_index, other_kext in enumerate(self.kexts)
            if other_kext.checked and kext.name in other_kext.requires_kexts
        ]

        if kext.required or any(dependent.required for _, dependent in dependents):
            return False

        kext.checked = False

        for dependent_index, _ in dependents:
            self.uncheck_kext(dependent_index)

        for dependency_name in kext.requires_kexts:
            dependency_index = kext_data.kext_index_by_name.get(dependency_name)
            dependency = self.kexts[dependency_index]
            if not dependency.required and not any(other_kext.checked and dependency_name in other_kext.requires_kexts for other_kext in self.kexts):
                self.uncheck_kext(dependency_index)

        return True

    def verify_kext_compatibility(self, kext_names, target_darwin_version):
        incompatible_kexts = []

        for kext_name in dict.fromkeys(kext_names):
            kext_index = kext_data.kext_index_by_name.get(kext_name)
            if kext_index is None:
                continue

            kext = self.kexts[kext_index]
            if not self.is_kext_compatible(kext, target_darwin_version) and kext.allow_force_load:
                incompatible_kexts.append((kext.name, "Lilu" in kext.requires_kexts))

        if not incompatible_kexts:
            return set()
        
        while True:
            self.utils.head("Kext 兼容性检查")
            print("\n当前 macOS 版本 ({}) 不兼容的 kext:\n".format(target_darwin_version))
            
            for index, (kext_name, is_lilu_dependent) in enumerate(incompatible_kexts, start=1):
                print("{:2}. {:25}{}".format(index, kext_name, " - Lilu 插件" if is_lilu_dependent else ""))
            
            print("\n\033[1;93m注意:\033[0m")
            print("- 对于 Lilu 插件，使用 \"-lilubetaall\" 启动参数会强制加载它们。")
            print("- 强制使用不支持的 kext 可能导致系统不稳定。\033[0;31m请谨慎操作。\033[0m")
            print("")
            
            option = self.utils.request_input("是否在不支持的 macOS 版本上强制加载{}? (是/否): ".format("这些 kext" if len(incompatible_kexts) > 1 else "此 kext"))
            
            if option.strip().lower() in ("yes", "y", "是"):
                return {kext_name for kext_name, _ in incompatible_kexts}
            elif option.strip().lower() in ("no", "n", "否"):
                return set()

    def kext_configuration_menu(self, macos_version):
        current_category = None
        messages = []

        while True:
            contents = []
            contents.append("")
            contents.append("可用 kext 列表:")

            for index, kext in enumerate(self.kexts, start=1):
                if kext.category != current_category:
                    current_category = kext.category
                    category_header = "类别: {}".format(self.category_labels.get(current_category, current_category) if current_category else "未分类")
                    contents.append(f"\n{category_header}\n" + "=" * len(category_header))
                status = self.get_kext_status(kext, macos_version)
                marker = {
                    "required": "[R]",
                    "selected": "[*]",
                    "force_loaded": "[!]",
                    "available": "[ ]",
                    "force_available": "[?]",
                    "not_needed": "[-]",
                    "requires_newer": "[-]"
                }[status]
                line = "{} {:2}. {:35} - {:60}".format(marker, index, kext.name, kext.description)
                if status in ("required", "selected"):
                    line = "\033[1;32m{}\033[0m".format(line)
                elif status == "force_loaded":
                    line = "\033[1;93m{}\033[0m".format(line)
                elif status in ("not_needed", "requires_newer"):
                    line = "\033[90m{}\033[0m".format(line)
                contents.append(line)
            contents.append("")
            contents.append("\033[1;93m状态:\033[0m")
            contents.append("- [R] 必需   [*] 已选择   [!] 已强制加载")
            contents.append("- [ ] 可用   [?] 可强制加载   [-] 不可用")
            contents.append("- 选择某个 kext 的插件时，整个 kext 会自动选中。")
            contents.append("- 可以输入以逗号分隔的编号来选择多个 kext（例如 '1, 2, 3'）。")
            contents.append("")
            contents.append("B. 返回")
            contents.append("Q. 退出")
            if messages:
                contents.append("")
                contents.extend("\033[1;96m{}\033[0m".format(message) for message in messages)
                messages = []
            contents.append("")
            content = "\n".join(contents)

            self.utils.adjust_window_size(content)
            self.utils.head("配置内核扩展", resize=False)
            print(content)
            option = self.utils.request_input("选择一个选项: ")
            if option.strip().lower() in ("b", "返回"):
                return
            if option.strip().lower() in ("q", "退出"):
                self.utils.exit_program()
            indices = [int(i.strip()) - 1 for i in option.split(",") if i.strip().isdigit()]
            indices = [index for index in indices if 0 <= index < len(self.kexts)]
            kexts_to_enable = [self.kexts[index].name for index in indices if not self.kexts[index].checked]

            force_load_kexts = self.verify_kext_compatibility(kexts_to_enable, macos_version)
    
            for index in indices:
                kext = self.kexts[index]
                if kext.checked and not kext.required:
                    checked_dependents = [other_kext.name for other_kext in self.kexts if other_kext.checked and kext.name in other_kext.requires_kexts]
                    self.uncheck_kext(index)
                    if checked_dependents:
                        messages.append("已禁用 {} 和依赖它的 kext: {}。".format(kext.name, ", ".join(checked_dependents)))
                else:
                    status = self.get_kext_status(kext, macos_version)
                    if status == "not_needed":
                        messages.append("{} 在 {} 中不需要。".format(kext.name, os_data.get_macos_name_by_darwin(macos_version)))
                        continue
                    if status == "requires_newer":
                        messages.append("{} 需要更新的 macOS 版本。".format(kext.name))
                        continue

                    conflicts = [other_kext.name for other_kext in self.kexts if other_kext.checked and kext.conflict_group_id and other_kext.conflict_group_id == kext.conflict_group_id and other_kext.name != kext.name]
                    if self.check_kext(index, macos_version, kext.name in force_load_kexts):
                        if conflicts:
                            messages.append("已选择 {}；互斥 kext 已禁用: {}。".format(kext.name, ", ".join(conflicts)))
                    else:
                        messages.append("无法选择 {}，因为所需的依赖项不可用。".format(kext.name))

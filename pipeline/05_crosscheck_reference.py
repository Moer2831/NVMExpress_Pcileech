#!/usr/bin/env python3
"""
NVM_Express_Pcileech_FPGA_75T
discord: Moer2831
community: https://discord.gg/sXcQhxa8qy

Cross-validate all extracted build inputs for consistency before firmware build.

Checks:
- Config space IDs match pcie_ids.json
- Identify Controller VID matches config space VID
- NVMe version consistency (Identify vs BAR0 VS register)
- Capability chain integrity (no loops, valid pointers)
- MSI-X table geometry fits within BAR0
- Namespace capacity produces ~256GB

Usage:
    python 05_crosscheck_reference.py
"""

import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_INPUTS_DIR = os.path.join(SCRIPT_DIR, "build_inputs")


def load_json(filename):
    """Load JSON from build_inputs directory."""
    path = os.path.join(BUILD_INPUTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_bin(filename):
    """Load binary from build_inputs directory."""
    path = os.path.join(BUILD_INPUTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


class Validator:
    def __init__(self):
        self.passes = 0
        self.fails = 0
        self.warnings = 0
        self.skipped = 0

    def check(self, name, condition, detail=""):
        suffix = f" -- {detail}" if detail else ""
        if condition:
            self.passes += 1
            print(f"  [PASS] {name}{suffix}")
        else:
            self.fails += 1
            print(f"  [FAIL] {name}{suffix}")

    def warn(self, name, condition, detail=""):
        suffix = f" -- {detail}" if detail else ""
        if condition:
            self.passes += 1
            print(f"  [PASS] {name}{suffix}")
        else:
            self.warnings += 1
            print(f"  [WARN] {name}{suffix}")

    def skip(self, name, reason="data not available"):
        self.skipped += 1
        print(f"  [SKIP] {name} -- {reason}")

    def summary(self):
        total = self.passes + self.fails + self.warnings + self.skipped
        print(f"\n{'=' * 60}")
        print(f"Validation Summary: {self.passes}/{total} passed, "
              f"{self.fails} failed, {self.warnings} warnings, {self.skipped} skipped")
        if self.fails == 0:
            print("STATUS: READY for COE generation")
        else:
            print("STATUS: ISSUES FOUND -- fix before proceeding")
        print(f"{'=' * 60}")
        return self.fails == 0


def main():
    print("=" * 60)
    print("Cross-Validation of Build Inputs")
    print("=" * 60)

    v = Validator()

    # Load all available data
    pcie_ids = load_json("pcie_ids.json")
    cfg_parsed = load_json("config_space_parsed.json")
    cfg_bin = load_bin("config_space_4k.bin")
    nvme_regs = load_json("nvme_registers_parsed.json")
    bar_info = load_json("bar_info.json")
    msix_parsed = load_json("msix_parsed.json")
    id_ctrl = load_json("identify_controller_parsed.json")
    id_ns = load_json("identify_namespace_parsed.json")
    id_ctrl_bin = load_bin("identify_controller.bin")
    id_ns_bin = load_bin("identify_namespace.bin")

    # --- File Availability ---
    print("\n[1] File Availability:")
    v.check("pcie_ids.json exists", pcie_ids is not None)
    v.check("config_space_4k.bin exists", cfg_bin is not None)
    v.check("config_space_parsed.json exists", cfg_parsed is not None)
    v.check("identify_controller.bin exists", id_ctrl_bin is not None)
    v.check("identify_namespace.bin exists", id_ns_bin is not None)
    v.warn("nvme_bar0.bin / nvme_registers_parsed.json exists", nvme_regs is not None)
    v.warn("bar_info.json exists", bar_info is not None)

    if id_ctrl_bin:
        v.check("identify_controller.bin is 4096 bytes", len(id_ctrl_bin) == 4096, f"got {len(id_ctrl_bin)}")
    if id_ns_bin:
        v.check("identify_namespace.bin is 4096 bytes", len(id_ns_bin) == 4096, f"got {len(id_ns_bin)}")
    if cfg_bin:
        v.check("config_space_4k.bin is 4096 bytes", len(cfg_bin) == 4096, f"got {len(cfg_bin)}")

    # --- PCIe ID Consistency ---
    print("\n[2] PCIe ID Consistency:")
    if pcie_ids:
        expected_vid = int(pcie_ids.get("VendorID", "0"), 16)
        expected_did = int(pcie_ids.get("DeviceID", "0"), 16)
        expected_rev = int(pcie_ids.get("RevisionID", "0"), 16)
        expected_svid = int(pcie_ids.get("SubsysVenID", "0"), 16)
        expected_ssid = int(pcie_ids.get("SubsystemID", "0"), 16)

        v.check("VendorID = 0x126F", expected_vid == 0x126F, f"got 0x{expected_vid:04X}")
        v.check("DeviceID = 0x2263", expected_did == 0x2263, f"got 0x{expected_did:04X}")
        v.check("RevisionID = 0x03", expected_rev == 0x03, f"got 0x{expected_rev:02X}")

        if cfg_bin:
            cfg_vid = struct.unpack_from("<H", cfg_bin, 0)[0]
            cfg_did = struct.unpack_from("<H", cfg_bin, 2)[0]
            cfg_rev = cfg_bin[8]
            cfg_svid = struct.unpack_from("<H", cfg_bin, 0x2C)[0]
            cfg_ssid = struct.unpack_from("<H", cfg_bin, 0x2E)[0]

            v.check("Config space VID matches pcie_ids.json",
                   cfg_vid == expected_vid,
                   f"cfg=0x{cfg_vid:04X} vs json=0x{expected_vid:04X}")
            v.check("Config space DID matches pcie_ids.json",
                   cfg_did == expected_did,
                   f"cfg=0x{cfg_did:04X} vs json=0x{expected_did:04X}")
            v.check("Config space RevID matches pcie_ids.json",
                   cfg_rev == expected_rev,
                   f"cfg=0x{cfg_rev:02X} vs json=0x{expected_rev:02X}")
            v.check("Config space SubsysVID matches pcie_ids.json",
                   cfg_svid == expected_svid,
                   f"cfg=0x{cfg_svid:04X} vs json=0x{expected_svid:04X}")
            v.check("Config space SubsysID matches pcie_ids.json",
                   cfg_ssid == expected_ssid,
                   f"cfg=0x{cfg_ssid:04X} vs json=0x{expected_ssid:04X}")

        if id_ctrl:
            v.check("Identify VID matches config space VID",
                   id_ctrl.get("VID") == expected_vid,
                   f"identify=0x{id_ctrl.get('VID', 0):04X} vs expected=0x{expected_vid:04X}")
            v.check("Identify SSVID matches config space SubsysVID",
                   id_ctrl.get("SSVID") == expected_svid,
                   f"identify=0x{id_ctrl.get('SSVID', 0):04X} vs expected=0x{expected_svid:04X}")

    # --- Class Code ---
    print("\n[3] Class Code:")
    if cfg_bin:
        class_code = (cfg_bin[11] << 16) | (cfg_bin[10] << 8) | cfg_bin[9]
        v.check("Class code = 0x010802 (NVMe)", class_code == 0x010802, f"got 0x{class_code:06X}")

    # --- BAR Configuration ---
    print("\n[4] BAR Configuration:")
    if cfg_bin:
        bar0_lo = struct.unpack_from("<I", cfg_bin, 0x10)[0]
        bar0_hi = struct.unpack_from("<I", cfg_bin, 0x14)[0]
        v.check("BAR0 is memory type", (bar0_lo & 1) == 0)
        v.check("BAR0 is 64-bit", (bar0_lo & 0x6) == 0x4, f"type bits = 0x{(bar0_lo & 0x6) >> 1:X}")
        bar0_addr = ((bar0_hi << 32) | (bar0_lo & 0xFFFFFFF0))
        v.check("BAR0 address non-zero (device is mapped)", bar0_addr != 0, f"addr=0x{bar0_addr:016X}")

        # Check BAR1-5 are unused (typical for NVMe)
        for i in range(2, 6):
            bar_val = struct.unpack_from("<I", cfg_bin, 0x10 + i * 4)[0]
            v.warn(f"BAR{i} is unused", bar_val == 0, f"got 0x{bar_val:08X}")

    # --- Capability Chain Integrity ---
    print("\n[5] Capability Chain Integrity:")
    if cfg_parsed:
        caps = cfg_parsed.get("capabilities", [])
        v.check("Capability chain has entries", len(caps) > 0, f"found {len(caps)}")

        # Check for required capabilities
        cap_ids = {c.get("id_int", 0) for c in caps}
        v.check("Has PM capability (0x01)", 0x01 in cap_ids)
        v.check("Has PCIe capability (0x10)", 0x10 in cap_ids)
        v.warn("Has MSI capability (0x05)", 0x05 in cap_ids)
        v.warn("Has MSI-X capability (0x11)", 0x11 in cap_ids)

        # Check chain is well-formed (no loops, all within 0x00-0xFF)
        offsets = [c.get("offset_int", 0) for c in caps]
        v.check("No duplicate capability offsets", len(offsets) == len(set(offsets)))
        v.check("All capability offsets in range 0x40-0xFF",
               all(0x40 <= o <= 0xFF for o in offsets),
               f"offsets: {[f'0x{o:02X}' for o in offsets]}")

    # --- Extended Capability Chain ---
    if cfg_parsed:
        ext_caps = cfg_parsed.get("extended_capabilities", [])
        v.warn("Extended capability chain has entries", len(ext_caps) > 0, f"found {len(ext_caps)}")
        if ext_caps:
            ext_offsets = [c.get("offset_int", 0) for c in ext_caps]
            v.check("All ext cap offsets >= 0x100",
                   all(o >= 0x100 for o in ext_offsets))
            v.check("No duplicate ext cap offsets", len(ext_offsets) == len(set(ext_offsets)))

    # --- MSI-X Geometry ---
    print("\n[6] MSI-X Configuration:")
    if cfg_parsed:
        msix_cap = None
        for cap in cfg_parsed.get("capabilities", []):
            if cap.get("id_int") == 0x11:
                msix_cap = cap
                break

        if msix_cap:
            table_size = msix_cap.get("table_size", 0)
            table_bir = msix_cap.get("table_BIR", -1)
            table_offset = msix_cap.get("table_offset_int", 0)
            pba_bir = msix_cap.get("PBA_BIR", -1)
            pba_offset = msix_cap.get("PBA_offset_int", 0)

            v.check("MSI-X table in BAR0", table_bir == 0, f"BIR={table_bir}")
            v.check("MSI-X PBA in BAR0", pba_bir == 0, f"BIR={pba_bir}")
            v.check("MSI-X table size > 0", table_size > 0, f"size={table_size}")
            v.check("MSI-X table offset valid", table_offset > 0, f"offset=0x{table_offset:X}")
            v.check("MSI-X PBA offset > table end",
                   pba_offset >= table_offset + table_size * 16,
                   f"PBA=0x{pba_offset:X}, table_end=0x{table_offset + table_size * 16:X}")

            if bar_info:
                bar0_size = bar_info.get("estimated_size", 0)
                if bar0_size > 0:
                    pba_size = ((table_size + 63) // 64) * 8
                    v.check("BAR0 size fits MSI-X table+PBA",
                           bar0_size >= pba_offset + pba_size,
                           f"BAR0={bar0_size}B, need>={pba_offset + pba_size}B")
        else:
            v.skip("MSI-X checks", "no MSI-X capability found")

    # --- NVMe Version Consistency ---
    print("\n[7] NVMe Version Consistency:")
    if nvme_regs and id_ctrl:
        bar0_vs = nvme_regs.get("VS", {}).get("raw", "0x0")
        id_ver = id_ctrl.get("VER", "0x0")
        v.warn("NVMe version: BAR0 VS matches Identify VER",
              bar0_vs == id_ver,
              f"BAR0={bar0_vs}, Identify={id_ver}")
    elif id_ctrl:
        v.skip("Version cross-check", "BAR0 register data not available")

    # --- Identify Data Integrity ---
    print("\n[8] Identify Data Integrity:")
    if id_ctrl:
        v.check("Model contains 'NE-256'", "NE-256" in id_ctrl.get("MN", ""))
        v.check("Serial non-empty", len(id_ctrl.get("SN", "").strip()) > 0)
        v.check("SQES = 0x66", id_ctrl.get("SQES") == 0x66, f"got 0x{id_ctrl.get('SQES', 0):02X}")
        v.check("CQES = 0x44", id_ctrl.get("CQES") == 0x44, f"got 0x{id_ctrl.get('CQES', 0):02X}")
        v.check("NN >= 1", id_ctrl.get("NN", 0) >= 1, f"got {id_ctrl.get('NN', 0)}")
        v.check("MDTS > 0", id_ctrl.get("MDTS", 0) > 0, f"got {id_ctrl.get('MDTS', 0)}")

    if id_ns:
        nsze = id_ns.get("NSZE", 0)
        lba_size = id_ns.get("LBASize", 512)
        capacity_gb = (nsze * lba_size) / (1000 ** 3) if nsze and lba_size else 0
        v.check("NSZE > 0", nsze > 0, f"got {nsze}")
        v.check("LBA size valid (512 or 4096)", lba_size in (512, 4096), f"got {lba_size}")
        v.check("Capacity ~256GB (200-300)", 200 < capacity_gb < 300, f"got {capacity_gb:.2f} GB")

    # --- Output Summary ---
    ok = v.summary()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()



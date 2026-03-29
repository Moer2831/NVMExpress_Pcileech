<div align="center">

# NVM_Express_Pcileech_FPGA_75T

<p><strong>Compact 75T NVMe FPGA project with a cleaned workflow, structured build inputs, and engineering-style runtime documentation.</strong></p>

[![Discord](https://img.shields.io/badge/Discord-Moer2831-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/sXcQhxa8qy)
[![Community](https://img.shields.io/badge/Community-Join%20Server-0A66C2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/sXcQhxa8qy)
[![Chinese](https://img.shields.io/badge/Docs-%E4%B8%AD%E6%96%87-0F766E?style=for-the-badge&logo=readthedocs&logoColor=white)](./README.zh-CN.md)

![Platform](https://img.shields.io/badge/Platform-Artix--7%2075T-111827?style=flat-square)
![Protocol](https://img.shields.io/badge/Protocol-NVMe-065F46?style=flat-square)
![Toolchain](https://img.shields.io/badge/Toolchain-Vivado-B45309?style=flat-square)
![Pipeline](https://img.shields.io/badge/Pipeline-build__inputs-1D4ED8?style=flat-square)

</div>

> Contact: [Moer2831](https://discord.gg/sXcQhxa8qy)  
> Community: [discord.gg/sXcQhxa8qy](https://discord.gg/sXcQhxa8qy)  
> Language: [English](./README.md) | [中文](./README.zh-CN.md)

## Snapshot

- Board project directory: `NVM_Express_Pcileech_FPGA_75T/`
- Vivado project name: `NVM_Express_Pcileech_FPGA_75T`
- Top module: `nvm_express_pcileech_fpga_75t_top`
- Build output: `NVM_Express_Pcileech_FPGA_75T/NVM_Express_Pcileech_FPGA_75T.bin`
- Build inputs workspace: `pipeline/build_inputs/`

## Repository Layout

```text
NVM_Express_Pcileech_FPGA_75T/
  src/
    nvm_express_pcileech_fpga_75t_top.sv
    nvm_express_pcileech_fpga_75t.xdc
    pcileech_com.sv
    pcileech_fifo.sv
    pcileech_pcie_a7.sv
    pcileech_tlps128_cfgspace_shadow.sv
    pcileech_tlps128_bar_controller.sv
    pcileech_nvme_controller.sv
    pcileech_nvme_engine.sv
  ip/
    nvmexp_cfgspace.coe
    nvmexp_cfgspace_mask.coe
    nvmexp_bar0.coe
    nvme_identify_ctrl.hex
    nvme_identify_ns.hex
  vivado_generate_project_75t.tcl
  vivado_configure_profile_75t.tcl
  vivado_build_75t.tcl

pipeline/
  01_capture_config_profile.py
  02_capture_bar0_profile.py
  03_verify_identity_profile.py
  04_build_init_images.py
  05_crosscheck_reference.py
  target_collect_configspace.ps1
  target_collect_profile.ps1
  build_inputs/
```

## Module Roles

| Module | Role | Key Interfaces |
| --- | --- | --- |
| `nvm_express_pcileech_fpga_75t_top` | Board shell, reset generation, LED wiring, FT601 and PCIe top-level hookup | `clk`, `ft601_clk`, PCIe fabric, FT601 pads |
| `pcileech_com` | FT601 communication bridge, 32-bit to 64-bit packing, clock crossing into system domain | `clk_com -> clk`, `IfComToFifo` |
| `pcileech_fifo` | Command/TLP/config routing between communication core and PCIe subsystems | `IfComToFifo`, `IfPCIeFifo*`, `IfShadow2Fifo` |
| `pcileech_pcie_a7` | PCIe endpoint wrapper, link-stable gating, PCIe user clock domain, NVMe TX mux integration | Xilinx `pcie_7x_0`, `IfAXIS128` |
| `pcileech_tlps128_cfgspace_shadow` | Shadow config-space BRAM path for config read/write forwarding and host-side replay | config TLP stream, shadow FIFO |
| `pcileech_tlps128_bar_controller` | BAR read/write engines, BAR dispatch, BAR0 NVMe register file hookup | BAR TLP decode, read/write engines |
| `pcileech_nvme_controller` | BAR0 register map, CC/CSTS/AQA/ASQ/ACQ handling, doorbells, MSI-X table storage | BAR0 accesses, engine control outputs |
| `pcileech_nvme_engine` | Admin SQ fetch, command parse/execute, DMA write generation, CQ writeback, MSI-X trigger | raw RX completions, TX AXIS output |

## Build Inputs Workflow

The build pipeline is split from the FPGA project for a clearer data-to-build flow.

```mermaid
flowchart LR
    A["Target / Reader Capture"] --> B["pipeline/build_inputs"]
    B --> C["01_capture_config_profile.py"]
    B --> D["02_capture_bar0_profile.py"]
    B --> E["03_verify_identity_profile.py"]
    C --> F["04_build_init_images.py"]
    D --> F
    E --> F
    B --> G["05_crosscheck_reference.py"]
    F --> H["NVM_Express_Pcileech_FPGA_75T/ip"]
    H --> I["Vivado Generate / Configure / Build"]
```

## Runtime Architecture

At runtime the FPGA design splits into three practical planes:

- Communication plane: FT601 host bridge and FIFO transport.
- PCIe endpoint plane: Xilinx PCIe core, config-space shadowing, and BAR TLP handling.
- NVMe emulation plane: BAR0 register file, admin queue engine, completion generation, and MSI-X signaling.

```mermaid
flowchart LR
    subgraph Host["Host System"]
        H1["PCIe Root Complex / NVMe Driver"]
        H2["FT601 Control Host"]
    end

    subgraph FPGA["NVM_Express_Pcileech_FPGA_75T"]
        subgraph Comm["Communication Plane"]
            C1["pcileech_com"]
            C2["pcileech_fifo"]
        end

        subgraph EP["PCIe Endpoint Plane"]
            P1["pcileech_pcie_a7"]
            P2["pcileech_pcie_cfg_a7"]
            P3["pcileech_pcie_tlp_a7"]
            P4["pcileech_tlps128_cfgspace_shadow"]
            P5["pcileech_tlps128_bar_controller"]
        end

        subgraph NVMe["NVMe Emulation Plane"]
            N1["pcileech_nvme_controller"]
            N2["pcileech_nvme_engine"]
        end
    end

    H2 <-- FT601 --> C1
    C1 <--> C2
    H1 <-- PCIe --> P1
    C2 <--> P2
    C2 <--> P3
    C2 <--> P4
    P1 --> P2
    P1 --> P3
    P3 --> P4
    P3 --> P5
    P5 --> N1
    N1 --> N2
    N2 --> P3
```

## Detailed Runtime Workflow

### 1. Board Bring-Up

- `nvm_express_pcileech_fpga_75t_top` generates reset and wires FT601, FIFO, and PCIe blocks.
- `pcileech_pcie_a7` holds the PCIe subsystem behind a link-stable delay gate.
- The holdoff prevents early bus-master activity before the platform is ready.

### 2. Config-Space Handling

- Standard config accesses are mediated by the Xilinx PCIe core and management path.
- Forwarded config TLPs can be served by `pcileech_tlps128_cfgspace_shadow`.
- Shadow config contents live in BRAM initialized from `nvmexp_cfgspace.coe` plus the write-mask image.

### 3. BAR0 Register Handling

- BAR memory TLPs are classified by `pcileech_tlps128_bar_controller`.
- BAR0 requests are routed into `pcileech_nvme_controller`.
- BAR0 reads return emulated controller values, while BAR0 writes update control registers, queue pointers, doorbells, and MSI-X table entries.

### 4. Admin Queue Execution

- When the host updates SQ0 tail, `pcileech_nvme_controller` raises `admin_sq_db_written`.
- `pcileech_nvme_engine` issues an MRd to fetch the 64-byte submission queue entry.
- CplD beats are collected into the local SQ buffer.
- The engine parses opcode, PRP1, CDW10, and CDW11.
- Depending on the command, the engine selects an internal response source, emits DMA writes, writes a CQ entry, and optionally emits an MSI-X write.

### 5. Data Return Paths

- Identify payloads are sourced from `nvme_identify_ctrl.hex` and `nvme_identify_ns.hex`.
- Config-space shadow data is BRAM-backed.
- BAR0 state is register-backed.
- TLP responses are multiplexed back into the PCIe transmit stream through `pcileech_pcie_tlp_a7`.

## Clock Domains

| Domain | Source | Main Blocks | Purpose |
| --- | --- | --- | --- |
| `clk` | 100 MHz board clock | `pcileech_fifo`, top-level control, FT601 system-side buffering | system control plane |
| `ft601_clk` / `clk_com` | FT601 interface clock | `pcileech_com`, `pcileech_ft601` | communication I/O domain |
| `clk_pcie` | PCIe user clock from `pcie_7x_0` | `pcileech_pcie_a7`, config shadow, BAR engine, NVMe engine | live PCIe transaction domain |

## Transaction Timing

The most important runtime path is the admin command loop. The diagram below maps the actual engine flow used for Identify, Get Log Page, and similar admin commands.

```mermaid
sequenceDiagram
    participant Host as Host / NVMe Driver
    participant BAR0 as NVMe BAR0 Controller
    participant ENG as NVMe Engine
    participant PCIe as PCIe TX/RX Path

    Host->>BAR0: Write SQ0 doorbell
    BAR0->>ENG: Pulse admin_sq_db_written
    ENG->>PCIe: MRd 64B for SQ entry
    PCIe-->>ENG: CplD beats with SQE payload
    ENG->>ENG: Parse opcode and command fields

    alt Data payload required
        loop One or more MWr TLPs
            ENG->>PCIe: MWr header + payload beats
            PCIe-->>Host: DMA response data
        end
    end

    ENG->>PCIe: MWr completion queue entry
    PCIe-->>Host: CQE writeback

    opt MSI-X enabled and vector unmasked
        ENG->>PCIe: MWr MSI-X message
        PCIe-->>Host: Interrupt write
    end

    ENG->>ENG: Advance SQ head / CQ tail
```

## NVMe Engine State Flow

The internal admin engine in `pcileech_nvme_engine.sv` uses the following execution chain:

```mermaid
stateDiagram-v2
    [*] --> ST_IDLE
    ST_IDLE --> ST_FETCH_SQ: sq_has_pending
    ST_FETCH_SQ --> ST_WAIT_CPLD
    ST_WAIT_CPLD --> ST_PARSE_CMD: 64B SQE assembled
    ST_WAIT_CPLD --> ST_ADVANCE_HEAD: timeout / skip
    ST_PARSE_CMD --> ST_EXECUTE
    ST_EXECUTE --> ST_DMA_WRITE: data payload needed
    ST_EXECUTE --> ST_CQ_HDR: CQ only
    ST_EXECUTE --> ST_ADVANCE_HEAD: no CQ path
    ST_DMA_WRITE --> ST_DMA_HDR
    ST_DMA_HDR --> ST_DMA_DATA
    ST_DMA_DATA --> ST_DMA_WRITE: more payload remains
    ST_DMA_DATA --> ST_CQ_HDR: payload done
    ST_CQ_HDR --> ST_CQ_DATA
    ST_CQ_DATA --> ST_SEND_MSIX
    ST_SEND_MSIX --> ST_MSIX_HDR: vector active
    ST_SEND_MSIX --> ST_ADVANCE_HEAD: masked / disabled
    ST_MSIX_HDR --> ST_MSIX_DATA
    ST_MSIX_DATA --> ST_ADVANCE_HEAD
    ST_ADVANCE_HEAD --> ST_IDLE
```

## Build Flow

### 1. Generate the Vivado project

```tcl
cd NVM_Express_Pcileech_FPGA_75T
source vivado_generate_project_75t.tcl -notrace
```

### 2. Apply the PCIe profile configuration

```tcl
source vivado_configure_profile_75t.tcl
```

### 3. Build the bitstream

```tcl
source vivado_build_75t.tcl -notrace
```

Expected output:

```text
NVM_Express_Pcileech_FPGA_75T/NVM_Express_Pcileech_FPGA_75T.bin
```

## Build Inputs Commands

Place reference files in:

```text
pipeline/build_inputs/
```

Then run:

```powershell
python pipeline/01_capture_config_profile.py
python pipeline/02_capture_bar0_profile.py
python pipeline/03_verify_identity_profile.py
python pipeline/04_build_init_images.py
python pipeline/05_crosscheck_reference.py
```



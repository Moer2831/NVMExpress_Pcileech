// pcileech_nvme_engine.sv
// ============================================================
// NVM_Express_Pcileech_FPGA_75T
// discord: Moer2831
// community: https://discord.gg/sXcQhxa8qy
// NVMe Admin Queue Engine & DMA/TLP Generator
// Companion to pcileech_nvme_controller.sv (BAR0 register file).
//
// Handles:
//   - Admin Submission Queue fetch (MRd 64B from host RAM)
//   - Admin command parsing and execution
//   - DMA write of response data (Identify 4KB, CQ entries 16B)
//   - MSI-X interrupt generation
//   - I/O queue creation tracking
//
// TLP format: 128-bit AXI-Stream, DW0 at [127:96], DW3 at [31:0]
// PCIe clock: 62.5 MHz
// Max Payload Size: 128 bytes (32 DWORDs)
// ============================================================

`timescale 1ns / 1ps

module pcileech_nvme_engine(
    input               rst,
    input               clk,            // clk_pcie (62.5 MHz)

    // From NVMe controller (register file)
    input               ctrl_enabled,   // CC.EN=1 and CSTS.RDY=1
    input  [63:0]       admin_sq_base,  // ASQ register value
    input  [63:0]       admin_cq_base,  // ACQ register value
    input  [15:0]       admin_sq_size,  // ASQS from AQA (0-based)
    input  [15:0]       admin_cq_size,  // ACQS from AQA (0-based)
    input  [15:0]       admin_sq_tail,  // From doorbell register
    input               admin_sq_db_written, // Pulse when SQ0 doorbell written

    // I/O queue management outputs (for I/O engine)
    output reg [8:0]    io_cq_created,  // Bitmask: which I/O CQs exist (QID 1-8 -> bits 1-8)
    output reg [8:0]    io_sq_created,  // Bitmask: which I/O SQs exist
    // I/O CQ parameters (indexed by QID 1-8)
    output reg [63:0]   io_cq_base [1:8],
    output reg [15:0]   io_cq_size [1:8],
    output reg [3:0]    io_cq_iv   [1:8],   // Interrupt vector
    // I/O SQ parameters
    output reg [63:0]   io_sq_base [1:8],
    output reg [15:0]   io_sq_size [1:8],
    output reg [3:0]    io_sq_cqid [1:8],   // Associated CQID

    // MSI-X table access (from NVMe controller BAR0 storage)
    input  [31:0]       msix_addr_lo  [0:15],  // Message address low per vector
    input  [31:0]       msix_addr_hi  [0:15],  // Message address high per vector
    input  [31:0]       msix_data     [0:15],  // Message data per vector
    input  [31:0]       msix_vec_ctrl [0:15],  // Vector control (bit 0 = masked)

    // TLP TX output (connects to sink_mux as a source)
    output reg [127:0]  tx_tdata,
    output reg [3:0]    tx_tkeepdw,
    output reg [8:0]    tx_tuser,       // [0]=first, [1]=last
    output reg          tx_tvalid,
    output reg          tx_tlast,
    input               tx_tready,
    output reg          tx_has_data,

    // TLP RX input (filtered CplD completions for our tags)
    input  [127:0]      rx_tdata,
    input  [3:0]        rx_tkeepdw,
    input               rx_tvalid,
    input               rx_tlast,
    input  [8:0]        rx_tuser,

    // PCIe identity
    input  [15:0]       pcie_id         // Our Bus/Dev/Fun (Requester ID)
);

    // ================================================================
    // NVMe ADMIN OPCODES
    // ================================================================
    localparam [7:0] OPC_DELETE_IO_SQ     = 8'h00;
    localparam [7:0] OPC_CREATE_IO_SQ     = 8'h01;
    localparam [7:0] OPC_GET_LOG_PAGE     = 8'h02;
    localparam [7:0] OPC_DELETE_IO_CQ     = 8'h04;
    localparam [7:0] OPC_CREATE_IO_CQ     = 8'h05;
    localparam [7:0] OPC_IDENTIFY         = 8'h06;
    localparam [7:0] OPC_ABORT            = 8'h08;
    localparam [7:0] OPC_SET_FEATURES     = 8'h09;
    localparam [7:0] OPC_GET_FEATURES     = 8'h0A;
    localparam [7:0] OPC_ASYNC_EVENT_REQ  = 8'h0C;

    // NVMe Feature IDs
    localparam [7:0] FID_ARBITRATION       = 8'h01;
    localparam [7:0] FID_POWER_MGMT       = 8'h02;
    localparam [7:0] FID_TEMP_THRESHOLD   = 8'h04;
    localparam [7:0] FID_ERROR_RECOVERY   = 8'h05;
    localparam [7:0] FID_VOLATILE_WC      = 8'h06;
    localparam [7:0] FID_NUM_QUEUES       = 8'h07;
    localparam [7:0] FID_INT_COALESCING   = 8'h08;
    localparam [7:0] FID_INT_VEC_CONFIG   = 8'h09;
    localparam [7:0] FID_WRITE_ATOMICITY  = 8'h0A;
    localparam [7:0] FID_ASYNC_EVENT_CFG  = 8'h0B;
    localparam [7:0] FID_APST             = 8'h0C;

    // GetLogPage Log Identifiers
    localparam [7:0] LID_ERROR_INFO       = 8'h01;
    localparam [7:0] LID_SMART_HEALTH     = 8'h02;
    localparam [7:0] LID_FW_SLOT          = 8'h03;
    localparam [7:0] LID_CMD_EFFECTS      = 8'h05;

    // Max payload = 128 bytes = 32 DWORDs
    localparam [5:0] MPS_DWORDS = 6'd32;

    // ================================================================
    // IDENTIFY DATA BRAMs (4KB each, loaded from hex files)
    // ================================================================
    // Note: These ROMs are read combinationally (up to 4 addresses per clock
    // for DMA data beats), which requires distributed RAM rather than block RAM.
    // Each 4KB ROM uses approximately 1024 LUTs in distributed RAM mode.
    // If LUT pressure becomes an issue, the DMA data path can be serialized
    // to use block RAM with a registered read port instead.
    (* ram_style = "distributed" *) reg [31:0] identify_ctrl_rom [0:1023];
    (* ram_style = "distributed" *) reg [31:0] identify_ns_rom   [0:1023];

    initial $readmemh("nvme_identify_ctrl.hex", identify_ctrl_rom);
    initial $readmemh("nvme_identify_ns.hex", identify_ns_rom);

    // ================================================================
    // COMMAND EFFECTS LOG (combinational, mostly zeros)
    // ================================================================
    function [31:0] cmd_effects_entry;
        input [9:0] dw_idx;
        begin
            cmd_effects_entry = 32'h0;
            if (dw_idx < 10'd256) begin
                case (dw_idx[7:0])
                    8'h00: cmd_effects_entry = 32'h00000001;
                    8'h01: cmd_effects_entry = 32'h00000001;
                    8'h02: cmd_effects_entry = 32'h00000001;
                    8'h04: cmd_effects_entry = 32'h00000001;
                    8'h05: cmd_effects_entry = 32'h00000001;
                    8'h06: cmd_effects_entry = 32'h00000001;
                    8'h08: cmd_effects_entry = 32'h00000001;
                    8'h09: cmd_effects_entry = 32'h00000001;
                    8'h0A: cmd_effects_entry = 32'h00000001;
                    8'h0C: cmd_effects_entry = 32'h00000001;
                    default: ;
                endcase
            end else if (dw_idx < 10'd512) begin
                case (dw_idx[7:0])
                    8'h00: cmd_effects_entry = 32'h00000001;
                    8'h01: cmd_effects_entry = 32'h00000001;
                    8'h02: cmd_effects_entry = 32'h00000001;
                    default: ;
                endcase
            end
        end
    endfunction

    // ================================================================
    // SMART/HEALTH LOG (combinational, 512B = 128 DWORDs)
    // DW0 = {AvailSpare, TempHi, TempLo, CritWarn} = {0x64, 0x01, 0x64, 0x00}
    // DW1 = {0x00, 0x00, PctUsed=0x00, SpareThresh=0x0A}
    // ================================================================
    function [31:0] smart_log_dword;
        input [6:0] dw_idx;
        begin
            case (dw_idx)
                7'd0:    smart_log_dword = 32'h64016400;
                7'd1:    smart_log_dword = 32'h0000000A;
                default: smart_log_dword = 32'h00000000;
            endcase
        end
    endfunction

    // ================================================================
    // ADMIN QUEUE POINTERS
    // ================================================================
    reg [15:0]  sq_head;
    reg [15:0]  cq_tail;
    reg         cq_phase;

    wire        sq_has_pending = (sq_head != admin_sq_tail);
    wire [15:0] sq_head_next   = (sq_head >= admin_sq_size) ? 16'h0 : (sq_head + 16'h1);

    // ================================================================
    // SQ ENTRY BUFFER (64 bytes = 16 DWORDs)
    // ================================================================
    reg [31:0]  sq_entry [0:15];

    wire [7:0]  cmd_opcode = sq_entry[0][7:0];
    wire [15:0] cmd_cid    = sq_entry[0][31:16];
    wire [63:0] cmd_prp1   = {sq_entry[7], sq_entry[6]};
    wire [31:0] cmd_cdw10  = sq_entry[10];
    wire [31:0] cmd_cdw11  = sq_entry[11];

    // ================================================================
    // CQ ENTRY FIELDS
    // ================================================================
    reg [31:0]  cq_dw0;
    reg [14:0]  cq_status;      // {DNR, M, CRD[1:0], SCT[2:0], SC[7:0]}
    reg         cq_skip;

    // ================================================================
    // DMA STATE
    // ================================================================
    reg [63:0]  dma_addr;
    reg [11:0]  dma_remaining;
    reg [9:0]   dma_rom_offset;
    reg [5:0]   dma_tlp_dw_cnt;
    reg [5:0]   dma_cur_tlp_len;
    reg [1:0]   dma_source;

    // ================================================================
    // COMPLETION RECEIVE STATE
    // ================================================================
    reg [4:0]   cpld_dw_idx;
    reg         cpld_active;
    reg         cpld_tag_matched;

    // ASYNC EVENT REQUEST TRACKING
    reg [2:0]   aer_pending_count;

    // ================================================================
    // FSM
    // ================================================================
    localparam [3:0] ST_IDLE         = 4'd0;
    localparam [3:0] ST_FETCH_SQ     = 4'd1;
    localparam [3:0] ST_WAIT_CPLD    = 4'd2;
    localparam [3:0] ST_PARSE_CMD    = 4'd3;
    localparam [3:0] ST_EXECUTE      = 4'd4;
    localparam [3:0] ST_DMA_WRITE    = 4'd5;
    localparam [3:0] ST_DMA_HDR      = 4'd6;
    localparam [3:0] ST_DMA_DATA     = 4'd7;
    localparam [3:0] ST_CQ_HDR       = 4'd8;
    localparam [3:0] ST_CQ_DATA      = 4'd9;
    localparam [3:0] ST_SEND_MSIX    = 4'd10;
    localparam [3:0] ST_MSIX_HDR     = 4'd11;
    localparam [3:0] ST_MSIX_DATA    = 4'd12;
    localparam [3:0] ST_ADVANCE_HEAD = 4'd13;

    reg [3:0]   state;
    reg [19:0]  cpld_timeout;
    localparam [19:0] CPLD_TIMEOUT_MAX = 20'hFFFFF;

    wire [63:0] sq_entry_addr = admin_sq_base + {42'b0, sq_head, 6'b0};
    wire [63:0] cq_entry_addr = admin_cq_base + {44'b0, cq_tail, 4'b0};

    wire tx_stalled       = tx_tvalid && !tx_tready;
    wire tx_beat_accepted = tx_tvalid && tx_tready;

    // ================================================================
    // MAIN STATE MACHINE
    // ================================================================
    integer i;

    always @(posedge clk) begin
        if (rst) begin
            state               <= ST_IDLE;
            sq_head             <= 16'h0;
            cq_tail             <= 16'h0;
            cq_phase            <= 1'b1;
            tx_tvalid           <= 1'b0;
            tx_has_data         <= 1'b0;
            tx_tlast            <= 1'b0;
            tx_tdata            <= 128'h0;
            tx_tkeepdw          <= 4'h0;
            tx_tuser            <= 9'h0;
            cpld_active         <= 1'b0;
            cpld_dw_idx         <= 5'h0;
            cpld_tag_matched    <= 1'b0;
            io_cq_created       <= 9'h0;
            io_sq_created       <= 9'h0;
            aer_pending_count   <= 3'h0;
            cq_skip             <= 1'b0;
            cq_dw0              <= 32'h0;
            cq_status           <= 15'h0;
            dma_remaining       <= 12'h0;
            dma_rom_offset      <= 10'h0;
            dma_tlp_dw_cnt      <= 6'h0;
            dma_cur_tlp_len     <= 6'h0;
            dma_source          <= 2'h0;
            dma_addr            <= 64'h0;
            cpld_timeout        <= 20'h0;
            for (i = 1; i <= 8; i = i + 1) begin
                io_cq_base[i]   <= 64'h0;
                io_cq_size[i]   <= 16'h0;
                io_cq_iv[i]     <= 4'h0;
                io_sq_base[i]   <= 64'h0;
                io_sq_size[i]   <= 16'h0;
                io_sq_cqid[i]   <= 4'h0;
            end
            for (i = 0; i < 16; i = i + 1)
                sq_entry[i]     <= 32'h0;
        end else if (!ctrl_enabled) begin
            // Controller disabled: reset queue state.
            // CRITICAL: If a multi-beat TLP is in progress (mux has id=5 locked),
            // we must send a tlast beat to release the mux. Otherwise the mux
            // deadlocks permanently waiting for tlast that never comes.
            if (tx_tvalid && !tx_tlast) begin
                // Mid-packet: send a dummy tlast beat to release the mux
                tx_tdata    <= 128'h0;
                tx_tkeepdw  <= 4'b0000;
                tx_tuser    <= {7'b0, 1'b1, 1'b0};  // last=1, first=0
                tx_tlast    <= 1'b1;
                tx_tvalid   <= 1'b1;
                tx_has_data <= 1'b1;
                // Stay in current state until this beat is accepted
            end else begin
                // No in-progress packet (or tlast already sent): safe to reset
                state               <= ST_IDLE;
                sq_head             <= 16'h0;
                cq_tail             <= 16'h0;
                cq_phase            <= 1'b1;
                cpld_active         <= 1'b0;
                aer_pending_count   <= 3'h0;
                tx_tvalid           <= 1'b0;
                tx_has_data         <= 1'b0;
                tx_tlast            <= 1'b0;
                io_cq_created       <= 9'h0;
                io_sq_created       <= 9'h0;
            end
        end else begin

            case (state)

            // ============================================================
            // IDLE
            // ============================================================
            ST_IDLE: begin
                tx_tvalid   <= 1'b0;
                tx_has_data <= 1'b0;
                if (sq_has_pending)
                    state <= ST_FETCH_SQ;
            end

            // ============================================================
            // FETCH_SQ: MRd 64B from ASQ (single-beat TLP)
            // ============================================================
            ST_FETCH_SQ: begin
                if (!tx_tvalid) begin
                    // Drive MRd TLP (single beat: first=1, last=1)
                    tx_tdata[127:96] <= {3'b001, 5'b00000, 1'b0, 3'b000, 4'b0000,
                                         1'b0, 1'b0, 2'b00, 2'b00, 10'd16};
                    tx_tdata[95:64]  <= {pcie_id, 8'hE0, 4'hF, 4'hF};
                    tx_tdata[63:32]  <= sq_entry_addr[63:32];
                    tx_tdata[31:0]   <= {sq_entry_addr[31:2], 2'b00};
                    tx_tkeepdw       <= 4'b1111;
                    tx_tuser         <= {7'b0, 1'b1, 1'b1}; // first=1, last=1
                    tx_tlast         <= 1'b1;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;
                    cpld_dw_idx      <= 5'h0;
                    cpld_active      <= 1'b1;
                    cpld_tag_matched <= 1'b0;
                    cpld_timeout     <= 20'h0;
                end else if (tx_beat_accepted) begin
                    // Beat accepted by mux: deassert immediately and move on
                    tx_tvalid        <= 1'b0;
                    tx_has_data      <= 1'b0;
                    tx_tlast         <= 1'b0;
                    state            <= ST_WAIT_CPLD;
                end
                // else: tx_stalled, hold current values
            end

            // ============================================================
            // WAIT_CPLD: Collect 16 DWORDs of CplD data
            //
            // CplD first beat (3DW hdr + 1 data DW):
            //   [127:96] DW0 = {FMT=010, Type=01010, ...Length}
            //   [95:64]  DW1 = {CompleterID, Status, ByteCount}
            //   [63:32]  DW2 = {RequesterID, Tag, LowerAddr}
            //   [31:0]   DW3 = First data DWORD
            //
            // Continuation beats: up to 4 data DWORDs at [127:96]..[31:0]
            //
            // The 64B MRd may yield 1 or 2 CplD TLPs. We use cpld_dw_idx
            // to track how many DWORDs we've received. On each beat we
            // compute write indices from the current registered cpld_dw_idx
            // and update cpld_dw_idx by the number of valid DWORDs.
            // ============================================================
            ST_WAIT_CPLD: begin
                if (rx_tvalid && cpld_active) begin
                    if (rx_tuser[0]) begin
                        // --- First beat of a CplD packet ---
                        if (rx_tdata[63:56] == 8'hE0) begin
                            cpld_tag_matched <= 1'b1;
                            // 1 data DWORD at [31:0]
                            if (cpld_dw_idx < 5'd16) begin
                                sq_entry[cpld_dw_idx[3:0]] <= rx_tdata[31:0];
                                cpld_dw_idx <= cpld_dw_idx + 5'd1;
                            end
                        end else begin
                            cpld_tag_matched <= 1'b0;
                        end
                    end else if (cpld_tag_matched) begin
                        // --- Continuation beat: up to 4 data DWORDs ---
                        // Write each valid DWORD at cpld_dw_idx + offset.
                        // cpld_dw_idx hasn't updated yet (register), so we
                        // can compute all write addresses from the old value.
                        begin : cpld_cont_blk
                            reg [4:0] base;
                            base = cpld_dw_idx;

                            if (rx_tkeepdw[3] && base < 5'd16)
                                sq_entry[base[3:0]] <= rx_tdata[127:96];
                            if (rx_tkeepdw[2] && base + 5'd1 < 5'd16)
                                sq_entry[base[3:0] + 4'd1] <= rx_tdata[95:64];
                            if (rx_tkeepdw[1] && base + 5'd2 < 5'd16)
                                sq_entry[base[3:0] + 4'd2] <= rx_tdata[63:32];
                            if (rx_tkeepdw[0] && base + 5'd3 < 5'd16)
                                sq_entry[base[3:0] + 4'd3] <= rx_tdata[31:0];

                            cpld_dw_idx <= base + rx_tkeepdw[3] + rx_tkeepdw[2]
                                               + rx_tkeepdw[1] + rx_tkeepdw[0];
                        end
                    end
                end

                // Timeout
                cpld_timeout <= cpld_timeout + 20'd1;
                if (cpld_timeout >= CPLD_TIMEOUT_MAX) begin
                    cpld_active <= 1'b0;
                    state       <= ST_ADVANCE_HEAD;
                end

                // Transition when all 16 DWORDs received
                if (cpld_dw_idx >= 5'd16) begin
                    cpld_active <= 1'b0;
                    state       <= ST_PARSE_CMD;
                end
            end

            // ============================================================
            // PARSE_CMD: Set defaults, proceed to EXECUTE
            // ============================================================
            ST_PARSE_CMD: begin
                cq_dw0    <= 32'h0;
                cq_status <= 15'h0;
                cq_skip   <= 1'b0;
                state     <= ST_EXECUTE;
            end

            // ============================================================
            // EXECUTE: Dispatch by opcode
            // ============================================================
            ST_EXECUTE: begin
                case (cmd_opcode)

                OPC_IDENTIFY: begin
                    case (cmd_cdw10[7:0])
                        8'h01: begin // Identify Controller
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd1024;
                            dma_rom_offset <= 10'h0;
                            dma_source     <= 2'b00;
                            state          <= ST_DMA_WRITE;
                        end
                        8'h00: begin // Identify Namespace
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd1024;
                            dma_rom_offset <= 10'h0;
                            dma_source     <= 2'b01;
                            state          <= ST_DMA_WRITE;
                        end
                        default: begin
                            cq_status <= 15'b000_0000_000_00000010;
                            state     <= ST_CQ_HDR;
                        end
                    endcase
                end

                OPC_SET_FEATURES: begin
                    case (cmd_cdw10[7:0])
                        FID_NUM_QUEUES:      cq_dw0 <= 32'h00070007;
                        FID_INT_COALESCING,
                        FID_ARBITRATION,
                        FID_APST,
                        FID_ASYNC_EVENT_CFG,
                        FID_VOLATILE_WC,
                        FID_POWER_MGMT,
                        FID_TEMP_THRESHOLD,
                        FID_ERROR_RECOVERY,
                        FID_WRITE_ATOMICITY: cq_dw0 <= 32'h0;
                        default: begin
                            cq_dw0 <= 32'h0;
                            if (cmd_cdw10[7:0] >= 8'hC0)
                                cq_status <= 15'b000_0000_000_00000010;
                        end
                    endcase
                    state <= ST_CQ_HDR;
                end

                OPC_GET_FEATURES: begin
                    case (cmd_cdw10[7:0])
                        FID_NUM_QUEUES:     cq_dw0 <= 32'h00070007;
                        FID_TEMP_THRESHOLD: cq_dw0 <= 32'h00000164;
                        FID_VOLATILE_WC:    cq_dw0 <= 32'h00000001;
                        default: begin
                            cq_dw0 <= 32'h0;
                            if (cmd_cdw10[7:0] >= 8'hC0)
                                cq_status <= 15'b000_0000_000_00000010;
                        end
                    endcase
                    state <= ST_CQ_HDR;
                end

                OPC_CREATE_IO_CQ: begin
                    if (cmd_cdw10[15:0] >= 16'h1 && cmd_cdw10[15:0] <= 16'h8) begin
                        io_cq_created[cmd_cdw10[3:0]] <= 1'b1;
                        io_cq_base[cmd_cdw10[3:0]]    <= cmd_prp1;
                        io_cq_size[cmd_cdw10[3:0]]    <= cmd_cdw10[31:16];
                        io_cq_iv[cmd_cdw10[3:0]]      <= cmd_cdw11[19:16];
                    end else begin
                        cq_status <= 15'b000_0000_001_00000001;
                    end
                    cq_dw0 <= 32'h0;
                    state  <= ST_CQ_HDR;
                end

                OPC_CREATE_IO_SQ: begin
                    if (cmd_cdw10[15:0] >= 16'h1 && cmd_cdw10[15:0] <= 16'h8) begin
                        io_sq_created[cmd_cdw10[3:0]] <= 1'b1;
                        io_sq_base[cmd_cdw10[3:0]]    <= cmd_prp1;
                        io_sq_size[cmd_cdw10[3:0]]    <= cmd_cdw10[31:16];
                        io_sq_cqid[cmd_cdw10[3:0]]    <= cmd_cdw11[19:16];
                    end else begin
                        cq_status <= 15'b000_0000_001_00000001;
                    end
                    cq_dw0 <= 32'h0;
                    state  <= ST_CQ_HDR;
                end

                OPC_DELETE_IO_SQ: begin
                    if (cmd_cdw10[15:0] >= 16'h1 && cmd_cdw10[15:0] <= 16'h8)
                        io_sq_created[cmd_cdw10[3:0]] <= 1'b0;
                    else
                        cq_status <= 15'b000_0000_001_00000001;
                    cq_dw0 <= 32'h0;
                    state  <= ST_CQ_HDR;
                end

                OPC_DELETE_IO_CQ: begin
                    if (cmd_cdw10[15:0] >= 16'h1 && cmd_cdw10[15:0] <= 16'h8)
                        io_cq_created[cmd_cdw10[3:0]] <= 1'b0;
                    else
                        cq_status <= 15'b000_0000_001_00000001;
                    cq_dw0 <= 32'h0;
                    state  <= ST_CQ_HDR;
                end

                OPC_GET_LOG_PAGE: begin
                    case (cmd_cdw10[7:0])
                        LID_CMD_EFFECTS: begin
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd1024;
                            dma_rom_offset <= 10'h0;
                            dma_source     <= 2'b10;
                            state          <= ST_DMA_WRITE;
                        end
                        LID_SMART_HEALTH: begin
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd128;
                            dma_rom_offset <= 10'h0;
                            dma_source     <= 2'b11;
                            state          <= ST_DMA_WRITE;
                        end
                        LID_ERROR_INFO: begin
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd16;      // 64 bytes
                            dma_rom_offset <= 10'd768;     // high offset -> all zeros
                            dma_source     <= 2'b10;
                            state          <= ST_DMA_WRITE;
                        end
                        LID_FW_SLOT: begin
                            dma_addr       <= cmd_prp1;
                            dma_remaining  <= 12'd128;     // 512 bytes
                            dma_rom_offset <= 10'd768;     // all zeros
                            dma_source     <= 2'b10;
                            state          <= ST_DMA_WRITE;
                        end
                        default: begin
                            cq_status <= 15'b000_0000_000_00001001;
                            cq_dw0   <= 32'h0;
                            state    <= ST_CQ_HDR;
                        end
                    endcase
                end

                OPC_ASYNC_EVENT_REQ: begin
                    if (aer_pending_count < 3'd4)
                        aer_pending_count <= aer_pending_count + 3'd1;
                    cq_skip <= 1'b1;
                    state   <= ST_ADVANCE_HEAD;
                end

                OPC_ABORT: begin
                    cq_dw0 <= 32'h0;
                    state  <= ST_CQ_HDR;
                end

                default: begin
                    cq_dw0    <= 32'h0;
                    cq_status <= 15'h0;
                    state     <= ST_CQ_HDR;
                end

                endcase
            end

            // ============================================================
            // DMA_WRITE: Orchestrate multi-TLP data writes
            // ============================================================
            ST_DMA_WRITE: begin
                if (dma_remaining == 12'd0) begin
                    state <= ST_CQ_HDR;
                end else begin
                    dma_cur_tlp_len <= (dma_remaining > {6'b0, MPS_DWORDS})
                                       ? MPS_DWORDS : dma_remaining[5:0];
                    dma_tlp_dw_cnt  <= 6'h0;
                    state           <= ST_DMA_HDR;
                end
            end

            // ============================================================
            // DMA_HDR: MWr 4DW header
            // ============================================================
            ST_DMA_HDR: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tdata[127:96] <= {3'b011, 5'b00000, 1'b0, 3'b000, 4'b0000,
                                         1'b0, 1'b0, 2'b00, 2'b00,
                                         {4'b0, dma_cur_tlp_len}};
                    tx_tdata[95:64]  <= {pcie_id, 8'hE1, 4'hF, 4'hF};
                    tx_tdata[63:32]  <= dma_addr[63:32];
                    tx_tdata[31:0]   <= {dma_addr[31:2], 2'b00};
                    tx_tkeepdw       <= 4'b1111;
                    tx_tuser         <= {7'b0, 1'b0, 1'b1}; // first=1, last=0
                    tx_tlast         <= 1'b0;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;
                    state            <= ST_DMA_DATA;
                end
            end

            // ============================================================
            // DMA_DATA: Send data beats for current MWr TLP
            //
            // Each beat packs up to 4 DWORDs read combinationally from
            // the selected source. The read_dma_source function handles
            // all four source types. For BRAM sources (identify ctrl/ns),
            // Vivado synthesizes these as distributed ROM reads or block
            // RAM with asynchronous read port.
            // ============================================================
            ST_DMA_DATA: begin
                if (tx_stalled) begin
                    // hold current beat
                end else begin
                    begin : dma_data_blk
                        reg [5:0] dw_left;
                        reg [2:0] dw_this;
                        reg [9:0] ridx;
                        reg       is_last;

                        dw_left = dma_cur_tlp_len - dma_tlp_dw_cnt;
                        dw_this = (dw_left >= 6'd4) ? 3'd4 : dw_left[2:0];
                        ridx    = dma_rom_offset + {4'b0, dma_tlp_dw_cnt};
                        is_last = ({3'b0, dw_this} + dma_tlp_dw_cnt >= dma_cur_tlp_len);

                        // Pack data DWORDs: DW0 at [127:96] etc.
                        tx_tdata[127:96] <= (dw_this >= 3'd1)
                            ? read_dma_source(dma_source, ridx) : 32'h0;
                        tx_tdata[95:64]  <= (dw_this >= 3'd2)
                            ? read_dma_source(dma_source, ridx + 10'd1) : 32'h0;
                        tx_tdata[63:32]  <= (dw_this >= 3'd3)
                            ? read_dma_source(dma_source, ridx + 10'd2) : 32'h0;
                        tx_tdata[31:0]   <= (dw_this >= 3'd4)
                            ? read_dma_source(dma_source, ridx + 10'd3) : 32'h0;

                        case (dw_this)
                            3'd1:    tx_tkeepdw <= 4'b1000;
                            3'd2:    tx_tkeepdw <= 4'b1100;
                            3'd3:    tx_tkeepdw <= 4'b1110;
                            default: tx_tkeepdw <= 4'b1111;
                        endcase

                        tx_tlast    <= is_last;
                        tx_tuser    <= {7'b0, is_last, 1'b0}; // first=0, last=is_last
                        tx_tvalid   <= 1'b1;
                        tx_has_data <= 1'b1;

                        dma_tlp_dw_cnt <= dma_tlp_dw_cnt + {3'b0, dw_this};

                        if (is_last) begin
                            dma_addr       <= dma_addr + {52'b0, dma_cur_tlp_len, 2'b00};
                            dma_remaining  <= dma_remaining - {6'b0, dma_cur_tlp_len};
                            dma_rom_offset <= dma_rom_offset + {4'b0, dma_cur_tlp_len};
                            state          <= ST_DMA_WRITE;
                        end
                    end
                end
            end

            // ============================================================
            // CQ_HDR: MWr header for 16-byte CQ entry
            // ============================================================
            ST_CQ_HDR: begin
                if (cq_skip) begin
                    state <= ST_ADVANCE_HEAD;
                end else if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tdata[127:96] <= {3'b011, 5'b00000, 1'b0, 3'b000, 4'b0000,
                                         1'b0, 1'b0, 2'b00, 2'b00, 10'd4};
                    tx_tdata[95:64]  <= {pcie_id, 8'hE2, 4'hF, 4'hF};
                    tx_tdata[63:32]  <= cq_entry_addr[63:32];
                    tx_tdata[31:0]   <= {cq_entry_addr[31:2], 2'b00};
                    tx_tkeepdw       <= 4'b1111;
                    tx_tuser         <= {7'b0, 1'b0, 1'b1}; // first=1, last=0
                    tx_tlast         <= 1'b0;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;
                    state            <= ST_CQ_DATA;
                end
            end

            // ============================================================
            // CQ_DATA: CQ entry data (4 DWORDs, 1 beat)
            //   DW0 = cq_dw0
            //   DW1 = 0
            //   DW2 = {SQID=0, SQ_Head}
            //   DW3 = {Status, Phase, CID}
            // ============================================================
            ST_CQ_DATA: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tdata[127:96] <= cq_dw0;
                    tx_tdata[95:64]  <= 32'h0;
                    tx_tdata[63:32]  <= {16'h0000, sq_head_next};
                    tx_tdata[31:0]   <= {cq_status, cq_phase, cmd_cid};
                    tx_tkeepdw       <= 4'b1111;
                    tx_tuser         <= {7'b0, 1'b1, 1'b0}; // first=0, last=1
                    tx_tlast         <= 1'b1;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;

                    // Advance CQ tail
                    if (cq_tail >= admin_cq_size) begin
                        cq_tail  <= 16'h0;
                        cq_phase <= ~cq_phase;
                    end else begin
                        cq_tail  <= cq_tail + 16'h1;
                    end

                    state <= ST_SEND_MSIX;
                end
            end

            // ============================================================
            // SEND_MSIX: Wait for CQ data beat, then optionally send IRQ
            // ============================================================
            ST_SEND_MSIX: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tvalid   <= 1'b0;
                    tx_has_data <= 1'b0;
                    if (msix_vec_ctrl[0][0])
                        state <= ST_ADVANCE_HEAD; // masked
                    else
                        state <= ST_MSIX_HDR;
                end
            end

            // ============================================================
            // MSIX_HDR: MWr header for MSI-X (1 DWORD payload)
            // ============================================================
            ST_MSIX_HDR: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tdata[127:96] <= {3'b011, 5'b00000, 1'b0, 3'b000, 4'b0000,
                                         1'b0, 1'b0, 2'b00, 2'b00, 10'd1};
                    tx_tdata[95:64]  <= {pcie_id, 8'hE3, 4'h0, 4'hF};
                    tx_tdata[63:32]  <= msix_addr_hi[0];
                    tx_tdata[31:0]   <= {msix_addr_lo[0][31:2], 2'b00};
                    tx_tkeepdw       <= 4'b1111;
                    tx_tuser         <= {7'b0, 1'b0, 1'b1}; // first=1, last=0
                    tx_tlast         <= 1'b0;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;
                    state            <= ST_MSIX_DATA;
                end
            end

            // ============================================================
            // MSIX_DATA: MSI-X message data (1 DWORD)
            // ============================================================
            ST_MSIX_DATA: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tdata[127:96] <= msix_data[0];
                    tx_tdata[95:64]  <= 32'h0;
                    tx_tdata[63:32]  <= 32'h0;
                    tx_tdata[31:0]   <= 32'h0;
                    tx_tkeepdw       <= 4'b1000;
                    tx_tuser         <= {7'b0, 1'b1, 1'b0}; // first=0, last=1
                    tx_tlast         <= 1'b1;
                    tx_tvalid        <= 1'b1;
                    tx_has_data      <= 1'b1;
                    state            <= ST_ADVANCE_HEAD;
                end
            end

            // ============================================================
            // ADVANCE_HEAD: increment SQ head, return to IDLE
            // ============================================================
            ST_ADVANCE_HEAD: begin
                if (tx_stalled) begin
                    // hold
                end else begin
                    tx_tvalid   <= 1'b0;
                    tx_has_data <= 1'b0;
                    sq_head     <= sq_head_next;
                    cq_skip     <= 1'b0;
                    state       <= ST_IDLE;
                end
            end

            default: state <= ST_IDLE;

            endcase

        end // ctrl_enabled
    end // always

    // ================================================================
    // DATA SOURCE READ FUNCTION (used by DMA_DATA state)
    // ================================================================
    function [31:0] read_dma_source;
        input [1:0]  source;
        input [9:0]  idx;
        begin
            case (source)
                2'b00:   read_dma_source = identify_ctrl_rom[idx];
                2'b01:   read_dma_source = identify_ns_rom[idx];
                2'b10:   read_dma_source = cmd_effects_entry(idx);
                2'b11:   read_dma_source = (idx < 10'd128) ? smart_log_dword(idx[6:0]) : 32'h0;
                default: read_dma_source = 32'h0;
            endcase
        end
    endfunction

endmodule

// pcileech_nvme_controller.sv
// ============================================================
// NVM_Express_Pcileech_FPGA_75T
// discord: Moer2831
// community: https://discord.gg/sXcQhxa8qy
// NVMe Controller Emulation for NE-256 2280 (SM2263)
// Replaces pcileech_bar_impl_zerowrite4k as BAR0 implementation.
//
// BAR0 Memory Map (16KB):
//   0x0000-0x003F  NVMe Controller Registers (CAP, VS, CC, CSTS, etc.)
//   0x0040-0x0FFF  Reserved
//   0x1000-0x103F  Doorbell Registers (SQ0-SQ8, CQ0-CQ8)
//   0x1040-0x1FFF  Reserved
//   0x2000-0x20FF  MSI-X Table (16 entries x 16 bytes)
//   0x2100-0x210F  MSI-X PBA
//   0x2110-0x3FFF  Reserved
//
// Read latency: 2 CLKs (matches other BAR implementations)
//
// Real device values from NE-256 reference capture:
//   CAP  = 0x00100030_F00100FF
//   VS   = 0x00010300 (NVMe 1.3.0)
//   CC   = starts at 0 (disabled), host enables
//   CSTS = starts at 0, firmware manages RDY transition
// ============================================================

module pcileech_nvme_controller(
    input               rst,
    input               clk,
    // incoming BAR writes:
    input [31:0]        wr_addr,
    input [3:0]         wr_be,
    input [31:0]        wr_data,
    input               wr_valid,
    // incoming BAR reads:
    input  [87:0]       rd_req_ctx,
    input  [31:0]       rd_req_addr,
    input               rd_req_valid,
    // outgoing BAR read replies:
    output bit [87:0]   rd_rsp_ctx,
    output bit [31:0]   rd_rsp_data,
    output bit          rd_rsp_valid,
    // NVMe register outputs (for engine)
    output              o_ctrl_enabled,
    output [63:0]       o_admin_sq_base,
    output [63:0]       o_admin_cq_base,
    output [15:0]       o_admin_sq_size,
    output [15:0]       o_admin_cq_size,
    output [15:0]       o_admin_sq_tail,
    output              o_admin_sq_db_written,
    output [31:0]       o_msix_table [0:63]
);

    // ================================================================
    // REAL DONOR REGISTER VALUES (from NE-256 BAR0 dump)
    // ================================================================

    // CAP - Controller Capabilities (64-bit, read-only)
    // MQES=255, CQR=1, AMS=0, TO=240(120s), DSTRD=0, NSSRS=0,
    // CSS=NVM(1), BPS=0, MPSMIN=0(4KB), MPSMAX=1(8KB)
    localparam [63:0] NVME_CAP = 64'h00100030_F00100FF;

    // VS - Version (read-only)
    localparam [31:0] NVME_VS  = 32'h00010300;  // NVMe 1.3.0

    // Derived from CAP
    localparam [3:0]  CAP_DSTRD  = NVME_CAP[35:32];  // Doorbell stride = 0 (4 bytes)
    localparam [15:0] CAP_MQES   = NVME_CAP[15:0];   // Max Queue Entries - 1 = 255
    localparam [7:0]  CAP_TO     = NVME_CAP[31:24];   // Timeout in 500ms units = 240

    // Number of I/O queue pairs to support (matching real device response)
    localparam NUM_IO_QUEUES = 8;   // QIDs 1-8

    // ================================================================
    // NVMe REGISTER FILE
    // ================================================================

    // Writable registers
    reg [31:0]  reg_cc;         // 0x14: Controller Configuration
    reg [31:0]  reg_csts;       // 0x1C: Controller Status
    reg [31:0]  reg_intms;      // 0x0C: Interrupt Mask Set
    reg [31:0]  reg_nssr;       // 0x20: NVM Subsystem Reset
    reg [31:0]  reg_aqa;        // 0x24: Admin Queue Attributes
    reg [63:0]  reg_asq;        // 0x28: Admin Submission Queue Base
    reg [63:0]  reg_acq;        // 0x30: Admin Completion Queue Base

    // Doorbell registers (write-only from host perspective)
    // SQ doorbells: even indices (0, 2, 4, ..., 16)
    // CQ doorbells: odd indices (1, 3, 5, ..., 17)
    // Total: 9 SQ (admin + 8 IO) + 9 CQ (admin + 8 IO) = 18 doorbells
    reg [15:0]  doorbell_sq [0:NUM_IO_QUEUES];   // SQ tail pointers (QID 0-8)
    reg [15:0]  doorbell_cq [0:NUM_IO_QUEUES];   // CQ head pointers (QID 0-8)

    // Doorbell write pulse signals (active for 1 clock on doorbell write)
    reg         doorbell_sq_written [0:NUM_IO_QUEUES];
    reg         doorbell_cq_written [0:NUM_IO_QUEUES];

    // MSI-X Table (16 entries, each 16 bytes = 4 DWORDs)
    // Entry format: [0] Msg Addr Low, [1] Msg Addr High, [2] Msg Data, [3] Vector Control
    reg [31:0]  msix_table [0:63];  // 16 entries x 4 DWORDs = 64 DWORDs

    // MSI-X PBA (Pending Bit Array)
    reg [31:0]  msix_pba [0:0];     // 1 DWORD for 16 vectors

    // ================================================================
    // CC.EN -> CSTS.RDY STATE MACHINE
    // ================================================================

    localparam SM_DISABLED  = 3'd0;
    localparam SM_ENABLING  = 3'd1;
    localparam SM_READY     = 3'd2;
    localparam SM_DISABLING = 3'd3;
    localparam SM_SHUTDOWN  = 3'd4;

    reg [2:0]   ctrl_state;
    reg [15:0]  transition_counter;  // Delay counter for state transitions

    wire        cc_en  = reg_cc[0];
    wire [2:0]  cc_css = reg_cc[6:4];
    wire [3:0]  cc_mps = reg_cc[10:7];
    wire [2:0]  cc_ams = reg_cc[13:11];
    wire [1:0]  cc_shn = reg_cc[15:14];
    wire [3:0]  cc_iosqes = reg_cc[19:16];
    wire [3:0]  cc_iocqes = reg_cc[23:20];

    always @(posedge clk) begin
        if (rst) begin
            ctrl_state          <= SM_DISABLED;
            reg_csts            <= 32'h0;
            transition_counter  <= 0;
        end else begin
            case (ctrl_state)
                SM_DISABLED: begin
                    reg_csts <= 32'h0;  // RDY=0, SHST=0
                    if (cc_en) begin
                        ctrl_state <= SM_ENABLING;
                        transition_counter <= 16'd100;  // ~1.6us at 62.5MHz
                    end
                end

                SM_ENABLING: begin
                    if (!cc_en) begin
                        // Host cleared CC.EN during transition: abort, go disabled
                        reg_csts   <= 32'h0;
                        ctrl_state <= SM_DISABLED;
                    end else if (transition_counter > 0) begin
                        transition_counter <= transition_counter - 1;
                    end else begin
                        reg_csts[0] <= 1'b1;    // CSTS.RDY = 1
                        ctrl_state  <= SM_READY;
                    end
                end

                SM_READY: begin
                    // Check for disable
                    if (!cc_en) begin
                        ctrl_state <= SM_DISABLING;
                        transition_counter <= 16'd50;
                    end
                    // Check for shutdown
                    else if (cc_shn != 2'b00) begin
                        ctrl_state <= SM_SHUTDOWN;
                        transition_counter <= 16'd50;
                        // NVMe spec: set SHST=01 (shutdown processing) immediately
                        reg_csts <= {reg_csts[31:4], 2'b01, reg_csts[1:0]};  // SHST=01, keep RDY
                    end
                end

                SM_DISABLING: begin
                    if (transition_counter > 0) begin
                        transition_counter <= transition_counter - 1;
                    end else begin
                        reg_csts <= 32'h0;  // RDY=0, SHST=0
                        ctrl_state <= SM_DISABLED;
                    end
                end

                SM_SHUTDOWN: begin
                    if (transition_counter > 0) begin
                        transition_counter <= transition_counter - 1;
                    end else begin
                        // NVMe spec: set SHST=10 (shutdown complete), clear RDY
                        reg_csts <= 32'h0000_0008;  // SHST=10b, RDY=0
                        // Stay in shutdown until CC.EN cleared
                        if (!cc_en) begin
                            ctrl_state <= SM_DISABLED;
                            reg_csts <= 32'h0;
                        end
                    end
                end

                default: begin
                    ctrl_state <= SM_DISABLED;
                end
            endcase
        end
    end

    // ================================================================
    // BAR0 WRITE HANDLER
    // ================================================================

    wire [13:0] wr_offset = wr_addr[13:0];  // 16KB BAR0 offset
    wire [11:0] wr_dw_addr = wr_addr[13:2]; // DWORD address within BAR0

    // Byte-masked write helper
    function [31:0] byte_merge;
        input [31:0] old_val;
        input [31:0] new_val;
        input [3:0]  be;
        begin
            byte_merge = {
                be[3] ? new_val[31:24] : old_val[31:24],
                be[2] ? new_val[23:16] : old_val[23:16],
                be[1] ? new_val[15:8]  : old_val[15:8],
                be[0] ? new_val[7:0]   : old_val[7:0]
            };
        end
    endfunction

    integer i;

    always @(posedge clk) begin
        // Clear doorbell write pulses
        for (i = 0; i <= NUM_IO_QUEUES; i = i + 1) begin
            doorbell_sq_written[i] <= 1'b0;
            doorbell_cq_written[i] <= 1'b0;
        end

        if (rst) begin
            reg_cc    <= 32'h0;
            reg_intms <= 32'h0;
            reg_nssr  <= 32'h0;
            reg_aqa   <= 32'h0;
            reg_asq   <= 64'h0;
            reg_acq   <= 64'h0;
            for (i = 0; i <= NUM_IO_QUEUES; i = i + 1) begin
                doorbell_sq[i] <= 16'h0;
                doorbell_cq[i] <= 16'h0;
            end
            for (i = 0; i < 64; i = i + 1) begin
                msix_table[i] <= 32'h0;
            end
            msix_pba[0] <= 32'h0;
        end else if (wr_valid) begin

            // --- NVMe Controller Registers (0x0000-0x003F) ---
            if (wr_offset < 14'h0040) begin
                case (wr_dw_addr[3:0])
                    // 0x00-0x07: CAP (read-only, ignore writes)
                    // 0x08-0x0B: VS  (read-only, ignore writes)

                    4'h3: begin // 0x0C: INTMS (write-set: bits written 1 are set in mask)
                        reg_intms <= reg_intms | byte_merge(32'h0, wr_data, wr_be);
                    end

                    4'h4: begin // 0x10: INTMC (write-clear: bits written 1 are cleared in mask)
                        reg_intms <= reg_intms & ~byte_merge(32'h0, wr_data, wr_be);
                    end

                    4'h5: begin // 0x14: CC
                        reg_cc <= byte_merge(reg_cc, wr_data, wr_be);
                    end

                    // 0x1C: CSTS (read-only, managed by state machine)

                    4'h8: begin // 0x20: NSSR
                        reg_nssr <= byte_merge(reg_nssr, wr_data, wr_be);
                    end

                    4'h9: begin // 0x24: AQA (only writable when CC.EN=0)
                        if (!cc_en)
                            reg_aqa <= byte_merge(reg_aqa, wr_data, wr_be);
                    end

                    4'hA: begin // 0x28: ASQ low (only writable when CC.EN=0)
                        if (!cc_en)
                            reg_asq[31:0] <= byte_merge(reg_asq[31:0], wr_data, wr_be);
                    end

                    4'hB: begin // 0x2C: ASQ high
                        if (!cc_en)
                            reg_asq[63:32] <= byte_merge(reg_asq[63:32], wr_data, wr_be);
                    end

                    4'hC: begin // 0x30: ACQ low (only writable when CC.EN=0)
                        if (!cc_en)
                            reg_acq[31:0] <= byte_merge(reg_acq[31:0], wr_data, wr_be);
                    end

                    4'hD: begin // 0x34: ACQ high
                        if (!cc_en)
                            reg_acq[63:32] <= byte_merge(reg_acq[63:32], wr_data, wr_be);
                    end

                    default: ; // Reserved, ignore
                endcase
            end

            // --- Doorbell Registers (0x1000-0x103F) ---
            // DSTRD=0: SQ0 at 0x1000, CQ0 at 0x1004, SQ1 at 0x1008, CQ1 at 0x100C, etc.
            // wr_dw_addr = wr_addr[13:2]. For 0x1000: dw_addr = 0x400.
            // Doorbell DWORD index = wr_dw_addr - 0x400. Even=SQ, Odd=CQ, QID=index/2.
            else if (wr_dw_addr >= 12'h400 && wr_dw_addr < 12'h400 + (NUM_IO_QUEUES + 1) * 2) begin
                if (!wr_dw_addr[0]) begin
                    // Even offset = SQ tail doorbell
                    doorbell_sq[wr_dw_addr[4:1]] <= wr_data[15:0];
                    doorbell_sq_written[wr_dw_addr[4:1]] <= 1'b1;
                end else begin
                    // Odd offset = CQ head doorbell
                    doorbell_cq[wr_dw_addr[4:1]] <= wr_data[15:0];
                    doorbell_cq_written[wr_dw_addr[4:1]] <= 1'b1;
                end
            end

            // --- MSI-X Table (0x2000-0x20FF) ---
            else if (wr_offset >= 14'h2000 && wr_offset < 14'h2100) begin
                // 16 entries × 4 DWORDs = 64 DWORDs
                reg [5:0] msix_idx;
                msix_idx = wr_dw_addr[5:0];  // DW offset within MSI-X table
                if (msix_idx < 64)
                    msix_table[msix_idx] <= byte_merge(msix_table[msix_idx], wr_data, wr_be);
            end

            // --- MSI-X PBA (0x2100-0x210F) ---
            else if (wr_offset >= 14'h2100 && wr_offset < 14'h2110) begin
                msix_pba[0] <= byte_merge(msix_pba[0], wr_data, wr_be);
            end
        end
    end

    // ================================================================
    // BAR0 READ HANDLER (2-clock pipeline)
    // ================================================================

    // Pipeline stage 1: Latch request, compute read data
    reg [87:0]  rd_ctx_p1;
    reg         rd_valid_p1;
    reg [31:0]  rd_data_p1;

    wire [13:0] rd_offset = rd_req_addr[13:0];
    wire [11:0] rd_dw_addr_in = rd_req_addr[13:2];

    always @(posedge clk) begin
        rd_ctx_p1   <= rd_req_ctx;
        rd_valid_p1 <= rd_req_valid;

        if (rd_req_valid) begin
            // --- NVMe Controller Registers ---
            if (rd_offset < 14'h0040) begin
                case (rd_dw_addr_in[3:0])
                    4'h0: rd_data_p1 <= NVME_CAP[31:0];     // CAP low
                    4'h1: rd_data_p1 <= NVME_CAP[63:32];    // CAP high
                    4'h2: rd_data_p1 <= NVME_VS;             // VS
                    4'h3: rd_data_p1 <= reg_intms;           // INTMS
                    4'h4: rd_data_p1 <= 32'h0;               // INTMC (reads 0)
                    4'h5: rd_data_p1 <= reg_cc;              // CC
                    4'h6: rd_data_p1 <= 32'h0;               // Reserved
                    4'h7: rd_data_p1 <= reg_csts;            // CSTS
                    4'h8: rd_data_p1 <= reg_nssr;            // NSSR
                    4'h9: rd_data_p1 <= reg_aqa;             // AQA
                    4'hA: rd_data_p1 <= reg_asq[31:0];       // ASQ low
                    4'hB: rd_data_p1 <= reg_asq[63:32];      // ASQ high
                    4'hC: rd_data_p1 <= reg_acq[31:0];       // ACQ low
                    4'hD: rd_data_p1 <= reg_acq[63:32];      // ACQ high
                    4'hE: rd_data_p1 <= 32'h0;               // CMBLOC
                    4'hF: rd_data_p1 <= 32'h0;               // CMBSZ
                    default: rd_data_p1 <= 32'h0;
                endcase
            end
            // --- Doorbell registers (read returns 0 per NVMe spec) ---
            else if (rd_offset >= 14'h1000 && rd_offset < 14'h2000) begin
                rd_data_p1 <= 32'h0;
            end
            // --- MSI-X Table ---
            else if (rd_offset >= 14'h2000 && rd_offset < 14'h2100) begin
                rd_data_p1 <= msix_table[rd_dw_addr_in[5:0]];
            end
            // --- MSI-X PBA ---
            else if (rd_offset >= 14'h2100 && rd_offset < 14'h2110) begin
                rd_data_p1 <= msix_pba[0];
            end
            // --- Everything else returns 0 ---
            else begin
                rd_data_p1 <= 32'h0;
            end
        end else begin
            rd_data_p1 <= 32'h0;
        end
    end

    // Pipeline stage 2: Output
    always @(posedge clk) begin
        rd_rsp_ctx   <= rd_ctx_p1;
        rd_rsp_data  <= rd_data_p1;
        rd_rsp_valid <= rd_valid_p1;
    end

    // ================================================================
    // OUTPUT SIGNALS FOR ADMIN/IO ENGINES
    // ================================================================

    // Controller state outputs
    wire        ctrl_enabled = (ctrl_state == SM_READY);
    wire [15:0] admin_sq_size_w = reg_aqa[11:0];    // ASQS (0-based)
    wire [15:0] admin_cq_size_w = reg_aqa[27:16];   // ACQS (0-based)
    wire [63:0] admin_sq_base_w = reg_asq;
    wire [63:0] admin_cq_base_w = reg_acq;

    // Doorbell signals for queue engines
    wire [15:0] admin_sq_tail_w = doorbell_sq[0];
    wire [15:0] admin_cq_head = doorbell_cq[0];

    // MSI-X table access for interrupt engine
    // Entry N: msg_addr = {msix_table[N*4+1], msix_table[N*4+0]}
    //          msg_data = msix_table[N*4+2]
    //          vec_ctrl = msix_table[N*4+3]

    // Drive output ports
    assign o_ctrl_enabled        = ctrl_enabled;
    assign o_admin_sq_base       = admin_sq_base_w;
    assign o_admin_cq_base       = admin_cq_base_w;
    assign o_admin_sq_size       = admin_sq_size_w;
    assign o_admin_cq_size       = admin_cq_size_w;
    assign o_admin_sq_tail       = admin_sq_tail_w;
    assign o_admin_sq_db_written = doorbell_sq_written[0];

    genvar gi;
    generate
        for (gi = 0; gi < 64; gi = gi + 1) begin : gen_msix_out
            assign o_msix_table[gi] = msix_table[gi];
        end
    endgenerate

endmodule

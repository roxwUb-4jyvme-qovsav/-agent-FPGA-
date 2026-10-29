// 公开采集核心。100 MHz 使用时钟使能；本模块不含 UART/寄存器总线外壳。
module capture_core #(
    parameter integer CHANNELS = 8,
    parameter integer CLK_HZ = 100000000
) (
    input wire clk,
    input wire reset,
    input wire [CHANNELS-1:0] async_inputs,
    input wire cfg_valid,
    input wire [1:0] cfg_trigger_mode,
    input wire [4:0] cfg_trigger_channel,
    input wire [15:0] cfg_divider,
    input wire [15:0] cfg_channel_mask,
    input wire [12:0] cfg_depth,
    input wire ctrl_arm,
    input wire ctrl_abort,
    output reg cfg_accepted,
    output reg arm_accepted,
    output reg [2:0] state,
    output reg [31:0] capture_id,
    output reg [12:0] sample_count,
    output reg [3:0] error_code,
    output reg sample_tick,
    output reg sample_write,
    output reg [CHANNELS-1:0] sample_data,
    input wire rd_enable,
    input wire [31:0] rd_capture_id,
    input wire [12:0] rd_offset,
    output reg rd_valid,
    output reg [CHANNELS-1:0] rd_data,
    output reg [3:0] rd_error
);
    localparam [2:0] IDLE=0, ARMED=1, CAPTURING=2, DONE=3, ABORTED=4;
    localparam [15:0] ALL_MASK = (CHANNELS == 16) ? 16'hffff : 16'h00ff;
    // async_reg 属性仅辅助工具识别 CDC，不构成实板约束验证。
    (* ASYNC_REG = "TRUE" *) reg [CHANNELS-1:0] sync1, sync2;
    reg [CHANNELS-1:0] previous_vector;
    reg [CHANNELS-1:0] channel_mask;
    reg trigger_mode;
    reg [4:0] trigger_channel;
    reg [15:0] divider, phase;
    reg [12:0] depth;
    reg valid_capture;
    reg [CHANNELS-1:0] ram [0:4095];
    wire busy = state == ARMED || state == CAPTURING;
    wire config_ok = cfg_trigger_mode <= 1 && cfg_trigger_channel < CHANNELS
        && (cfg_divider == 10 || cfg_divider == 100)
        && cfg_channel_mask != 0 && (cfg_channel_mask & ~ALL_MASK) == 0
        && cfg_depth >= 16 && cfg_depth <= 4096
        && (cfg_depth & (cfg_depth - 1'b1)) == 0
        && (cfg_trigger_mode == 0 || ((cfg_channel_mask >> cfg_trigger_channel) & 1'b1) != 0);
    wire rising_trigger = !previous_vector[trigger_channel] && sync2[trigger_channel];

    // 复位 > 中止 > 原子配置 > ARM。被拒绝的命令不会暂停正在运行的采样时钟。
    always @(posedge clk) begin
        if (reset) begin
            sync1 <= 0; sync2 <= 0; previous_vector <= 0;
            channel_mask <= ALL_MASK; trigger_mode <= 0; trigger_channel <= 0;
            divider <= 10; phase <= 0; depth <= 4096;
            valid_capture <= 0; state <= IDLE; capture_id <= 0; sample_count <= 0;
            error_code <= 0; cfg_accepted <= 0; arm_accepted <= 0;
            sample_tick <= 0; sample_write <= 0; sample_data <= 0;
            rd_valid <= 0; rd_data <= 0; rd_error <= 0;
        end else begin
            // 核心在本沿读取旧 sync2：引脚变化不能越过两级同步器。
            sync1 <= async_inputs;
            sync2 <= sync1;
            cfg_accepted <= 0; arm_accepted <= 0;
            sample_tick <= 0; sample_write <= 0;
            rd_valid <= 0; rd_error <= 0;

            if (busy && !ctrl_abort) begin
                if (phase == divider - 1'b1) begin
                    phase <= 0;
                    sample_tick <= 1;
                    previous_vector <= sync2;
                    if (state == CAPTURING || !trigger_mode || rising_trigger) begin
                        // 触发 tick 即样本 0；第 depth 次写后冻结，不写 depth+1。
                        ram[sample_count] <= sync2 & channel_mask;
                        sample_data <= sync2 & channel_mask;
                        sample_write <= 1;
                        sample_count <= sample_count + 1'b1;
                        state <= (sample_count == depth - 1'b1) ? DONE : CAPTURING;
                    end
                end else begin
                    phase <= phase + 1'b1;
                end
            end

            if (ctrl_abort) begin
                if (busy) state <= ABORTED;
            end else if (cfg_valid) begin
                if (!config_ok) error_code <= 4;
                else if (busy) error_code <= 6;
                else begin
                    channel_mask <= cfg_channel_mask[CHANNELS-1:0];
                    trigger_mode <= cfg_trigger_mode[0];
                    trigger_channel <= cfg_trigger_channel;
                    divider <= cfg_divider; depth <= cfg_depth;
                    sample_count <= 0; phase <= 0; valid_capture <= 0;
                    state <= IDLE; error_code <= 0; cfg_accepted <= 1;
                end
            end else if (ctrl_arm) begin
                if (busy) error_code <= 6;
                else begin
                    capture_id <= (capture_id == 32'hffffffff) ? 1 : capture_id + 1'b1;
                    state <= ARMED; sample_count <= 0; phase <= 0;
                    previous_vector <= sync2; valid_capture <= 1; arm_accepted <= 1;
                end
            end

            if (rd_enable) begin
                // 旧 ID 始终先判；新 ARM/配置所在沿不能读出已失效的旧 RAM。
                if (!valid_capture || rd_capture_id != capture_id
                    || (!busy && !ctrl_abort && (ctrl_arm || (cfg_valid && config_ok)))) rd_error <= 5;
                else if (state == ABORTED || (busy && ctrl_abort)) rd_error <= 10;
                else if (state != DONE) rd_error <= 7;
                else if (rd_offset >= sample_count) rd_error <= 8;
                else begin rd_data <= ram[rd_offset]; rd_valid <= 1; end
            end
        end
    end
endmodule

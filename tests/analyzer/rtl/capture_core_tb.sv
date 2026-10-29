`timescale 1ns/1ps
module capture_core_tb;
    parameter integer CHANNELS = 8;
    reg clk = 0;
    always #5 clk = ~clk;
    reg reset = 1;
    reg [CHANNELS-1:0] async_inputs = 0;
    reg cfg_valid = 0;
    reg [1:0] cfg_trigger_mode = 0;
    reg [4:0] cfg_trigger_channel = 0;
    reg [15:0] cfg_divider = 10;
    reg [15:0] cfg_channel_mask = (CHANNELS == 16) ? 16'hffff : 16'hff;
    reg [12:0] cfg_depth = 16;
    reg ctrl_arm = 0, ctrl_abort = 0;
    wire cfg_accepted, arm_accepted;
    wire [2:0] state;
    wire [31:0] capture_id;
    wire [12:0] sample_count;
    wire [3:0] error_code;
    wire sample_tick, sample_write;
    wire [CHANNELS-1:0] sample_data;
    reg rd_enable = 0;
    reg [31:0] rd_capture_id = 0;
    reg [12:0] rd_offset = 0;
    wire rd_valid;
    wire [CHANNELS-1:0] rd_data;
    wire [3:0] rd_error;
    capture_core #(.CHANNELS(CHANNELS)) dut (.*);
    integer edge_number;

    task cycle;
        begin @(posedge clk); #1; end
    endtask
    task configure;
        begin
            @(negedge clk); cfg_valid = 1;
            cycle();
            if (!cfg_accepted) $fatal(1, "config was not atomically accepted");
            @(negedge clk); cfg_valid = 0;
        end
    endtask
    task arm_capture;
        begin
            @(negedge clk); ctrl_arm = 1;
            cycle();
            if (!arm_accepted || sample_count != 0) $fatal(1, "ARM acceptance failed");
            @(negedge clk); ctrl_arm = 0;
        end
    endtask

    initial begin
        cycle(); cycle();
        @(negedge clk); reset = 0;
        configure();
        arm_capture();
        for (edge_number = 1; edge_number <= 160; edge_number = edge_number + 1) begin
            // 在第 9 拍前改变引脚，tick 10 仍必须看到旧的第二级值。
            if (edge_number == 9) async_inputs = {CHANNELS{1'b1}};
            cycle();
            if (sample_tick != ((edge_number % 10) == 0)) $fatal(1, "tick drift at %0d", edge_number);
            if (edge_number == 10 && (sample_count != 1 || sample_data != 0)) $fatal(1, "two-flop latency ignored");
            if (edge_number == 20 && sample_data != {CHANNELS{1'b1}}) $fatal(1, "synchronized data missing");
            @(negedge clk);
        end
        if (state != 3 || sample_count != 16) $fatal(1, "depth completion off by one");
        ctrl_abort = 1;
        cycle();
        if (state != 3) $fatal(1, "ABORT destroyed DONE data");
        @(negedge clk); ctrl_abort = 0; rd_enable = 1; rd_capture_id = capture_id; rd_offset = 1;
        cycle();
        if (!rd_valid || rd_data != {CHANNELS{1'b1}}) $fatal(1, "frozen RAM read failed");
        @(negedge clk); rd_enable = 0;
        cfg_trigger_mode = 1; cfg_trigger_channel = CHANNELS - 1;
        cfg_channel_mask = (1 << (CHANNELS - 1)); async_inputs = 0;
        configure();
        repeat (3) cycle();
        arm_capture();
        repeat (10) cycle();
        if (sample_count != 0 || state != 1) $fatal(1, "rising mode triggered while low");
        @(negedge clk); async_inputs = {CHANNELS{1'b1}};
        repeat (10) cycle();
        if (sample_count != 1 || sample_data != (1 << (CHANNELS - 1))) $fatal(1, "trigger channel/mask/first sample wrong");
        repeat (9) cycle();
        @(negedge clk); ctrl_abort = 1;
        cycle();
        if (sample_write || sample_count != 1 || state != 4) $fatal(1, "ABORT lost to same-edge tick");
        @(negedge clk); reset = 1;
        cycle();
        if (state != 0 || capture_id != 0 || sample_count != 0) $fatal(1, "reset priority failed");
        $display("PUBLIC_CAPTURE_PASS channels=%0d", CHANNELS);
        $finish;
    end
    initial begin #100000; $fatal(1, "public TB timeout"); end
endmodule

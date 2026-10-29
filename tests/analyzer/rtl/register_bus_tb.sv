`timescale 1ns/1ps
module register_bus_tb;
    reg clk=0;always #5 clk=~clk;
    reg reset=1;
    reg req_valid=0,req_write=0;
    reg [31:0] req_address=0,req_wdata=0;
    wire req_ready,rsp_valid,rsp_error;
    reg rsp_ready=0;
    wire [31:0] rsp_rdata;
    reg [2:0] capture_state=0;
    reg [31:0] capture_id=3;
    reg [12:0] sample_count=16;
    reg [3:0] error_code=0;
    wire cfg_valid,ctrl_arm,ctrl_abort;
    wire [1:0] cfg_trigger_mode;
    wire [4:0] cfg_trigger_channel;
    wire [15:0] cfg_divider,cfg_channel_mask;
    wire [12:0] cfg_depth;
    reg cfg_accepted=0,arm_accepted=0;
    capture_register_bus #(.CHANNELS(16)) dut (.*);
    task request(input write,input [31:0] address,input [31:0] value);
        begin
            wait(req_ready);@(negedge clk);req_valid=1;req_write=write;req_address=address;req_wdata=value;
            @(negedge clk);req_valid=0;
        end
    endtask
    task response(input error,input [31:0] value);
        begin
            wait(rsp_valid);#1;
            if(rsp_error!==error||(!error&&rsp_rdata!==value))$fatal(1,"bus response mismatch");
            repeat(3)begin @(posedge clk);#1;if(!rsp_valid||req_ready)$fatal(1,"bus response/backpressure violated");end
            @(negedge clk);rsp_ready=1;@(negedge clk);rsp_ready=0;
        end
    endtask
    initial begin
        repeat(3)@(negedge clk);reset=0;
        request(0,0,0);response(0,0);
        request(0,40,0);response(0,32'h00010210);
        request(1,4,7);response(1,0);
        request(0,3,0);response(1,0);
        request(1,8,11);response(1,0);
        request(1,8,100);wait(cfg_valid);
        if(cfg_divider!=100)$fatal(1,"bus shadow write missing");
        @(negedge clk);cfg_accepted=1;@(negedge clk);cfg_accepted=0;response(0,0);
        request(0,8,0);response(0,100);
        capture_state=1;request(1,24,32);response(1,0);
        capture_state=0;request(1,0,4);response(1,0);
        request(1,0,3);wait(ctrl_abort);if(ctrl_arm)$fatal(1,"ABORT priority lost");response(0,0);
        $display("PUBLIC_BUS_PASS");$finish;
    end
    initial begin #100000;$fatal(1,"bus timeout");end
endmodule

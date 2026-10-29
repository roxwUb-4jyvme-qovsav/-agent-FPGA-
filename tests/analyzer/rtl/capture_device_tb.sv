`timescale 1ns/1ps
module capture_device_tb;
    reg clk = 0; always #5 clk = ~clk;
    reg reset = 1;
    reg [15:0] async_inputs = 16'h8001;
    reg [7:0] rx_data = 0;
    reg rx_valid = 0, rx_frame_error = 0;
    wire rx_ready;
    wire [7:0] tx_data;
    wire tx_valid;
    reg tx_ready = 1;
    wire [2:0] state;
    wire [31:0] capture_id;
    wire [12:0] sample_count;
    capture_device #(.CHANNELS(16), .CLK_HZ(100000000)) dut (.*);
    reg [7:0] request [0:1035];
    reg [7:0] received [0:1035];
    integer receive_count = 0;
    integer i;
    reg [15:0] crc;
    function [15:0] crc_byte(input [15:0] old, input [7:0] value);
        integer bitno; reg [15:0] work;
        begin work = old ^ (value << 8); for(bitno=0;bitno<8;bitno=bitno+1) work = work[15] ? (work<<1)^16'h1021 : work<<1; crc_byte=work; end
    endfunction
    always @(posedge clk) if(tx_valid && tx_ready) begin received[receive_count] = tx_data; receive_count = receive_count+1; end
    task send_request(input [7:0] kind, input [15:0] seq, input integer length);
        begin
            request[0]=8'ha5; request[1]=8'h5a; request[2]=1; request[3]=0;
            request[4]=kind; request[5]=0; request[6]=seq[7:0]; request[7]=seq[15:8];
            request[8]=length&255; request[9]=(length>>8)&255;
            crc=16'hffff; for(i=2;i<length+10;i=i+1) crc=crc_byte(crc,request[i]);
            request[length+10]=crc[7:0]; request[length+11]=crc[15:8];
            receive_count=0;
            for(i=0;i<length+12;i=i+1) begin
                @(negedge clk); rx_valid=1; rx_data=request[i];
                do begin @(posedge clk); end while(!rx_ready);
                @(negedge clk); rx_valid=0;
            end
        end
    endtask
    task check_response(input [7:0] kind, input [15:0] seq, input integer length);
        integer n; reg [15:0] response_crc;
        begin
            wait(receive_count >= length+12); repeat(3) @(negedge clk);
            if(receive_count != length+12 || received[0]!=8'ha5 || received[1]!=8'h5a
                ||received[2]!=1||received[3]!=0||received[4]!=kind||received[5]!=0
                ||received[6]!=seq[7:0]||received[7]!=seq[15:8]||received[8]!=(length&255)||received[9]!=((length>>8)&255)) $fatal(1,"wire header mismatch kind=%h count=%0d",kind,receive_count);
            response_crc=16'hffff; for(n=2;n<length+10;n=n+1) response_crc=crc_byte(response_crc,received[n]);
            if(received[length+10]!=response_crc[7:0]||received[length+11]!=response_crc[15:8]) $fatal(1,"response CRC wrong");
        end
    endtask
    initial begin
        repeat(3) @(negedge clk); reset=0;
        send_request(1,1,0); check_response(8'h81,1,14);
        if(received[10]!=16||received[11]!=2) $fatal(1,"caps width mismatch");
        request[10]=0;request[11]=0;request[12]=10;request[13]=0;request[14]=1;request[15]=8'h80;
        request[16]=16;request[17]=0;request[18]=0;request[19]=0;
        send_request(2,2,10); check_response(8'h82,2,0);
        send_request(3,3,0); check_response(8'h83,3,4);
        if(capture_id!=1) $fatal(1,"ARM not accepted before response");
        send_request(3,3,0); check_response(8'h83,3,4);
        if(capture_id!=1) $fatal(1,"duplicate ARM executed twice");
        send_request(6,3,0); check_response(8'hff,3,3);
        if(received[11]!=9) $fatal(1,"sequence conflict missing");
        wait(state==3);
        request[10]=1;request[11]=0;request[12]=0;request[13]=0;
        request[14]=0;request[15]=0;request[16]=0;request[17]=0;request[18]=2;request[19]=0;
        send_request(4,4,10); check_response(8'h84,4,16);
        if(received[20]!=2||received[21]!=0||received[22]!=1||received[23]!=8'h80||received[24]!=1||received[25]!=8'h80) $fatal(1,"read prefix/LE/mask mismatch");
        send_request(6,5,0); check_response(8'h86,5,4);
        if(state!=3||sample_count!=16) $fatal(1,"ABORT destroyed DONE");
        $display("PUBLIC_DEVICE_PASS"); $finish;
    end
    initial begin #2000000; $fatal(1,"device timeout state=%0d receive_count=%0d",state,receive_count); end
endmodule

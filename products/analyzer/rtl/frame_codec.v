// capture/1.0 有界接收帧缓冲。CRC/长度错误不产生可信请求或 ERROR 响应。
module frame_codec #(parameter integer CLK_HZ=100000000) (
    input wire clk,input wire reset,
    input wire [7:0] rx_data,input wire rx_valid,output wire rx_ready,input wire rx_frame_error,
    output reg request_valid,input wire request_ready,
    output reg [7:0] request_major,request_minor,request_type,request_flags,
    output reg [15:0] request_sequence,request_length,
    input wire [9:0] payload_index,output wire [7:0] payload_data
);
    localparam integer TIMEOUT_CLOCKS=CLK_HZ/10;
    reg [7:0] buffer [0:1035];
    reg [10:0] count;
    integer silent_clocks;
    integer i,j;
    reg [15:0] calculated_crc;
    wire [15:0] candidate_length={buffer[9],buffer[8]};
    wire needs_work = (count>=1 && buffer[0]!=8'ha5)
        || (count>=2 && buffer[1]!=8'h5a)
        || (count>=10 && candidate_length>1024)
        || (count>=12 && candidate_length<=1024 && count>=candidate_length+12);
    assign rx_ready=!request_valid && !needs_work && count<1036;
    assign payload_data=buffer[10+payload_index];
    function [15:0] crc_byte(input [15:0] old,input [7:0] value);
        integer bitno; reg [15:0] work;
        begin work=old^(value<<8);for(bitno=0;bitno<8;bitno=bitno+1)work=work[15]?(work<<1)^16'h1021:work<<1;crc_byte=work;end
    endfunction
    always @* begin
        calculated_crc=16'hffff;
        for(j=2;j<1034;j=j+1)
            if(j<candidate_length+10) calculated_crc=crc_byte(calculated_crc,buffer[j]);
    end
    always @(posedge clk) begin
        if(reset) begin
            count<=0;silent_clocks<=0;request_valid<=0;
            request_major<=0;request_minor<=0;request_type<=0;request_flags<=0;
            request_sequence<=0;request_length<=0;
        end else if(rx_frame_error) begin count<=0;silent_clocks<=0;request_valid<=0;end
        else if(request_valid) begin
            if(request_ready) begin request_valid<=0;count<=0;silent_clocks<=0;end
        end else if(rx_valid && rx_ready) begin
            buffer[count]<=rx_data;count<=count+1'b1;silent_clocks<=0;
        end else if(count!=0 && silent_clocks>=TIMEOUT_CLOCKS-1) begin count<=0;silent_clocks<=0;end
        else begin
            if(count!=0) silent_clocks<=silent_clocks+1;
            if(needs_work) begin
                if(count>=12 && buffer[0]==8'ha5 && buffer[1]==8'h5a && candidate_length<=1024
                    && count==candidate_length+12
                    && calculated_crc=={buffer[candidate_length+11],buffer[candidate_length+10]}) begin
                    request_major<=buffer[2];request_minor<=buffer[3];request_type<=buffer[4];request_flags<=buffer[5];
                    request_sequence<={buffer[7],buffer[6]};request_length<=candidate_length;request_valid<=1;
                end else begin
                    // 错误候选只丢起点一个字节，允许从损坏帧中的下一同步字恢复。
                    for(i=0;i<1035;i=i+1) buffer[i]<=buffer[i+1];
                    count<=count-1'b1;
                end
            end
        end
    end
endmodule

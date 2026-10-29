// 响应帧流化编码；ready 低时字节、索引及 CRC 均保持稳定。
module frame_encoder (
    input wire clk,input wire reset,
    input wire response_valid,output wire response_ready,
    input wire [7:0] response_type,input wire [15:0] response_sequence,response_length,
    output wire [9:0] payload_index,input wire [7:0] payload_data,
    output reg [7:0] tx_data,output wire tx_valid,input wire tx_ready,output reg frame_done
);
    reg active;
    reg [7:0] kind;
    reg [15:0] seq,length,position,crc;
    assign response_ready=!active;
    assign tx_valid=active;
    assign payload_index=(position>=10 && position<length+10)?position-10:0;
    function [15:0] crc_byte(input [15:0] old,input [7:0] value);
        integer bitno;reg [15:0] work;
        begin work=old^(value<<8);for(bitno=0;bitno<8;bitno=bitno+1)work=work[15]?(work<<1)^16'h1021:work<<1;crc_byte=work;end
    endfunction
    always @* begin
        case(position)
            0:tx_data=8'ha5;1:tx_data=8'h5a;2:tx_data=1;3:tx_data=0;
            4:tx_data=kind;5:tx_data=0;6:tx_data=seq[7:0];7:tx_data=seq[15:8];
            8:tx_data=length[7:0];9:tx_data=length[15:8];
            default:begin
                if(position<length+10)tx_data=payload_data;
                else if(position==length+10)tx_data=crc[7:0];else tx_data=crc[15:8];
            end
        endcase
    end
    always @(posedge clk) begin
        if(reset)begin active<=0;position<=0;kind<=0;seq<=0;length<=0;crc<=16'hffff;frame_done<=0;end
        else begin
            frame_done<=0;
            if(response_valid && response_ready)begin active<=1;kind<=response_type;seq<=response_sequence;length<=response_length;position<=0;crc<=16'hffff;end
            else if(active && tx_ready)begin
                if(position>=2 && position<length+10)crc<=crc_byte(crc,tx_data);
                if(position==length+11)begin active<=0;frame_done<=1;end
                else position<=position+1'b1;
            end
        end
    end
endmodule

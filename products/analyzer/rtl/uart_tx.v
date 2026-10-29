// 参数化 8N1 发送器；输入 ready/valid 握手，忙期间不会消费下一字节。
module uart_tx #(parameter integer CLK_HZ=100000000, parameter integer BAUD=115200) (
    input wire clk, input wire reset,
    input wire [7:0] tx_data, input wire tx_valid, output wire tx_ready,
    output reg serial_tx
);
    localparam integer BIT_CLOCKS=(CLK_HZ+BAUD/2)/BAUD;
    reg active;
    reg [9:0] shift;
    reg [3:0] bit_index;
    integer remaining;
    assign tx_ready = !active;
    always @(posedge clk) begin
        if(reset) begin serial_tx<=1; active<=0; shift<=10'h3ff; bit_index<=0; remaining<=0; end
        else if(!active) begin
            serial_tx<=1;
            if(tx_valid) begin
                shift<={1'b1,tx_data,1'b0}; serial_tx<=0;
                active<=1; bit_index<=0; remaining<=BIT_CLOCKS-1;
            end
        end else if(remaining != 0) remaining<=remaining-1;
        else if(bit_index==9) begin active<=0; serial_tx<=1; end
        else begin
            // 每一位保持完整 BIT_CLOCKS 拍，低位先发送，包括起始与停止位。
            shift<={1'b1,shift[9:1]}; serial_tx<=shift[1];
            bit_index<=bit_index+1'b1; remaining<=BIT_CLOCKS-1;
        end
    end
endmodule

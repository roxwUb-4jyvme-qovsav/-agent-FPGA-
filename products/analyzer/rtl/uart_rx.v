// 参数化 8N1 接收器；中心采样、双同步、帧错丢弃、背压保持与溢出脉冲。
module uart_rx #(parameter integer CLK_HZ=100000000, parameter integer BAUD=115200) (
    input wire clk, input wire reset, input wire serial_rx,
    output reg [7:0] rx_data, output reg rx_valid, input wire rx_ready,
    output reg framing_error, output reg overflow
);
    localparam integer BIT_CLOCKS=(CLK_HZ+BAUD/2)/BAUD;
    localparam [2:0] IDLE=0, START=1, DATA=2, STOP=3, WAIT_HIGH=4;
    (* ASYNC_REG="TRUE" *) reg sync1,sync2;
    reg [2:0] state, bit_index;
    reg [7:0] assembled;
    integer remaining;
    always @(posedge clk) begin
        if(reset) begin
            sync1<=1;sync2<=1;state<=IDLE;bit_index<=0;assembled<=0;remaining<=0;
            rx_data<=0;rx_valid<=0;framing_error<=0;overflow<=0;
        end else begin
            sync1<=serial_rx;sync2<=sync1;framing_error<=0;overflow<=0;
            if(rx_valid && rx_ready) rx_valid<=0;
            case(state)
                IDLE: if(!sync2) begin state<=START;remaining<=BIT_CLOCKS/2-1;end
                START: if(remaining!=0) remaining<=remaining-1;
                    else if(sync2) state<=IDLE;
                    else begin state<=DATA;bit_index<=0;remaining<=BIT_CLOCKS-1;end
                DATA: if(remaining!=0) remaining<=remaining-1;
                    else begin
                        assembled[bit_index]<=sync2;remaining<=BIT_CLOCKS-1;
                        if(bit_index==7) state<=STOP; else bit_index<=bit_index+1'b1;
                    end
                STOP: if(remaining!=0) remaining<=remaining-1;
                    else if(!sync2) begin framing_error<=1;state<=WAIT_HIGH;end
                    else begin
                        // 无流控场景只能报告溢出，禁止覆盖消费者尚未接收的字节。
                        if(rx_valid && !rx_ready) overflow<=1;
                        else begin rx_data<=assembled;rx_valid<=1;end
                        state<=IDLE;
                    end
                WAIT_HIGH: if(sync2) state<=IDLE;
                default: state<=IDLE;
            endcase
        end
    end
endmodule

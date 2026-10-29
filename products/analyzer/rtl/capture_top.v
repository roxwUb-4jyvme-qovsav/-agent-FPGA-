// 串口顶层。仅逻辑接口，无器件/管脚/电气约束，不授予烧录权限。
module capture_top #(parameter integer CHANNELS=8,parameter integer CLK_HZ=100000000,parameter integer BAUD=115200) (
    input wire clk,input wire reset,input wire [CHANNELS-1:0] async_inputs,
    input wire serial_rx,output wire serial_tx,
    output wire [2:0] state,output wire [31:0] capture_id,output wire [12:0] sample_count
);
    wire [7:0] rx_data,tx_data;
    wire rx_valid,rx_ready,tx_valid,tx_ready,framing_error,overflow;
    uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) receiver (
        .clk(clk),.reset(reset),.serial_rx(serial_rx),.rx_data(rx_data),.rx_valid(rx_valid),.rx_ready(rx_ready),.framing_error(framing_error),.overflow(overflow));
    uart_tx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) transmitter (
        .clk(clk),.reset(reset),.serial_tx(serial_tx),.tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready));
    capture_device #(.CHANNELS(CHANNELS),.CLK_HZ(CLK_HZ)) device (
        .clk(clk),.reset(reset),.async_inputs(async_inputs),.rx_data(rx_data),.rx_valid(rx_valid),.rx_ready(rx_ready),
        .rx_frame_error(framing_error|overflow),.tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready),
        .state(state),.capture_id(capture_id),.sample_count(sample_count));
endmodule

`timescale 1ns/1ps
module uart_tb;
    reg clk = 0; always #5 clk = ~clk;
    reg reset = 1;
    reg [7:0] tx_data = 0;
    reg tx_valid = 0;
    wire tx_ready, serial_tx;
    reg rx_ready = 0;
    wire [7:0] rx_data;
    wire rx_valid, framing_error, overflow;
    uart_tx #(.CLK_HZ(10000000), .BAUD(1000000)) transmitter (.*);
    uart_rx #(.CLK_HZ(10000000), .BAUD(1000000)) receiver (
        .clk(clk), .reset(reset), .serial_rx(serial_tx), .rx_data(rx_data),
        .rx_valid(rx_valid), .rx_ready(rx_ready), .framing_error(framing_error), .overflow(overflow));
    task send(input [7:0] value);
        begin
            wait(tx_ready); @(negedge clk); tx_data = value; tx_valid = 1;
            @(negedge clk); tx_valid = 0;
        end
    endtask
    task receive(input [7:0] value);
        begin
            wait(rx_valid); #1;
            if (rx_data !== value) $fatal(1, "UART byte mismatch %h != %h", rx_data, value);
            repeat (7) begin @(posedge clk); #1; if (!rx_valid || rx_data !== value) $fatal(1, "RX backpressure not stable"); end
            @(negedge clk); rx_ready = 1;
            @(negedge clk); rx_ready = 0;
        end
    endtask
    initial begin
        repeat(3) @(negedge clk); reset = 0;
        send(8'ha5); receive(8'ha5);
        send(8'h00); receive(8'h00);
        send(8'hff); receive(8'hff);
        if (framing_error || overflow) $fatal(1, "unexpected UART error");
        $display("PUBLIC_UART_PASS"); $finish;
    end
    initial begin #100000; $fatal(1, "UART timeout"); end
endmodule

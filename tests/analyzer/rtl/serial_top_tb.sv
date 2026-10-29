`timescale 1ns/1ps
module serial_top_tb;
    reg clk=0;always #5 clk=~clk;
    reg reset=1,serial_rx=1;
    wire serial_tx;
    reg [7:0] async_inputs=8'h55;
    wire [2:0] state;
    wire [31:0] capture_id;
    wire [12:0] sample_count;
    capture_top #(.CHANNELS(8),.CLK_HZ(10000000),.BAUD(1000000)) dut (.*);
    reg [7:0] expected[0:25];
    integer index,bitno;
    reg [7:0] actual;
    task send_byte(input [7:0] value);
        integer n;
        begin
            @(negedge clk);serial_rx=0;repeat(10)@(negedge clk);
            for(n=0;n<8;n=n+1)begin serial_rx=value[n];repeat(10)@(negedge clk);end
            serial_rx=1;repeat(10)@(negedge clk);
        end
    endtask
    initial begin
        expected[0]=8'ha5;expected[1]=8'h5a;expected[2]=1;expected[3]=0;expected[4]=8'h81;expected[5]=0;
        expected[6]=1;expected[7]=0;expected[8]=14;expected[9]=0;
        expected[10]=8;expected[11]=1;expected[12]=8'h80;expected[13]=8'h96;expected[14]=8'h98;expected[15]=0;
        expected[16]=0;expected[17]=16;expected[18]=0;expected[19]=4;expected[20]=3;expected[21]=0;expected[22]=0;expected[23]=0;
        // 预期 CRC 在 Python 冻结向量逻辑之外手工固定（CLK_HZ=10 MHz）。
        expected[24]=8'ha9;expected[25]=8'hba;
        repeat(3)@(negedge clk);reset=0;
        fork
            begin
                send_byte(8'ha5);send_byte(8'h5a);send_byte(1);send_byte(0);send_byte(1);send_byte(0);
                send_byte(1);send_byte(0);send_byte(0);send_byte(0);send_byte(8'hf9);send_byte(8'h45);
            end
            begin
                for(index=0;index<26;index=index+1)begin
                    @(negedge serial_tx);repeat(15)@(posedge clk);
                    for(bitno=0;bitno<8;bitno=bitno+1)begin actual[bitno]=serial_tx;repeat(10)@(posedge clk);end
                    if(!serial_tx)$fatal(1,"serial stop bit low");
                    if(actual!==expected[index])$fatal(1,"serial byte %0d: %h expected %h",index,actual,expected[index]);
                end
            end
        join
        $display("PUBLIC_SERIAL_PASS");$finish;
    end
    initial begin #1000000;$fatal(1,"serial top timeout");end
endmodule

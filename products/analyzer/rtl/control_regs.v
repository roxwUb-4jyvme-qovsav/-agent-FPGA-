// 固定 capture/1.0 命令控制器。影子配置整体验证后提交，ACK 等待核心接受。
module control_regs #(parameter integer CHANNELS=8, parameter integer CLK_HZ=100000000) (
    input wire clk,input wire reset,
    input wire request_valid,output wire request_ready,
    input wire [7:0] request_major,request_minor,request_type,request_flags,
    input wire [15:0] request_sequence,request_length,
    output wire [9:0] request_payload_index,input wire [7:0] request_payload_data,
    output wire response_valid,input wire response_ready,input wire frame_done,
    output reg [7:0] response_type,output reg [15:0] response_sequence,response_length,
    input wire [9:0] response_payload_index,output wire [7:0] response_payload_data,
    output reg cfg_valid,output reg [1:0] cfg_trigger_mode,
    output reg [4:0] cfg_trigger_channel,output reg [15:0] cfg_divider,cfg_channel_mask,
    output reg [12:0] cfg_depth,input wire cfg_accepted,
    output reg ctrl_arm,ctrl_abort,input wire arm_accepted,
    input wire [2:0] capture_state,input wire [31:0] capture_id,input wire [12:0] sample_count,
    output reg [3:0] last_error,
    output reg rd_enable,output reg [31:0] rd_capture_id,output reg [12:0] rd_offset,
    input wire rd_valid,input wire [CHANNELS-1:0] rd_data,input wire [3:0] rd_error
);
    localparam [3:0] IDLE=0,COPY=1,ACK_REQUEST=2,EXECUTE=3,WAIT_CFG=4,WAIT_ARM=5,
        WAIT_ABORT=6,READ_SAMPLES=7,CACHE=8,SEND=9,WAIT_SEND=10;
    localparam integer SAMPLE_BYTES=CHANNELS/8;
    localparam integer CAPACITY=1012/SAMPLE_BYTES;
    localparam [15:0] ALL_MASK=(CHANNELS==16)?16'hffff:16'h00ff;
    reg [3:0] stage;
    reg [7:0] major,minor,kind,flags;
    reg [15:0] seq,length;
    reg [10:0] copied;
    reg [7:0] request_bytes[0:1023],response_bytes[0:1023];
    reg cache_valid;
    reg [7:0] cache_major,cache_minor,cache_type,cache_flags,cache_response_type;
    reg [15:0] cache_sequence,cache_length,cache_response_length;
    reg [7:0] cache_request[0:1023],cache_response[0:1023];
    reg same_request;
    reg [15:0] read_count,read_index;
    reg [31:0] read_offset;
    integer i;
    wire busy=capture_state==1 || capture_state==2;
    wire [31:0] requested_id={request_bytes[3],request_bytes[2],request_bytes[1],request_bytes[0]};
    wire [31:0] requested_offset={request_bytes[7],request_bytes[6],request_bytes[5],request_bytes[4]};
    wire [15:0] requested_count={request_bytes[9],request_bytes[8]};
    wire [15:0] requested_divider={request_bytes[3],request_bytes[2]};
    wire [15:0] requested_mask={request_bytes[5],request_bytes[4]};
    wire [31:0] requested_depth={request_bytes[9],request_bytes[8],request_bytes[7],request_bytes[6]};
    wire config_ok=request_bytes[0]<=1 && request_bytes[1]<CHANNELS
        && (requested_divider==10||requested_divider==100)
        && requested_mask!=0 && (requested_mask & ~ALL_MASK)==0
        && requested_depth>=16 && requested_depth<=4096 && (requested_depth & (requested_depth-1))==0
        && (request_bytes[0]==0 || ((requested_mask>>request_bytes[1])&1)!=0);
    assign request_ready=stage==ACK_REQUEST;
    assign request_payload_index=copied[9:0];
    assign response_valid=stage==SEND;
    assign response_payload_data=response_bytes[response_payload_index];
    task put_u32(input integer offset,input [31:0] value);
        begin response_bytes[offset]<=value[7:0];response_bytes[offset+1]<=value[15:8];
            response_bytes[offset+2]<=value[23:16];response_bytes[offset+3]<=value[31:24];end
    endtask
    task error_response(input [3:0] code,input cache_result);
        begin
            response_type<=8'hff;response_length<=3;
            response_bytes[0]<=kind;response_bytes[1]<={4'b0,code};response_bytes[2]<=0;last_error<=code;
            stage<=cache_result?CACHE:SEND;
        end
    endtask
    always @* begin
        same_request=major==cache_major && minor==cache_minor && kind==cache_type && flags==cache_flags && length==cache_length;
        for(integer n=0;n<1024;n=n+1)if(n<length && request_bytes[n]!=cache_request[n])same_request=0;
    end
    always @(posedge clk) begin
        if(reset)begin
            stage<=IDLE;copied<=0;major<=0;minor<=0;kind<=0;flags<=0;seq<=0;length<=0;
            cache_valid<=0;cache_major<=0;cache_minor<=0;cache_type<=0;cache_flags<=0;
            cache_sequence<=0;cache_length<=0;cache_response_type<=0;cache_response_length<=0;
            response_type<=0;response_sequence<=0;response_length<=0;
            cfg_valid<=0;cfg_trigger_mode<=0;cfg_trigger_channel<=0;cfg_divider<=10;cfg_channel_mask<=ALL_MASK;cfg_depth<=4096;
            ctrl_arm<=0;ctrl_abort<=0;last_error<=0;
            rd_enable<=0;rd_capture_id<=0;rd_offset<=0;read_count<=0;read_index<=0;read_offset<=0;
        end else begin
            cfg_valid<=0;ctrl_arm<=0;ctrl_abort<=0;rd_enable<=0;
            case(stage)
                IDLE:if(request_valid)begin
                    major<=request_major;minor<=request_minor;kind<=request_type;flags<=request_flags;
                    seq<=request_sequence;length<=request_length;copied<=0;
                    stage<=request_length==0?ACK_REQUEST:COPY;
                end
                COPY:begin
                    request_bytes[copied]<=request_payload_data;
                    if(copied+1==length)stage<=ACK_REQUEST;else copied<=copied+1'b1;
                end
                ACK_REQUEST:begin response_sequence<=seq;stage<=EXECUTE;end
                EXECUTE:begin
                    response_type<=kind|8'h80;
                    // 精确重放先于版本和 BUSY；冲突错误不能替换原缓存。
                    if(cache_valid && seq==cache_sequence)begin
                        if(same_request)begin
                            response_type<=cache_response_type;response_length<=cache_response_length;
                            for(i=0;i<1024;i=i+1)if(i<cache_response_length)response_bytes[i]<=cache_response[i];
                            stage<=SEND;
                        end else error_response(9,0);
                    end else if(major!=1||minor!=0)error_response(2,1);
                    else if(flags!=0||kind<1||kind>6)error_response(3,1);
                    else if(((kind==2||kind==4)&&length!=10)||((kind!=2&&kind!=4)&&length!=0))error_response(1,1);
                    else case(kind)
                        1:begin
                            response_bytes[0]<=CHANNELS;response_bytes[1]<=SAMPLE_BYTES;put_u32(2,CLK_HZ);
                            response_bytes[6]<=0;response_bytes[7]<=16;response_bytes[8]<=0;response_bytes[9]<=4;
                            put_u32(10,3);response_length<=14;stage<=CACHE;
                        end
                        2:if(!config_ok)error_response(4,1);
                            else if(busy)error_response(6,1);
                            else begin
                                cfg_trigger_mode<=request_bytes[0][1:0];cfg_trigger_channel<=request_bytes[1][4:0];
                                cfg_divider<=requested_divider;cfg_channel_mask<=requested_mask;cfg_depth<=requested_depth[12:0];
                                cfg_valid<=1;stage<=WAIT_CFG;
                            end
                        3:if(busy)error_response(6,1);else begin ctrl_arm<=1;stage<=WAIT_ARM;end
                        4:begin
                            if(capture_id==0||requested_id!=capture_id||capture_state==0)error_response(5,1);
                            else if(capture_state==4)error_response(10,1);
                            else if(capture_state!=3)error_response(7,1);
                            else if(requested_count==0||requested_count>CAPACITY
                                ||({1'b0,requested_offset}+requested_count)>{20'b0,sample_count})error_response(8,1);
                            else begin
                                put_u32(0,capture_id);put_u32(4,requested_offset);
                                response_bytes[8]<=requested_count[7:0];response_bytes[9]<=requested_count[15:8];
                                response_bytes[10]<=SAMPLE_BYTES;response_bytes[11]<=0;
                                read_count<=requested_count;read_index<=0;read_offset<=requested_offset;
                                rd_capture_id<=capture_id;rd_offset<=requested_offset[12:0];rd_enable<=1;stage<=READ_SAMPLES;
                            end
                        end
                        5:begin
                            response_bytes[0]<={5'b0,capture_state};response_bytes[1]<=CHANNELS;
                            response_bytes[2]<={6'b0,cfg_trigger_mode};response_bytes[3]<=0;
                            put_u32(4,capture_id);put_u32(8,{19'b0,sample_count});
                            response_bytes[12]<={4'b0,last_error};response_bytes[13]<=0;response_bytes[14]<=0;response_bytes[15]<=0;
                            response_length<=16;stage<=CACHE;
                        end
                        6:begin ctrl_abort<=1;stage<=WAIT_ABORT;end
                    endcase
                end
                WAIT_CFG:if(cfg_accepted)begin last_error<=0;response_length<=0;stage<=CACHE;end
                WAIT_ARM:if(arm_accepted)begin put_u32(0,capture_id);response_length<=4;stage<=CACHE;end
                WAIT_ABORT:begin put_u32(0,capture_id);response_length<=4;stage<=CACHE;end
                READ_SAMPLES:if(rd_error!=0)error_response(rd_error,1);
                    else if(rd_valid)begin
                        response_bytes[12+read_index*SAMPLE_BYTES]<=rd_data[7:0];
                        if(CHANNELS==16)response_bytes[13+read_index*SAMPLE_BYTES]<=rd_data>>8;
                        if(read_index+1==read_count)begin response_length<=12+read_count*SAMPLE_BYTES;stage<=CACHE;end
                        else begin read_index<=read_index+1'b1;rd_offset<=read_offset+read_index+1'b1;rd_enable<=1;end
                    end
                CACHE:begin
                    cache_valid<=1;cache_major<=major;cache_minor<=minor;cache_type<=kind;cache_flags<=flags;
                    cache_sequence<=seq;cache_length<=length;cache_response_type<=response_type;cache_response_length<=response_length;
                    for(i=0;i<1024;i=i+1)begin
                        if(i<length)cache_request[i]<=request_bytes[i];
                        if(i<response_length)cache_response[i]<=response_bytes[i];
                    end
                    stage<=SEND;
                end
                SEND:if(response_ready)stage<=WAIT_SEND;
                WAIT_SEND:if(frame_done)stage<=IDLE;
                default:stage<=IDLE;
            endcase
        end
    end
endmodule

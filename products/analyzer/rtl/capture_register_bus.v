// 32 位、字节寻址、4 字节对齐的寄存器适配器；至多一条在途事务。
// UART 使用 control_regs 的整包影子提交；此接口供独立 MMIO 集成，不可无仲裁并联驱动核心。
module capture_register_bus #(parameter integer CHANNELS=8) (
    input wire clk,input wire reset,
    input wire req_valid,output wire req_ready,input wire req_write,
    input wire [31:0] req_address,req_wdata,
    output reg rsp_valid,input wire rsp_ready,output reg [31:0] rsp_rdata,output reg rsp_error,
    input wire [2:0] capture_state,input wire [31:0] capture_id,input wire [12:0] sample_count,input wire [3:0] error_code,
    output reg cfg_valid,output reg [1:0] cfg_trigger_mode,output reg [4:0] cfg_trigger_channel,
    output reg [15:0] cfg_divider,cfg_channel_mask,output reg [12:0] cfg_depth,input wire cfg_accepted,
    output reg ctrl_arm,ctrl_abort,input wire arm_accepted
);
    localparam [1:0] IDLE=0,WAIT_CFG=1,WAIT_ARM=2,WAIT_ABORT=3;
    localparam [15:0] ALL_MASK=(CHANNELS==16)?16'hffff:16'h00ff;
    reg [1:0] stage;
    reg [31:0] candidate_divider,candidate_mask,candidate_mode,candidate_channel,candidate_depth;
    wire busy=capture_state==1||capture_state==2;
    wire config_ok=(candidate_divider==10||candidate_divider==100)
        && candidate_mask!=0 && (candidate_mask & ~{16'b0,ALL_MASK})==0
        && candidate_mode<=1 && candidate_channel<CHANNELS
        && candidate_depth>=16 && candidate_depth<=4096 && (candidate_depth & (candidate_depth-1))==0
        && (candidate_mode==0||((candidate_mask>>candidate_channel)&1)!=0);
    assign req_ready=stage==IDLE&&!rsp_valid;
    always @* begin
        candidate_divider=cfg_divider;candidate_mask=cfg_channel_mask;
        candidate_mode=cfg_trigger_mode;candidate_channel=cfg_trigger_channel;candidate_depth=cfg_depth;
        case(req_address)
            8:candidate_divider=req_wdata;12:candidate_mask=req_wdata;16:candidate_mode=req_wdata;
            20:candidate_channel=req_wdata;24:candidate_depth=req_wdata;
        endcase
    end
    always @(posedge clk)begin
        if(reset)begin
            stage<=IDLE;rsp_valid<=0;rsp_rdata<=0;rsp_error<=0;
            cfg_valid<=0;cfg_trigger_mode<=0;cfg_trigger_channel<=0;cfg_divider<=10;cfg_channel_mask<=ALL_MASK;cfg_depth<=4096;
            ctrl_arm<=0;ctrl_abort<=0;
        end else begin
            cfg_valid<=0;ctrl_arm<=0;ctrl_abort<=0;
            if(rsp_valid&&rsp_ready)rsp_valid<=0;
            if(req_valid&&req_ready)begin
                rsp_error<=0;rsp_rdata<=0;
                if(req_address[1:0]!=0||req_address>40)begin rsp_valid<=1;rsp_error<=1;end
                else if(!req_write)begin
                    rsp_valid<=1;
                    case(req_address)
                        0:rsp_rdata<=0;
                        4:rsp_rdata<={22'b0,(error_code!=0),1'b0,5'b0,capture_state};
                        8:rsp_rdata<={16'b0,cfg_divider};12:rsp_rdata<={16'b0,cfg_channel_mask};
                        16:rsp_rdata<={30'b0,cfg_trigger_mode};20:rsp_rdata<={27'b0,cfg_trigger_channel};
                        24:rsp_rdata<={19'b0,cfg_depth};28:rsp_rdata<={19'b0,sample_count};
                        32:rsp_rdata<=capture_id;36:rsp_rdata<={28'b0,error_code};
                        40:rsp_rdata<=32'h00010000|((CHANNELS/8)<<8)|CHANNELS;
                        default:rsp_error<=1;
                    endcase
                end else if(req_address==0)begin
                    if((req_wdata & 32'hfffffffc)!=0)begin rsp_valid<=1;rsp_error<=1;end
                    else if(req_wdata[1])begin ctrl_abort<=1;stage<=WAIT_ABORT;end
                    else if(req_wdata[0])begin
                        if(busy)begin rsp_valid<=1;rsp_error<=1;end
                        else begin ctrl_arm<=1;stage<=WAIT_ARM;end
                    end else rsp_valid<=1;
                end else if(req_address==8||req_address==12||req_address==16||req_address==20||req_address==24)begin
                    if(busy||!config_ok)begin rsp_valid<=1;rsp_error<=1;end
                    else begin
                        // 字段与保留位全部合法后，完整影子配置一次交给核心。
                        cfg_divider<=candidate_divider[15:0];cfg_channel_mask<=candidate_mask[15:0];
                        cfg_trigger_mode<=candidate_mode[1:0];cfg_trigger_channel<=candidate_channel[4:0];cfg_depth<=candidate_depth[12:0];
                        cfg_valid<=1;stage<=WAIT_CFG;
                    end
                end else begin rsp_valid<=1;rsp_error<=1;end
            end else case(stage)
                WAIT_CFG:if(cfg_accepted)begin rsp_valid<=1;stage<=IDLE;end
                WAIT_ARM:if(arm_accepted)begin rsp_valid<=1;stage<=IDLE;end
                WAIT_ABORT:begin rsp_valid<=1;stage<=IDLE;end
                default:begin end
            endcase
        end
    end
endmodule

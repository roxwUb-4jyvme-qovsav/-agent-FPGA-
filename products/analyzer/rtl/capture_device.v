// 字节级设备集成：分帧 → 固定命令 → 采集核心 → 编帧；只允许一条在途命令。
module capture_device #(parameter integer CHANNELS=8,parameter integer CLK_HZ=100000000) (
    input wire clk,input wire reset,input wire [CHANNELS-1:0] async_inputs,
    input wire [7:0] rx_data,input wire rx_valid,output wire rx_ready,input wire rx_frame_error,
    output wire [7:0] tx_data,output wire tx_valid,input wire tx_ready,
    output wire [2:0] state,output wire [31:0] capture_id,output wire [12:0] sample_count
);
    wire request_valid,request_ready,response_valid,response_ready,frame_done;
    wire [7:0] request_major,request_minor,request_type,request_flags,response_type;
    wire [15:0] request_sequence,request_length,response_sequence,response_length;
    wire [9:0] request_payload_index,response_payload_index;
    wire [7:0] request_payload_data,response_payload_data;
    frame_codec #(.CLK_HZ(CLK_HZ)) decoder (
        .clk(clk),.reset(reset),.rx_data(rx_data),.rx_valid(rx_valid),.rx_ready(rx_ready),.rx_frame_error(rx_frame_error),
        .request_valid(request_valid),.request_ready(request_ready),.request_major(request_major),.request_minor(request_minor),
        .request_type(request_type),.request_flags(request_flags),.request_sequence(request_sequence),.request_length(request_length),
        .payload_index(request_payload_index),.payload_data(request_payload_data));
    frame_encoder encoder (
        .clk(clk),.reset(reset),.response_valid(response_valid),.response_ready(response_ready),.response_type(response_type),
        .response_sequence(response_sequence),.response_length(response_length),.payload_index(response_payload_index),.payload_data(response_payload_data),
        .tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready),.frame_done(frame_done));
    wire cfg_valid,cfg_accepted,ctrl_arm,ctrl_abort,arm_accepted;
    wire [1:0] cfg_trigger_mode;
    wire [4:0] cfg_trigger_channel;
    wire [15:0] cfg_divider,cfg_channel_mask;
    wire [12:0] cfg_depth;
    wire [3:0] last_error,core_error;
    wire rd_enable,rd_valid;
    wire [31:0] rd_capture_id;
    wire [12:0] rd_offset;
    wire [CHANNELS-1:0] rd_data;
    wire [3:0] rd_error;
    wire sample_tick,sample_write;
    wire [CHANNELS-1:0] sample_data;
    control_regs #(.CHANNELS(CHANNELS),.CLK_HZ(CLK_HZ)) controls (
        .clk(clk),.reset(reset),.request_valid(request_valid),.request_ready(request_ready),
        .request_major(request_major),.request_minor(request_minor),.request_type(request_type),.request_flags(request_flags),
        .request_sequence(request_sequence),.request_length(request_length),.request_payload_index(request_payload_index),.request_payload_data(request_payload_data),
        .response_valid(response_valid),.response_ready(response_ready),.frame_done(frame_done),.response_type(response_type),
        .response_sequence(response_sequence),.response_length(response_length),.response_payload_index(response_payload_index),.response_payload_data(response_payload_data),
        .cfg_valid(cfg_valid),.cfg_trigger_mode(cfg_trigger_mode),.cfg_trigger_channel(cfg_trigger_channel),.cfg_divider(cfg_divider),
        .cfg_channel_mask(cfg_channel_mask),.cfg_depth(cfg_depth),.cfg_accepted(cfg_accepted),.ctrl_arm(ctrl_arm),.ctrl_abort(ctrl_abort),.arm_accepted(arm_accepted),
        .capture_state(state),.capture_id(capture_id),.sample_count(sample_count),.last_error(last_error),
        .rd_enable(rd_enable),.rd_capture_id(rd_capture_id),.rd_offset(rd_offset),.rd_valid(rd_valid),.rd_data(rd_data),.rd_error(rd_error));
    capture_core #(.CHANNELS(CHANNELS),.CLK_HZ(CLK_HZ)) core (
        .clk(clk),.reset(reset),.async_inputs(async_inputs),.cfg_valid(cfg_valid),.cfg_trigger_mode(cfg_trigger_mode),
        .cfg_trigger_channel(cfg_trigger_channel),.cfg_divider(cfg_divider),.cfg_channel_mask(cfg_channel_mask),.cfg_depth(cfg_depth),
        .ctrl_arm(ctrl_arm),.ctrl_abort(ctrl_abort),.cfg_accepted(cfg_accepted),.arm_accepted(arm_accepted),.state(state),
        .capture_id(capture_id),.sample_count(sample_count),.error_code(core_error),.sample_tick(sample_tick),.sample_write(sample_write),.sample_data(sample_data),
        .rd_enable(rd_enable),.rd_capture_id(rd_capture_id),.rd_offset(rd_offset),.rd_valid(rd_valid),.rd_data(rd_data),.rd_error(rd_error));
endmodule

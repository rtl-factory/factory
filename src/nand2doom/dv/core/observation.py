from .rv32i_smoke_model import Observation


def read_observation(dut) -> Observation:
    return Observation(
        order=int(dut.observation_order_o.value),
        pc=int(dut.observation_pc_o.value),
        instruction=int(dut.observation_instruction_o.value),
        next_pc=int(dut.observation_next_pc_o.value),
        rd_address=int(dut.observation_rd_address_o.value),
        rd_write=int(dut.observation_rd_write_o.value),
        rd_write_data=int(dut.observation_rd_write_data_o.value),
        memory_valid=int(dut.observation_memory_valid_o.value),
        memory_write=int(dut.observation_memory_write_o.value),
        memory_address=int(dut.observation_memory_address_o.value),
        memory_read_data=int(dut.observation_memory_read_data_o.value),
        memory_write_data=int(dut.observation_memory_write_data_o.value),
        memory_read_mask=int(dut.observation_memory_read_mask_o.value),
        memory_write_mask=int(dut.observation_memory_write_mask_o.value),
        trap=int(dut.observation_trap_o.value),
        cause=int(dut.observation_cause_o.value),
        trap_value=int(dut.observation_trap_value_o.value),
    )

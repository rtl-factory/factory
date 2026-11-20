"""Synthetic, explicitly non-hardware report-gate package for mock-only tests."""
import json
from src.nand2mario.scripts.board import n2m_quartus as q

SYSTEM = 'u_clock|u_pll|auto_generated|pll1|clk[0]'


def passing_evidence(config):
    """Concrete passing rows; callers corrupt one independent obligation at a time."""
    de2 = config['top'] == 'n2m_de2_115'
    clocks = {
        'reference': dict(Type='Base', Period='20', Rise='0', Fall='10', Targets=config['reference_port'], Master='', Source=''),
        'jtag_tck': dict(Type='Base', Period='100', Rise='0', Fall='50', Targets='altera_reserved_tck', Master='', Source=''),
        SYSTEM: {'Type': 'Generated', 'Period': '40', 'Rise': '0', 'Fall': '20', 'Divide by': '2', 'Multiply by': '1',
                 'Inverted': 'false', 'Master': 'reference', 'Source': SYSTEM.rsplit('|', 1)[0] + '|inclk[0]', 'Targets': SYSTEM},
    }
    for name, port in [('sdram_clock', 'DRAM_CLK')] + ([('vga_dac_clock', 'VGA_CLK')] if de2 else []):
        clocks[name] = {'Type': 'Generated', 'Period': '40', 'Rise': '20', 'Fall': '40', 'Divide by': '1', 'Multiply by': '1',
                        'Inverted': 'true', 'Master': SYSTEM, 'Source': SYSTEM, 'Targets': port}
    timing, paths, dac = [], [], []
    for corner in config['timing_corners']:
        for clock, checks in [(SYSTEM, ['Setup', 'Hold', 'Recovery', 'Removal', 'Minimum Pulse Width']),
                              ('reference', ['Minimum Pulse Width']), ('jtag_tck', ['Setup', 'Hold', 'Minimum Pulse Width']),
                              ('sdram_clock', ['Setup', 'Hold'])] + ([('vga_dac_clock', ['Setup', 'Hold'])] if de2 else []):
            timing += [dict(corner=corner, clock=clock, check=kind, slack_ns=1.0, tns_ns=0.0) for kind in checks]
        for kind in ['setup', 'hold']:
            for direction in ['read', 'write']:
                read = direction == 'read'
                paths.append(dict(corner=corner, check=kind, direction=direction, slack_ns=2.0, report='mock-sdram.rpt',
                                  **{'From Node': 'DRAM_DQ[3]' if read else config['sdram_controller'] + 'state.phase',
                                     'To Node': config['sdram_capture'] + '[3]' if read else 'DRAM_ADDR[4]',
                                     'Launch Clock': 'sdram_clock' if read else SYSTEM,
                                     'Latch Clock': SYSTEM if read else 'sdram_clock', 'Slack': '2.000'}))
            if de2:
                dac.append(dict(corner=corner, check=kind, slack_ns=3.0, report='mock-dac.rpt',
                                **{'From Node': 'u_system|display_rgb', 'To Node': 'VGA_R[3]', 'Launch Clock': SYSTEM,
                                   'Latch Clock': 'vga_dac_clock', 'Slack': '3.000'}))
    memories = []
    for need in config['ram_requirements']:
        for i in range(need['count']):
            memories.append({'Name': need['name'] + str(i), 'Type': 'M9K', 'Mode': need['mode'], 'Clock Mode': 'Single Clock',
                             'Port A Depth': str(need['depth']), 'Port A Width': str(need['width']), 'M9Ks': str(need['m9ks_each']),
                             'Port B Depth': '', 'Port B Width': '', 'Port A RDW Mode': '', 'Mixed Width RDW Mode': ''})
    resources = {}
    for name, used, available in [('Total logic elements', 10000, 114480), ('Total registers', 9000, 114480),
                                  ('M9Ks', sum(int(m['M9Ks']) for m in memories), 432),
                                  ('Total PLLs', 1, 4), ('Embedded Multiplier 9-bit elements', 0, 532)]:
        resources[name] = dict(used=used, available=available, remaining=available-used)
    resources['ram_summary'] = memories
    checks = dict.fromkeys(q.CHECK_TIMING_CATEGORIES, '0')
    checks.update(no_clock='1', virtual_clock='1', no_output_delay='2' if de2 else '1')
    return dict(schema='n2m-fit-evidence/1', project=config['project'], device=config['device'], ignored_constraints=0,
                unconstrained=dict.fromkeys(q.UCP_CATEGORIES, 0), connectivity_warnings=[], minimum_slack_ns=1.0,
                clocks_ns={k: v['Period'] for k, v in clocks.items()}, timing=timing,
                fmax={c: dict(fmax_mhz=30.0, restricted_fmax_mhz=30.0) for c in config['timing_corners']},
                sdram_timing=dict(paths=paths, worst_margin_ns={d+'_'+k: 2.0 for d in ['read', 'write'] for k in ['setup', 'hold']}),
                dac_timing=dac, check_timing=checks, clock_relationships=clocks, resources=resources, reviewed_warnings=[])


def companion_bytes(config):
    names = config['sources'] + config['headers'] + [config['settings'], config['constraints']]
    return (json.dumps(dict(config=config, sha256=dict.fromkeys(names, 'a'*64))).encode(),
            json.dumps(passing_evidence(config)).encode())
